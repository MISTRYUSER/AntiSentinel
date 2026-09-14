"""Route acceptance, deduplication and fresh authorization at the dispatch boundary."""
from importlib import import_module
from importlib.util import find_spec

import pytest

from antisentinel.domain.intent import AuthorizedIntentContext
from tests.test_intent_lifecycle import request, setup, call, readonly_request


def dispatch_api():
    assert find_spec('antisentinel.control.intent_dispatch'), 'dispatcher not implemented'
    return (import_module('antisentinel.control.intent_dispatch'),
            import_module('antisentinel.evaluation.intent_receiver'))


class ContextReader:
    def __init__(self, context):
        self.context = context

    def read(self, actor, session, incident):
        return AuthorizedIntentContext.from_dict(self.context)


def rig(tmp_path, kind='diagnose'):
    store, service, errors = setup(tmp_path)
    candidate, context = request()
    candidate['intent_type'] = kind
    if kind == 'task_control':
        candidate['control'] = {'operation': 'status', 'target': None}
        context['tasks'] = [{'task_id': 't1', 'run_id': 'r1', 'state_version': 'v1'}]
    value = call(service, candidate, context).to_dict()
    module, receivers = dispatch_api()
    receiver = receivers.SQLiteIntentReceiver(tmp_path / 'receiver.sqlite')
    reader = ContextReader(context)
    dispatcher = module.IntentDispatcher(store, reader, answer=receiver, plan=receiver, control=receiver)
    return store, service, errors, value, context, reader, receiver, dispatcher


def send(dispatcher, value):
    return dispatcher.dispatch(value['identity']['intent_id'], value['identity']['revision'], 'a1', 's1', 'i1')


@pytest.mark.parametrize('kind,route', [('question', 'direct_answer'), ('diagnose', 'plan'), ('task_control', 'task_control')])
def test_routes_to_idempotent_receiver_and_persists_receipt(tmp_path, kind, route):
    store, _, _, value, _, _, receiver, dispatcher = rig(tmp_path, kind)
    first = send(dispatcher, value)
    again = send(dispatcher, value)
    assert first == again
    assert first['receipt']['route'] == route
    assert first['receipt']['status'] == 'accepted'
    assert first['receipt']['receiver_kind'] == 'contract'
    assert len(receiver.records()) == 1
    assert receiver.records()[0]['request']['constraints'] == value['constraints']
    assert store.receipt(value['identity']['intent_id'], 1, 'main', 'a1', 's1', 'i1') == first['receipt']


def test_stale_revision_is_not_accepted(tmp_path):
    _, service, errors, value, _, _, receiver, dispatcher = rig(tmp_path)
    c, ctx = readonly_request()
    call(service, c, ctx, 1, value['identity']['intent_id'])
    with pytest.raises(errors.IntentConflict):
        send(dispatcher, value)
    assert receiver.records() == []


@pytest.mark.parametrize('field', ['actor_id', 'session_id', 'incident_id'])
def test_fresh_scope_mismatch_denied(tmp_path, field):
    _, _, errors, value, ctx, _, receiver, dispatcher = rig(tmp_path, 'task_control')
    ctx[field] = 'other'
    with pytest.raises(errors.IntentAccessDenied):
        send(dispatcher, value)
    assert receiver.records() == []


def test_changed_task_state_is_not_guessed(tmp_path):
    _, _, errors, value, ctx, _, receiver, dispatcher = rig(tmp_path, 'task_control')
    ctx['tasks'][0]['state_version'] = 'v2'
    with pytest.raises(errors.IntentConflict):
        send(dispatcher, value)
    assert receiver.records() == []


def test_revoked_access_blocks_even_a_cached_receipt(tmp_path):
    _, _, errors, value, ctx, _, receiver, dispatcher = rig(tmp_path, 'task_control')
    send(dispatcher, value)
    ctx['tasks'] = []
    with pytest.raises(errors.IntentAccessDenied):
        send(dispatcher, value)
    assert len(receiver.records()) == 1


def test_receiver_rechecks_when_revision_changes_during_handoff(tmp_path):
    _, service, errors, value, _, _, receiver, dispatcher = rig(tmp_path)
    submit = receiver.submit
    def race(payload, validate_current):
        c, ctx = readonly_request()
        call(service, c, ctx, 1, value['identity']['intent_id'])
        return submit(payload, validate_current)
    receiver.submit = race
    with pytest.raises(errors.IntentConflict):
        send(dispatcher, value)
    assert receiver.records() == []


def test_ack_lost_before_local_commit_is_recovered_without_resubmission(tmp_path):
    store, _, _, value, _, _, receiver, dispatcher = rig(tmp_path)
    save = store.save_receipt
    def crash(*args, **kwargs):
        raise OSError('crash after downstream acceptance')
    store.save_receipt = crash
    with pytest.raises(OSError):
        send(dispatcher, value)
    store.save_receipt = save
    assert send(dispatcher, value)['receipt']['status'] == 'accepted'
    assert len(receiver.records()) == 1


def test_readonly_correction_waits_for_stop_completion(tmp_path):
    store, service, _, value, _, reader, receiver, dispatcher = rig(tmp_path)
    c, ctx = request('m2', '只分析', 'correction')
    c['constraints'] = [{'kind': 'read_only', 'value': True}]
    c['provenance'].append({'path': '/constraints/0', 'source': 'user', 'ref': 'm2', 'quote': '只分析'})
    value = call(service, c, ctx, 1, value['identity']['intent_id']).to_dict()
    ctx['tasks'] = [{'task_id': 't1', 'run_id': 'r1', 'state_version': 'v1'}]
    ctx['active_write_task'] = ctx['tasks'][0]
    ctx['active_write_intent_id'] = value['identity']['intent_id']
    reader.context = ctx
    first = send(dispatcher, value)
    assert first['delivery_status'] == 'waiting_for_stop'
    assert [r['request']['step'] for r in receiver.records()] == ['stop']
    receiver.complete(receiver.records()[0]['key'])
    second = send(dispatcher, value)
    assert second['receipt']['route'] == 'plan'
    assert [r['request']['step'] for r in receiver.records()] == ['stop', 'main']


def test_clarification_does_not_call_receiver(tmp_path):
    store, service, _, _, ctx, reader, receiver, dispatcher = rig(tmp_path)
    c, cx = request('m2')
    cx['required_fields'] = ['/entities/commit']
    value = call(service, c, cx).to_dict()
    reader.context = cx
    result = send(dispatcher, value)
    assert result['delivery_status'] == 'clarify'
    assert receiver.records() == []


def test_mismatched_receipt_is_not_saved(tmp_path):
    store, _, _, value, _, _, receiver, dispatcher = rig(tmp_path)
    original = receiver.submit
    def wrong(request, guard):
        receipt = original(request, guard)
        receipt['intent_ref']['revision'] = 999
        return receipt
    receiver.submit = wrong
    from antisentinel.domain.intent import IntentValidationError
    with pytest.raises(IntentValidationError):
        send(dispatcher, value)
    assert store.receipt(value['identity']['intent_id'], 1, 'main', 'a1','s1','i1') is None


def test_revision_change_after_acceptance_is_not_reported_as_current(tmp_path):
    store, service, errors, value, _, _, receiver, dispatcher = rig(tmp_path)
    original = receiver.submit
    def race(request_payload, guard):
        receipt = original(request_payload, guard)
        c, ctx = readonly_request()
        call(service, c, ctx, 1, value['identity']['intent_id'])
        return receipt
    receiver.submit = race
    with pytest.raises(errors.IntentConflict):
        send(dispatcher, value)
    assert len(receiver.records()) == 1  # Historical acceptance is preserved, never pretended undone.


def test_active_write_binding_must_belong_to_corrected_intent(tmp_path):
    store, service, errors, value, _, reader, receiver, dispatcher = rig(tmp_path)
    c, ctx = request('m2', '只分析', 'correction')
    c['constraints'] = [{'kind':'read_only','value':True}]
    c['provenance'].append({'path':'/constraints/0','source':'user','ref':'m2','quote':'只分析'})
    value = call(service,c,ctx,1,value['identity']['intent_id']).to_dict()
    ctx['tasks']=[{'task_id':'t1','run_id':'r1','state_version':'v1'}]
    ctx['active_write_task']=ctx['tasks'][0]
    ctx['active_write_intent_id']='unrelated'
    reader.context=ctx
    with pytest.raises(errors.IntentAccessDenied):
        send(dispatcher,value)
    assert receiver.records()==[]


def test_plan_handoff_event_has_plan_and_receipt_link(tmp_path):
    import sqlite3, json
    store,_,_,value,_,_,_,dispatcher=rig(tmp_path)
    receipt=send(dispatcher,value)['receipt']
    with sqlite3.connect(store.path) as db:
        fields={r[1] for r in db.execute('PRAGMA table_info(events)')}
        assert 'metadata' in fields, 'route audit metadata missing'
        metadata=json.loads(db.execute("SELECT metadata FROM events WHERE type='intent.routed'").fetchone()[0])
    assert metadata['plan_id']==receipt['downstream_id']
    assert metadata['receipt_id']==receipt['receipt_id']
    assert metadata['status']=='accepted'


def test_acceptance_guard_serializes_revision_updates(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    store,service,_,value,_,_,_,_=rig(tmp_path)
    assert hasattr(store,'revision_guard'), 'atomic acceptance guard missing'
    started=Event()
    iid=value['identity']['intent_id']
    def correct():
        c,ctx=readonly_request()
        started.set()
        return call(service,c,ctx,1,iid)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.revision_guard(iid,1,'a1','s1','i1'):
            future=pool.submit(correct)
            assert started.wait(1)
            assert not future.done()
        assert future.result(timeout=5).to_dict()['identity']['revision']==2


def test_execute_type_is_preserved_at_plan_handoff(tmp_path):
    _,_,_,value,_,_,receiver,dispatcher=rig(tmp_path,'execute')
    send(dispatcher,value)
    assert receiver.records()[0]['request']['intent_type']=='execute'
