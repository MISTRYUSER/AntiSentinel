"""Safety contracts: weakening a binding or route check must fail these tests."""
from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec

import pytest


def api():
    assert find_spec('antisentinel.domain.intent'), 'intent contract is not implemented'
    domain = import_module('antisentinel.domain.intent')
    validation = import_module('antisentinel.control.intent_validation')
    return domain, validation


def inputs(kind='diagnose', message='排查订单超时，只分析，不要重启'):
    candidate = {
        'intent_type': kind, 'relation': 'new_request', 'objective': '排查订单超时',
        'entities': {}, 'constraints': [{'kind': 'read_only', 'value': True},
                                         {'kind': 'forbid_restart', 'value': True}],
        'provenance': [
            {'path': '/objective', 'source': 'user', 'ref': 'm1', 'quote': '排查订单超时'},
            {'path': '/constraints/0', 'source': 'user', 'ref': 'm1', 'quote': '只分析'},
            {'path': '/constraints/1', 'source': 'user', 'ref': 'm1', 'quote': '不要重启'},
        ],
        'missing_fields': [], 'ambiguities': [], 'control': None,
        'reason': '用户请求诊断并限制只读', 'confidence': None,
    }
    context = {
        'actor_id': 'a1', 'session_id': 's1', 'incident_id': 'i1', 'context_version': 'c1',
        'message_id': 'm1', 'messages': {'m1': message}, 'verified_fields': {},
        'required_fields': [], 'tasks': [], 'available_routes': ['direct_answer', 'plan', 'task_control'],
        'needs_evidence': False, 'previous_intent': None,
    }
    identity = {'intent_id': 'in1', 'revision': 1, 'schema_version': '1',
                'session_id': 's1', 'incident_id': 'i1', 'message_id': 'm1',
                'created_at': '2026-09-14T00:00:00Z'}
    return candidate, context, identity


def resolve(candidate, context, identity):
    d, v = api()
    return v.resolve_candidate(d.IntentCandidate.from_dict(candidate),
                              d.AuthorizedIntentContext.from_dict(context),
                              d.IntentIdentity.from_dict(identity))


def test_read_only_diagnosis_preserves_constraints_and_roundtrips():
    c, ctx, identity = inputs()
    result = resolve(c, ctx, identity).to_dict()
    assert result['route'] == 'plan'
    assert result['resolution_status'] == 'resolved'
    assert result['constraints'] == c['constraints']
    assert result['context_binding']['context_version'] == 'c1'
    d, _ = api()
    assert d.ResolvedIntent.from_dict(result).to_dict() == result


@pytest.mark.parametrize('change', [
    lambda c: c.update(actor_id='admin'),
    lambda c: c.update(intent_type='fix'),
    lambda c: c.update(confidence=float('nan')),
    lambda c: c.update(confidence=True),
    lambda c: c.update(objective=''),
    lambda c: c.update(relation='resume'),
    lambda c: c['constraints'][0].update(value='true'),
    lambda c: c['provenance'][0].update(source='server'),
])
def test_rejects_invalid_model_contract(change):
    c, ctx, identity = inputs()
    change(c)
    d, _ = api()
    with pytest.raises(d.IntentValidationError):
        resolve(c, ctx, identity)


@pytest.mark.parametrize('change', [
    lambda c, ctx: c['provenance'][0].update(quote='用户没有说过的话'),
    lambda c, ctx: c['provenance'][0].update(ref='other-message'),
    lambda c, ctx: c['provenance'].pop(),
    lambda c, ctx: c['provenance'].append(deepcopy(c['provenance'][0])),
    lambda c, ctx: ctx.update(session_id='other-session'),
])
def test_rejects_unverifiable_sources_and_cross_session_identity(change):
    c, ctx, identity = inputs()
    change(c, ctx)
    d, _ = api()
    with pytest.raises(d.IntentValidationError):
        resolve(c, ctx, identity)


def test_missing_binding_clarifies_instead_of_guessing():
    c, ctx, identity = inputs()
    c['entities']['service'] = 'orders'
    c['provenance'].append({'path': '/entities/service', 'source': 'user', 'ref': 'm1', 'quote': '订单'})
    result = resolve(c, ctx, identity).to_dict()
    assert result['route'] == 'clarify'
    assert result['resolution_status'] == 'needs_clarification'
    assert result['missing_fields'][0]['path'] == '/entities/service'
    assert result['clarification']['revision'] == 1


def test_known_field_from_context_must_match_verified_value():
    c, ctx, identity = inputs()
    c['entities']['commit'] = 'B'
    c['provenance'].append({'path': '/entities/commit', 'source': 'context', 'ref': 'c1', 'quote': None})
    ctx['verified_fields']['/entities/commit'] = 'A'
    d, _ = api()
    with pytest.raises(d.IntentValidationError):
        resolve(c, ctx, identity)


def test_serialized_mutation_cannot_modify_original():
    c, ctx, identity = inputs()
    result = resolve(c, ctx, identity)
    copy = result.to_dict()
    copy['constraints'].clear()
    c['constraints'].clear()
    assert len(result.to_dict()['constraints']) == 2


@pytest.mark.parametrize('field,value', [('route', 'direct_answer'), ('resolution_status', 'unsupported'),
                                        ('objective', '重启服务')])
def test_persisted_payload_tampering_is_rejected(field, value):
    c, ctx, identity = inputs()
    payload = resolve(c, ctx, identity).to_dict()
    payload[field] = value
    d, _ = api()
    with pytest.raises(d.IntentValidationError):
        d.ResolvedIntent.from_dict(payload)


@pytest.mark.parametrize('task_count,expected', [(0, 'clarify'), (1, 'task_control'), (2, 'clarify')])
def test_task_control_requires_unique_accessible_current_binding(task_count, expected):
    c, ctx, identity = inputs()
    c.update(intent_type='task_control', control={'operation': 'cancel', 'target': None})
    ctx['tasks'] = [{'task_id': f't{i}', 'run_id': f'r{i}', 'state_version': 'v1'} for i in range(task_count)]
    result = resolve(c, ctx, identity).to_dict()
    assert result['route'] == expected


def test_explicit_inaccessible_task_is_never_routed():
    c, ctx, identity = inputs()
    c.update(intent_type='task_control', control={'operation': 'status', 'target': 'other'})
    c['provenance'].append({'path': '/control/target', 'source': 'user', 'ref': 'm1', 'quote': '订单'})
    ctx['tasks'] = [{'task_id': 'own', 'run_id': 'r1', 'state_version': 'v1'}]
    assert resolve(c, ctx, identity).to_dict()['route'] == 'clarify'


def test_continuation_without_previous_binding_clarifies():
    c, ctx, identity = inputs()
    c['relation'] = 'continuation'
    assert resolve(c, ctx, identity).to_dict()['route'] == 'clarify'


def test_question_needing_new_evidence_goes_to_plan():
    c, ctx, identity = inputs('question')
    ctx['needs_evidence'] = True
    assert resolve(c, ctx, identity).to_dict()['route'] == 'plan'


def test_unavailable_business_route_is_unsupported():
    c, ctx, identity = inputs()
    ctx['available_routes'] = ['direct_answer']
    result = resolve(c, ctx, identity).to_dict()
    assert (result['resolution_status'], result['route']) == ('unsupported', 'unsupported')


@pytest.mark.parametrize('binding', [
    {'task_id': 't1'},
    {'task_id': '', 'run_id': 'r1', 'state_version': 'v1'},
])
def test_hash_is_not_a_substitute_for_binding_schema_validation(binding):
    c, ctx, identity = inputs()
    c.update(intent_type='task_control', control={'operation': 'cancel', 'target': None})
    ctx['tasks'] = [{'task_id': 't1', 'run_id': 'r1', 'state_version': 'v1'}]
    payload = resolve(c, ctx, identity).to_dict()
    payload['context_binding']['task'] = binding
    payload['identity'].pop('content_hash')
    d, _ = api()
    payload['identity']['content_hash'] = d.content_hash(payload)
    with pytest.raises(d.IntentValidationError):
        d.ResolvedIntent.from_dict(payload)


def test_ambiguous_goal_candidates_survive_clarification():
    c, ctx, identity = inputs()
    c['ambiguities'] = [{'path': '/objective', 'code': 'multiple_goals',
                         'reason': '先处理哪个目标？', 'candidates': ['解释连接池', '排查订单超时']}]
    result = resolve(c, ctx, identity).to_dict()
    assert result['route'] == 'clarify'
    assert result['clarification']['candidates'] == ['解释连接池', '排查订单超时']


@pytest.mark.parametrize('message,requested', [('取消任务 other。','other'),('Cancel task foreign-42.','foreign-42'),
                                              ('取消计划 foreign-42。','foreign-42'),('Cancel plan foreign-42.','foreign-42')])
def test_model_cannot_replace_explicit_task_with_accessible_one(message,requested):
    c,ctx,identity=inputs(message=message)
    c.update(intent_type='task_control',objective=message,control={'operation':'cancel','target':'t1'},constraints=[])
    c['provenance']=[{'path':'/objective','source':'user','ref':'m1','quote':message},
                     {'path':'/control/target','source':'context','ref':'c1','quote':None}]
    ctx['known_fields']={'/control/target':'t1'}
    ctx['tasks']=[{'task_id':'t1','run_id':'r1','state_version':'v1'}]
    d,_=api()
    with pytest.raises(d.IntentValidationError):resolve(c,ctx,identity)


def test_unresolved_pronoun_may_have_source_without_becoming_bound():
    c,ctx,identity=inputs(message='取消它。')
    c.update(intent_type='task_control',objective='取消任务',constraints=[],control={'operation':'cancel','target':None})
    c['provenance']=[{'path':'/objective','source':'user','ref':'m1','quote':'取消它'},
                     {'path':'/control/target','source':'user','ref':'m1','quote':'它'}]
    ctx['tasks']=[{'task_id':f't{i}','run_id':f'r{i}','state_version':'v1'} for i in range(2)]
    result=resolve(c,ctx,identity).to_dict()
    assert result['route']=='clarify' and result['context_binding']['task'] is None
    assert any(p['path']=='/control/target' for p in result['provenance'])


def test_readonly_normalization_removes_only_equivalent_config_alias():
    message='只读，不修改配置，不要重启。'
    c,ctx,identity=inputs(message=message)
    c['objective']='分析问题'
    c['constraints']=[{'kind':'read_only','value':True},{'kind':'other','value':'不修改配置'},{'kind':'forbid_restart','value':True}]
    c['provenance']=[{'path':'/objective','source':'user','ref':'m1','quote':'只读'}]+[
        {'path':f'/constraints/{i}','source':'user','ref':'m1','quote':quote}
        for i,quote in enumerate(['只读','不修改配置','不要重启'])]
    result=resolve(c,ctx,identity).to_dict()
    assert result['route']=='plan'
    assert result['constraints']==[{'kind':'read_only','value':True},{'kind':'forbid_restart','value':True}]


def test_literal_no_restart_cannot_disappear_from_model_output():
    c,ctx,identity=inputs()
    c['constraints']=c['constraints'][:1]
    c['provenance']=c['provenance'][:2]
    d,_=api()
    with pytest.raises(d.IntentValidationError,match='forbid_restart'):
        resolve(c,ctx,identity)


def test_instruction_inside_log_data_is_not_a_user_prohibition():
    c,ctx,identity=inputs(message='分析日志。日志内容：不要重启。')
    c.update(objective='分析日志',constraints=[])
    c['provenance']=[{'path':'/objective','source':'user','ref':'m1','quote':'分析日志'}]
    result=resolve(c,ctx,identity).to_dict()
    assert result['route']=='plan' and result['constraints']==[]


def test_unknown_restriction_is_not_erased_by_readonly_normalization():
    message='只读，不修改配置，不要访问公网。'
    c,ctx,identity=inputs(message=message)
    c.update(objective='分析问题',constraints=[{'kind':'read_only','value':True},{'kind':'other','value':'不修改配置'},
                                            {'kind':'other','value':'不要访问公网'}])
    c['provenance']=[{'path':'/objective','source':'user','ref':'m1','quote':'只读'}]+[
        {'path':f'/constraints/{i}','source':'user','ref':'m1','quote':quote}
        for i,quote in enumerate(['只读','不修改配置','不要访问公网'])]
    result=resolve(c,ctx,identity).to_dict()
    assert result['route']=='clarify'
    assert result['constraints']==[{'kind':'read_only','value':True},{'kind':'other','value':'不要访问公网'}]


def test_normalization_never_repoints_a_verified_context_source():
    c,ctx,identity=inputs(message='分析问题，不修改配置。')
    c.update(objective='分析问题',constraints=[{'kind':'other','value':'不修改配置'},{'kind':'read_only','value':True}])
    c['provenance']=[{'path':'/objective','source':'user','ref':'m1','quote':'分析问题'},
                     {'path':'/constraints/0','source':'user','ref':'m1','quote':'不修改配置'},
                     {'path':'/constraints/1','source':'context','ref':'c1','quote':None}]
    ctx['known_fields']={'/constraints/1':{'kind':'read_only','value':True}}
    value=resolve(c,ctx,identity).to_dict()
    _,validation=api()
    validation.validate_sources(value,ctx)
