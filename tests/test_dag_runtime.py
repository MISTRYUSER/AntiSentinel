from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from antisentinel.orchestration import scheduler
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.execution.tool_executor import ToolExecutionScope


def node(sid, deps=(), tool=None):
    return {'step_id': sid, 'tool_name': tool or sid, 'arguments': {}, 'depends_on': list(deps)}


def setup_runtime(tmp_path, handlers, *, workers=2, attempts=1, writable=(), approval=()):
    assert hasattr(scheduler, 'DAGScheduler'), 'bounded DAG scheduler is missing'
    from antisentinel.orchestration.runtime import PlanRuntime
    from antisentinel.orchestration.store import SQLitePlanStore
    registry = ToolRegistry(auto_discover=False)
    for name, handler in handlers.items():
        registry.register(ToolDefinition(name, name, {'type': 'object'}, handler,
                                        read_only=name not in writable, requires_approval=name in approval))
    store = SQLitePlanStore(tmp_path / 'plans.sqlite')
    runtime = PlanRuntime(registry, store, max_workers=workers, max_attempts=attempts)
    scope = ToolExecutionScope(frozenset(handlers))
    return runtime, store, scope


def plan(*nodes):
    return {'plan_id': 'p', 'revision': 1, 'steps': list(nodes), 'constraints': [],
            'intent_ref': {'session_id': 's', 'incident_id': 'i'}}


def test_independent_nodes_overlap_and_join_waits_for_both(tmp_path):
    gate = Barrier(2)
    finished = set()
    lock = Lock()
    def root(name):
        def execute(_):
            gate.wait(2)
            with lock:
                finished.add(name)
            return {'name': name}
        return execute
    def join(_):
        assert finished == {'a', 'b'}
        return 'joined'
    runtime, store, scope = setup_runtime(tmp_path, {'a': root('a'), 'b': root('b'), 'c': join})
    report = runtime.run(plan(node('c', ('a', 'b')), node('a'), node('b')), run_id='run', scope=scope)
    assert report['status'] == 'completed'
    assert report['nodes']['c']['result'] == 'joined'
    assert store.load('run')['nodes'] == report['nodes']
    spans = runtime.telemetry.finished_spans
    assert sum(s.name == 'plan.node' for s in spans) == 3
    parent = next(s for s in spans if s.name == 'plan.run')
    assert all(s.parent.span_id == parent.context.span_id for s in spans if s.name == 'plan.node')


def test_worker_bound_and_no_barrier_between_unrelated_branches(tmp_path):
    entered, release, child = Event(), Event(), Event()
    def slow(_):
        entered.set()
        assert release.wait(3)
        return 'slow'
    def fast(_):
        assert entered.wait(2)
        return 'fast'
    def after(_):
        child.set()
        release.set()
        return 'child'
    runtime, _, scope = setup_runtime(tmp_path, {'a': slow, 'b': fast, 'c': after})
    report = runtime.run(plan(node('a'), node('b'), node('c', ('b',))), run_id='run', scope=scope)
    assert report['status'] == 'completed'
    assert child.is_set()


def test_failed_parent_blocks_descendants_but_not_independent_work(tmp_path):
    calls = []
    def fail(_):
        raise RuntimeError('unavailable')
    runtime, _, scope = setup_runtime(tmp_path, {'a': fail, 'b': lambda _: calls.append('b'), 'c': lambda _: calls.append('c')})
    report = runtime.run(plan(node('a'), node('b', ('a',)), node('c')), run_id='run', scope=scope)
    assert report['nodes']['a']['status'] == 'failed'
    assert report['nodes']['b']['status'] == 'blocked'
    assert calls == ['c']
    assert report['status'] == 'failed'


def test_readonly_retry_is_bounded_and_completed_run_is_reused_after_reopen(tmp_path):
    calls = []
    def flaky(_):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('temporary')
        return {'ok': True}
    runtime, _, scope = setup_runtime(tmp_path, {'a': flaky}, attempts=2)
    p = plan(node('a'))
    first = runtime.run(p, run_id='run', scope=scope)
    assert first['status'] == 'completed'
    assert first['nodes']['a']['attempts'] == 2
    reopened, _, scope = setup_runtime(tmp_path, {'a': flaky}, attempts=2)
    second = reopened.run(p, run_id='run', scope=scope)
    assert second['nodes'] == first['nodes']
    assert len(calls) == 2


def test_resume_preserves_successful_sibling_and_retries_only_interrupted_read(tmp_path):
    calls = []
    runtime, store, scope = setup_runtime(tmp_path, {'a': lambda _: calls.append('a'), 'b': lambda _: calls.append('b')}, attempts=2)
    p = plan(node('a'), node('b', ('a',)))
    report = runtime.run(p, run_id='run', scope=scope)
    report['status'] = 'running'
    report['nodes']['b'].update(status='running', attempts=1, result=None)
    store.save(report)
    calls.clear()
    resumed = runtime.run(p, run_id='run', scope=scope)
    assert resumed['status'] == 'completed'
    assert calls == ['b']


def test_recovery_never_replays_interrupted_write(tmp_path):
    calls = []
    runtime, store, _ = setup_runtime(tmp_path, {'a': lambda _: calls.append(1)}, writable=('a',), attempts=2)
    scope = ToolExecutionScope(frozenset({'a'}), read_only_mode=False)
    p = plan(node('a'))
    report = runtime.run(p, run_id='run', scope=scope)
    report['status'] = 'running'
    report['nodes']['a'].update(status='running', attempts=1)
    store.save(report)
    resumed = runtime.run(p, run_id='run', scope=scope)
    assert resumed['status'] == 'needs_review'
    assert resumed['nodes']['a']['status'] == 'uncertain'
    assert len(calls) == 1


def test_registry_permissions_and_plan_restrictions_are_rechecked(tmp_path):
    calls = []
    runtime, _, scope = setup_runtime(tmp_path, {'a': lambda _: calls.append(1)}, writable=('a',))
    with pytest.raises(ValueError, match='read_only'):
        runtime.run(plan({**node('a'), 'read_only': True}), run_id='run', scope=scope)
    p = plan(node('a'))
    p['constraints'] = [{'kind': 'read_only', 'value': True}]
    with pytest.raises(ValueError, match='read_only'):
        runtime.run(p, run_id='other', scope=ToolExecutionScope(frozenset({'a'}), read_only_mode=False))
    assert calls == []


def test_run_id_cannot_be_reused_with_different_definition_or_scope(tmp_path):
    runtime, _, scope = setup_runtime(tmp_path, {'a': lambda _: 1})
    p = plan(node('a'))
    runtime.run(p, run_id='run', scope=scope)
    p['steps'][0]['arguments'] = {'other': 1}
    with pytest.raises(ValueError, match='binding'):
        runtime.run(p, run_id='run', scope=scope)


def test_concurrent_run_is_rejected_by_an_independent_store(tmp_path):
    entered, release = Event(), Event()
    def execute(_):
        entered.set()
        assert release.wait(3)
        return 1
    runtime, _, scope = setup_runtime(tmp_path, {'a': execute})
    other, _, _ = setup_runtime(tmp_path, {'a': execute})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runtime.run, plan(node('a')), run_id='run', scope=scope)
        assert entered.wait(2)
        try:
            with pytest.raises(RuntimeError, match='already running'):
                other.run(plan(node('a')), run_id='run', scope=scope)
        finally:
            release.set()
        assert future.result(3)['status'] == 'completed'


def test_cancel_stops_submitting_new_nodes(tmp_path):
    cancelled = Event()
    calls = []
    def first(_):
        cancelled.set()
        return 1
    runtime, _, scope = setup_runtime(tmp_path, {'a': first, 'b': lambda _: calls.append(1)}, workers=1)
    result = runtime.run(plan(node('a'), node('b', ('a',))), run_id='run', scope=scope, cancel_requested=cancelled.is_set)
    assert result['status'] == 'cancelled'
    assert result['nodes']['b']['status'] == 'cancelled'
    assert calls == []


def test_approval_and_evidence_are_archived_without_running_gated_tool(tmp_path):
    from antisentinel.domain.evidence import Evidence
    evidence = Evidence.create(kind='test', content_ref='vault:result', content_hash='hash')
    runtime, store, _ = setup_runtime(tmp_path, {
        'a': lambda _: ToolExecutionResult(status='succeeded', result={'ok': 1}, evidence=evidence),
        'b': lambda _: pytest.fail('must not execute before approval'),
    }, approval=('b',))
    scope = ToolExecutionScope(frozenset({'a', 'b'}), allow_approval=True)
    result = runtime.run(plan(node('a'), node('b', ('a',))), run_id='run', scope=scope)
    assert result['status'] == 'waiting_approval'
    assert store.load('run')['nodes']['a']['evidences'][0]['evidence_id'] == evidence.evidence_id
    assert len(store.events('run')) >= 4


def test_recovery_rejects_same_manifest_with_different_handler_code(tmp_path):
    runtime, store, scope = setup_runtime(tmp_path, {'a': lambda _: 'old', 'b': lambda _: 'old-b'}, attempts=2)
    p = plan(node('a'), node('b', ('a',)))
    report = runtime.run(p, run_id='run', scope=scope)
    report['status'] = 'running'
    report['nodes']['b'].update(status='running', attempts=1)
    store.save(report)
    replaced, _, _ = setup_runtime(tmp_path, {'a': lambda _: 'new', 'b': lambda _: 'new-b'}, attempts=2)
    with pytest.raises(ValueError, match='binding'):
        replaced.run(p, run_id='run', scope=scope)


def test_unknown_checkpoint_schema_is_never_returned_as_completed(tmp_path):
    runtime, store, scope = setup_runtime(tmp_path, {'a': lambda _: 1})
    p = plan(node('a'))
    report = runtime.run(p, run_id='run', scope=scope)
    report['schema_version'] = 99
    store.save(report)
    with pytest.raises(ValueError, match='schema'):
        runtime.run(p, run_id='run', scope=scope)


def test_cancel_after_approval_wait_marks_entire_run_cancelled(tmp_path):
    cancelled = Event()
    runtime, _, _ = setup_runtime(tmp_path, {'a': lambda _: 1, 'b': lambda _: cancelled.set()},
                                  workers=1, approval=('a',))
    scope = ToolExecutionScope(frozenset({'a', 'b'}), allow_approval=True)
    result = runtime.run(plan(node('a'), node('b')), run_id='run', scope=scope, cancel_requested=cancelled.is_set)
    assert result['status'] == 'cancelled'
    assert result['nodes']['a']['status'] == 'cancelled'


def test_declared_tool_implementation_version_is_part_of_run_binding(tmp_path):
    from antisentinel.orchestration.runtime import PlanRuntime
    from antisentinel.orchestration.store import SQLitePlanStore
    store = SQLitePlanStore(tmp_path/'versioned.sqlite')
    def make(version):
        reg = ToolRegistry(auto_discover=False)
        reg.register(ToolDefinition('a', 'a', {'type': 'object'}, lambda _: 1, implementation_version=version))
        return PlanRuntime(reg, store)
    scope = ToolExecutionScope(frozenset({'a'}))
    make('release-1').run(plan(node('a')), run_id='run', scope=scope)
    with pytest.raises(ValueError, match='binding'):
        make('release-2').run(plan(node('a')), run_id='run', scope=scope)


def test_cancel_can_be_applied_to_a_persisted_approval_wait(tmp_path):
    runtime, _, _ = setup_runtime(tmp_path, {'a': lambda _: 1}, approval=('a',))
    scope = ToolExecutionScope(frozenset({'a'}), allow_approval=True)
    p = plan(node('a'))
    assert runtime.run(p, run_id='run', scope=scope)['status'] == 'waiting_approval'
    result = runtime.run(p, run_id='run', scope=scope, cancel_requested=lambda: True)
    assert result['status'] == 'cancelled'
