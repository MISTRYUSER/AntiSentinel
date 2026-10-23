"""Execute a frozen plan through the existing tool/policy/evidence boundary."""

from dataclasses import replace
import hashlib
import json
import marshal

from antisentinel.tracing.telemetry import Telemetry
from antisentinel.worker.execution.tool_executor import ToolExecutor

from .dag import canonical, nonempty
from .plan_validator import validate_plan
from .scheduler import DAGScheduler
from .state_machine import run_status


class PlanRuntime:
    def __init__(self, registry, store, *, max_workers=4, max_attempts=2, telemetry=None):
        self.registry = registry
        self.store = store
        self.scheduler = DAGScheduler(max_workers=max_workers, max_attempts=max_attempts)
        self.telemetry = telemetry or Telemetry(service_name='antisentinel.plan')

    @staticmethod
    def _tool_identity(tool):
        handler = tool.handler
        code = getattr(handler, '__code__', None)
        if code is None:
            code = getattr(getattr(handler, '__call__', None), '__code__', None)
        if code is None and tool.implementation_version is None:
            raise ValueError('opaque tool handler requires implementation_version')
        # Mutable closure state (counters/clients) is deliberately not an identity.
        # Tool authors must declare a release for configuration or remote API changes.
        code_hash = hashlib.sha256(marshal.dumps(code)).hexdigest() if code is not None else None
        return {'manifest': tool.manifest(), 'code_hash': code_hash}

    def run(self, plan, *, run_id, scope, cancel_requested=None):
        nonempty(run_id, 'run_id')
        # Freeze caller-owned JSON before validation or execution.
        definition = json.loads(canonical(plan))
        declared_hash = definition.pop('content_hash', None)
        plan_hash = hashlib.sha256(canonical(definition).encode()).hexdigest()
        if declared_hash is not None and declared_hash != plan_hash:
            raise ValueError('plan content hash mismatch')
        if any(c.get('kind') in {'read_only', 'forbid_restart'} and c.get('value') is True
               for c in definition.get('constraints', [])):
            scope = replace(scope, read_only_mode=True)
        graph = validate_plan(definition['steps'], self.registry, scope=scope)
        binding = hashlib.sha256(canonical({
            'plan': plan_hash, 'graph': graph.content_hash,
            'tools': [self._tool_identity(self.registry.resolve(n.tool_name)) for n in graph.nodes],
            'allowed_tools': sorted(scope.allowed_tools), 'read_only_mode': scope.read_only_mode,
            'allow_approval': scope.allow_approval,
        }).encode()).hexdigest()
        with self.store.exclusive(run_id):
            snapshot = self.store.load(run_id)
            if snapshot is not None and snapshot.get('schema_version') != 1:
                raise ValueError('unsupported plan checkpoint schema')
            if snapshot is not None and snapshot['binding'] != binding:
                raise ValueError('run binding does not match plan, scope or tool catalog')
            if snapshot is None:
                snapshot = {'schema_version': 1, 'run_id': run_id, 'plan_id': definition.get('plan_id'),
                            'binding': binding, 'plan_hash': plan_hash, 'graph_hash': graph.content_hash,
                            'status': 'running', 'nodes': {
                                n.step_id: {'status': 'pending', 'attempts': 0, 'result': None,
                                            'error': None, 'evidences': [],
                                            'read_only': self.registry.resolve(n.tool_name).read_only}
                                for n in graph.nodes}}
                self.store.save(snapshot, {'type': 'plan.created'})
            elif (snapshot['status'] in {'completed', 'cancelled', 'failed', 'needs_review'}
                  or snapshot['status'] == 'waiting_approval' and not (cancel_requested and cancel_requested())):
                return snapshot

            def persist(kind, sid):
                self.store.save(snapshot, {'type': kind, 'step_id': sid})

            for sid, record in snapshot['nodes'].items():
                if record['status'] == 'running':
                    if not record['read_only']:
                        record.update(status='uncertain', error={'code': 'interrupted_write_requires_review'})
                    elif record['attempts'] >= self.scheduler.max_attempts:
                        record.update(status='failed', error={'code': 'attempt_budget_exhausted'})
                    else:
                        record['status'] = 'pending'
                    persist('node.recovered', sid)

            executor = ToolExecutor(self.registry)

            def execute(node):
                with self.telemetry.span('plan.node', step_id=node.step_id, tool_name=node.tool_name, run_id=run_id) as span:
                    result = executor.execute(node.tool_name, node.arguments, scope=scope)
                    span.set_attribute('status', result.status)
                    evidence = ((result.evidence,) if result.evidence else ()) + result.evidences
                    return {'status': result.status, 'result': result.result, 'summary': result.result_summary,
                            'error': result.error, 'evidences': [e.to_dict() for e in evidence]}

            session_id = str(definition.get('intent_ref', {}).get('session_id', ''))
            with self.telemetry.span('plan.run', run_id=run_id, session_id=session_id) as span:
                self.scheduler.run(graph, snapshot['nodes'], execute, persist, cancel_requested=cancel_requested)
                snapshot['status'] = run_status(snapshot['nodes'])
                span.set_attribute('status', snapshot['status'])
                self.store.save(snapshot, {'type': 'plan.' + snapshot['status']})
            return snapshot
