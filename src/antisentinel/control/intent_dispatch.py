"""Durable handoff with fresh scope/version checks. Never executes arbitrary tools."""
from copy import deepcopy
from contextlib import contextmanager

from antisentinel.domain.intent import content_hash
from antisentinel.ports.intent_routes import IntentContextReader, RouteGateway, validate_receipt
from antisentinel.ports.intent_store import IntentAccessDenied, IntentConflict


class IntentDispatcher:
    def __init__(self, store, contexts: IntentContextReader, *, answer: RouteGateway | None = None,
                 plan: RouteGateway | None = None, control: RouteGateway | None = None):
        self.store = store
        self.contexts = contexts
        self.gateways = {'direct_answer': answer, 'plan': plan, 'task_control': control}

    def _fresh(self, iid, revision, actor, session, incident):
        value = self.store.get(iid, actor, session, incident).to_dict()
        if value['identity']['revision'] != revision:
            raise IntentConflict('intent revision superseded')
        ctx = self.contexts.read(actor, session, incident).to_dict()
        if (ctx['actor_id'], ctx['session_id'], ctx['incident_id']) != (actor, session, incident):
            raise IntentAccessDenied('fresh scope mismatch')
        return value, ctx

    @staticmethod
    def _authorize(value, ctx, route, task=None, *, cached=False):
        if route not in ctx['available_routes']:
            raise IntentAccessDenied('route not currently available')
        for key, val in value['entities'].items():
            if key == 'task_target':
                if not any(t['task_id'] == val for t in ctx['tasks']):
                    raise IntentAccessDenied('task target no longer accessible')
            elif ctx['verified_fields'].get(f'/entities/{key}') != val:
                raise IntentAccessDenied('target requires fresh authorization')
        if route == 'direct_answer' and ctx['needs_evidence']:
            raise IntentConflict('new evidence requires intent revision and rerouting')
        if route == 'task_control':
            if task is None:
                raise IntentConflict('control binding required')
            current = next((t for t in ctx['tasks'] if t['task_id'] == task['task_id']), None)
            if current is None:
                raise IntentAccessDenied('task no longer accessible')
            if current != task and not cached:
                raise IntentConflict('task state changed; re-resolve binding')

    def dispatch(self, intent_id, revision, actor, session, incident):
        value, ctx = self._fresh(intent_id, revision, actor, session, incident)
        route = value['route']
        if route in ('clarify', 'unsupported'):
            return {'delivery_status': route, 'intent': value, 'receipt': None}
        task = value['context_binding']['task']
        cached = self.store.receipt(intent_id, revision, 'main', actor, session, incident)
        self._authorize(value, ctx, route, task, cached=cached is not None)
        active = ctx.get('active_write_task')
        readonly = any(c['kind'] == 'read_only' for c in value['constraints'])
        if value['relation'] == 'correction' and readonly and active is not None:
            if ctx.get('active_write_intent_id') != intent_id:
                raise IntentAccessDenied('active execution belongs to a different intent')
            stop = self._deliver(value, 'task_control', 'stop', active,
                                 actor, session, incident)
            if stop['status'] != 'completed':
                return {'delivery_status': 'stop_failed' if stop['status'] == 'failed' else 'waiting_for_stop',
                        'intent': value, 'receipt': stop}
        receipt = self._deliver(value, route, 'main', task, actor, session, incident)
        return {'delivery_status': receipt['status'], 'intent': value, 'receipt': receipt}

    def _deliver(self, value, route, step, task, actor, session, incident):
        ident = value['identity']
        gateway = self.gateways[route]
        if gateway is None:
            raise IntentAccessDenied('no authorized gateway configured')
        ref = {k: ident[k] for k in ('intent_id', 'revision', 'content_hash', 'session_id', 'incident_id')}
        request = {k: deepcopy(value[k]) for k in ('intent_type', 'objective', 'entities', 'constraints', 'provenance', 'context_binding', 'control')}
        request.update(intent_ref=ref, route=route, step=step,
                       key=content_hash({'intent_ref': ref, 'step': step}))
        if step == 'stop':
            request['control'] = {'operation': 'cancel', 'target': task['task_id']}
            request['context_binding']['task'] = task

        saved = self.store.receipt(ident['intent_id'], ident['revision'], step, actor, session, incident)

        def validate_current():
            current, fresh = self._fresh(ident['intent_id'], ident['revision'], actor, session, incident)
            if current['identity']['content_hash'] != ident['content_hash']:
                raise IntentConflict('intent content changed')
            self._authorize(current, fresh, route, task, cached=saved is not None)
            if step == 'stop' and saved is None and (fresh.get('active_write_intent_id') != ident['intent_id'] or
                                   fresh.get('active_write_task') != task):
                raise IntentConflict('active execution binding changed')

        validate_current()
        @contextmanager
        def acceptance_guard():
            with self.store.revision_guard(ident['intent_id'], ident['revision'], actor, session, incident):
                validate_current()
                yield

        receipt = saved
        if receipt is None or receipt['status'] == 'accepted':
            receipt = gateway.lookup(request['key'])
            if receipt is None and saved is not None:
                raise IntentConflict('accepted downstream receipt disappeared; reconcile before retry')
            if receipt is None:
                receipt = gateway.submit(deepcopy(request), acceptance_guard)
        receipt = validate_receipt(receipt, request)
        self.store.save_receipt(receipt, actor, session, incident)
        saved = receipt
        validate_current()
        return receipt
