"""Durable intent behavior across requests, processes and concurrent writers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier

import pytest

from antisentinel.domain.intent import AuthorizedIntentContext, IntentCandidate


def modules():
    assert find_spec('antisentinel.persistence.intent_store'), 'durable intent store is missing'
    return (import_module('antisentinel.persistence.intent_store'),
            import_module('antisentinel.control.intents'),
            import_module('antisentinel.ports.intent_store'))


def request(mid='m1', message='排查订单超时', relation='new_request'):
    candidate = {'intent_type': 'diagnose', 'relation': relation, 'objective': message,
                 'entities': {}, 'constraints': [],
                 'provenance': [{'path': '/objective', 'source': 'user', 'ref': mid, 'quote': message}],
                 'missing_fields': [], 'ambiguities': [], 'control': None,
                 'reason': '用户请求诊断', 'confidence': None}
    context = {'actor_id': 'a1', 'session_id': 's1', 'incident_id': 'i1', 'context_version': 'c1',
               'message_id': mid, 'messages': {mid: message}, 'verified_fields': {},
               'required_fields': [], 'tasks': [], 'available_routes': ['plan', 'direct_answer', 'task_control'],
               'needs_evidence': False, 'previous_intent': None}
    return candidate, context


def call(service, c, ctx, revision=0, intent_id=None, clarification_id=None):
    return service.resolve(IntentCandidate.from_dict(c), AuthorizedIntentContext.from_dict(ctx),
                           expected_revision=revision, intent_id=intent_id, clarification_id=clarification_id)


def readonly_request(mid='m2'):
    candidate,context=request(mid,'只分析','correction')
    candidate['constraints']=[{'kind':'read_only','value':True}]
    candidate['provenance'].append({'path':'/constraints/0','source':'user','ref':mid,'quote':'只分析'})
    return candidate,context


def setup(tmp_path, clock=None):
    storage, services, errors = modules()
    store = storage.SQLiteIntentStore(tmp_path / 'intents.sqlite')
    service = services.IntentService(store, clock=clock or (lambda: 1000.0))
    return store, service, errors


def test_replay_returns_same_revision_and_one_outbox_after_reopen(tmp_path):
    store, service, _ = setup(tmp_path)
    c, ctx = request()
    first = call(service, c, ctx).to_dict()
    _, reopened, _ = setup(tmp_path)
    assert call(reopened, c, ctx).to_dict() == first
    assert store.counts() == {'revisions': 1, 'messages': 1, 'outbox': 1, 'questions': 0, 'events': 2}


def test_same_key_different_content_conflicts(tmp_path):
    store, service, errors = setup(tmp_path)
    c, ctx = request()
    call(service, c, ctx)
    c2, ctx2 = request(message='改配置')
    with pytest.raises(errors.IntentConflict):
        call(service, c2, ctx2)
    assert store.counts()['revisions'] == 1


def test_context_update_for_same_message_creates_new_revision(tmp_path):
    store, service, _ = setup(tmp_path)
    c, ctx = request()
    first = call(service, c, ctx).to_dict()
    ctx['context_version'] = 'c2'
    second = call(service, c, ctx, 1).to_dict()
    assert second['identity']['intent_id'] == first['identity']['intent_id']
    assert second['identity']['revision'] == 2
    assert store.pending('a1', 's1', 'i1')[0]['revision'] == 2


def test_continuation_preserves_goal_and_constraints_then_corrects_read_only(tmp_path):
    store, service, _ = setup(tmp_path)
    c, ctx = request(message='排查订单超时，不要重启')
    c['constraints'] = [{'kind': 'forbid_restart', 'value': True}]
    c['provenance'].append({'path': '/constraints/0', 'source': 'user', 'ref': 'm1', 'quote': '不要重启'})
    ctx['required_fields'] = ['/entities/commit']
    first = call(service, c, ctx).to_dict()
    iid = first['identity']['intent_id']
    c2, ctx2 = request('m2', '版本是 B', 'continuation')
    c2['entities'] = {'commit': 'B'}
    c2['provenance'].append({'path': '/entities/commit', 'source': 'user', 'ref': 'm2', 'quote': 'B'})
    ctx2['verified_fields'] = {'/entities/commit': 'B'}
    ctx2['required_fields'] = ['/entities/commit']
    second = call(service, c2, ctx2, 1, iid).to_dict()
    assert second['objective'] == '排查订单超时，不要重启'
    assert second['route'] == 'plan'
    assert second['constraints'] == c['constraints']
    c3, ctx3 = request('m3', '只分析订单超时', 'correction')
    c3['constraints'] = [{'kind': 'read_only', 'value': True}]
    c3['provenance'].append({'path': '/constraints/0', 'source': 'user', 'ref': 'm3', 'quote': '只分析'})
    ctx3['verified_fields'] = {'/entities/commit': 'B'}
    third = call(service, c3, ctx3, 2, iid).to_dict()
    assert third['identity']['revision'] == 3
    assert {x['kind'] for x in third['constraints']} == {'read_only', 'forbid_restart'}
    assert [x['revision'] for x in store.pending('a1', 's1', 'i1')] == [3]


@pytest.mark.parametrize('key', ['actor_id', 'session_id', 'incident_id'])
def test_cross_scope_read_and_update_rejected(tmp_path, key):
    store, service, errors = setup(tmp_path)
    c, ctx = request()
    first = call(service, c, ctx).to_dict()
    c2, ctx2 = request('m2', '版本是 B', 'continuation')
    ctx2[key] = 'other'
    with pytest.raises(errors.IntentAccessDenied):
        call(service, c2, ctx2, 1, first['identity']['intent_id'])
    with pytest.raises(errors.IntentAccessDenied):
        store.get(first['identity']['intent_id'], ctx2['actor_id'], ctx2['session_id'], ctx2['incident_id'])
    assert store.counts()['revisions'] == 1


def test_concurrent_corrections_have_exactly_one_winner(tmp_path):
    store, service, errors = setup(tmp_path)
    c, ctx = request()
    iid = call(service, c, ctx).to_dict()['identity']['intent_id']
    barrier = Barrier(2)
    def write(mid):
        _, independent, _ = setup(tmp_path)
        cc, cx = readonly_request(mid)
        barrier.wait()
        try:
            call(independent, cc, cx, 1, iid)
            return 'saved'
        except errors.IntentConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ['m2', 'm3']))
    assert sorted(outcomes) == ['conflict', 'saved']
    assert store.counts()['revisions'] == 2
    assert len(store.pending('a1', 's1', 'i1')) == 1


def test_three_questions_then_wait_without_reset_on_restart(tmp_path):
    store, service, _ = setup(tmp_path)
    c, ctx = request()
    ctx['required_fields'] = ['/entities/commit']
    result = call(service, c, ctx).to_dict()
    iid = result['identity']['intent_id']
    for revision in range(1, 4):
        _, service, _ = setup(tmp_path)
        c, ctx = request(f'm{revision+1}', '还不知道版本', 'continuation')
        ctx['required_fields'] = ['/entities/commit']
        result = call(service, c, ctx, revision, iid).to_dict()
    question = store.question(iid, 'a1', 's1', 'i1', now=1000)
    assert result['resolution_status'] == 'needs_clarification'
    assert question['rounds'] == 3
    assert question['state'] == 'waiting_user'
    assert store.counts()['outbox'] == 0


def test_expired_question_cannot_dispatch_a_late_answer(tmp_path):
    now = [1000.0]
    store, service, _ = setup(tmp_path, lambda: now[0])
    c, ctx = request()
    ctx['required_fields'] = ['/entities/commit']
    first = call(service, c, ctx).to_dict()
    iid = first['identity']['intent_id']
    question = store.question(iid, 'a1', 's1', 'i1', now=now[0])
    now[0] += 86400
    assert store.question(iid, 'a1', 's1', 'i1', now=now[0])['state'] == 'expired'
    c2, ctx2 = request('m2', '版本是 B', 'continuation')
    c2['entities'] = {'commit': 'B'}
    c2['provenance'].append({'path': '/entities/commit', 'source': 'user', 'ref': 'm2', 'quote': 'B'})
    ctx2['verified_fields'] = {'/entities/commit': 'B'}
    result = call(service, c2, ctx2, 1, iid, question['question_id']).to_dict()
    assert result['route'] == 'clarify'
    assert store.counts()['outbox'] == 0


def test_new_process_reads_same_hash_and_pending_question(tmp_path):
    store, service, _ = setup(tmp_path)
    c, ctx = request()
    ctx['required_fields'] = ['/entities/commit']
    value = call(service, c, ctx).to_dict()
    code = '''import json,sys
from antisentinel.persistence.intent_store import SQLiteIntentStore
s=SQLiteIntentStore(sys.argv[1])
v=s.get(sys.argv[2], 'a1','s1','i1').to_dict()
print(json.dumps({'hash':v['identity']['content_hash'],'question':s.question(sys.argv[2],'a1','s1','i1',now=1000)}))'''
    root = str(Path(__file__).resolve().parents[1] / 'src')
    import os
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path/'intents.sqlite'), value['identity']['intent_id']],
                            env={**os.environ, 'PYTHONPATH':root}, text=True, capture_output=True, check=True)
    loaded = json.loads(result.stdout)
    assert loaded['hash'] == value['identity']['content_hash']
    assert loaded['question']['state'] == 'open'


def test_missing_required_field_survives_a_partial_reply(tmp_path):
    store, service, _ = setup(tmp_path)
    c, ctx = request()
    ctx['required_fields'] = ['/entities/commit']
    first = call(service, c, ctx).to_dict()
    c2, ctx2 = request('m2', '还是超时', 'continuation')
    result = call(service, c2, ctx2, 1, first['identity']['intent_id']).to_dict()
    assert result['route'] == 'clarify'
    assert store.counts()['outbox'] == 0


def test_old_entity_is_not_current_authorization(tmp_path):
    store, service, _ = setup(tmp_path)
    c, ctx = request(message='排查 staging')
    c['entities'] = {'environment': 'staging'}
    c['provenance'].append({'path': '/entities/environment', 'source': 'user', 'ref': 'm1', 'quote': 'staging'})
    ctx['verified_fields'] = {'/entities/environment': 'staging'}
    first = call(service, c, ctx).to_dict()
    c2, ctx2 = request('m2', '继续', 'continuation')
    result = call(service, c2, ctx2, 1, first['identity']['intent_id']).to_dict()
    assert result['entities']['environment'] == 'staging'
    assert result['route'] == 'clarify'
    assert store.pending('a1', 's1', 'i1') == []


def test_expired_reply_without_question_id_also_revalidates(tmp_path):
    now = [1000.0]
    store, service, _ = setup(tmp_path, lambda: now[0])
    c, ctx = request()
    ctx['required_fields'] = ['/entities/commit']
    iid = call(service, c, ctx).to_dict()['identity']['intent_id']
    now[0] += 86400
    c2, ctx2 = request('m2', '版本是 B', 'continuation')
    c2['entities'] = {'commit': 'B'}
    c2['provenance'].append({'path': '/entities/commit', 'source': 'user', 'ref': 'm2', 'quote': 'B'})
    ctx2['verified_fields'] = {'/entities/commit': 'B'}
    assert call(service, c2, ctx2, 1, iid).to_dict()['route'] == 'clarify'


def test_merge_cannot_hide_forged_original_provenance(tmp_path):
    _, service, _ = setup(tmp_path)
    c, ctx = request()
    iid = call(service, c, ctx).to_dict()['identity']['intent_id']
    c2, ctx2 = request('m2', '版本是 B', 'continuation')
    c2['provenance'][0]['quote'] = '这句话不存在'
    from antisentinel.domain.intent import IntentValidationError
    with pytest.raises(IntentValidationError):
        call(service, c2, ctx2, 1, iid)
