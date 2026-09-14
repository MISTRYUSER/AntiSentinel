from importlib import import_module
from importlib.util import find_spec

import pytest


def scoring():
    assert find_spec('antisentinel.evaluation.intent_effectiveness'), 'effectiveness scorer missing'
    return import_module('antisentinel.evaluation.intent_effectiveness')


def case(cid,kind,route,danger=False):
    return {'id':cid,'expected':{'intent_type':kind,'route':route,'fields':{'/relation':'new_request'},
                                'explicit_prohibitions':[],'dangerous_ambiguity':danger}}


def result(cid,kind,route):
    return {'case_id':cid,'candidate':{'intent_type':kind},'resolved':{'intent_type':kind,'route':route,'relation':'new_request','constraints':[]},
            'elapsed_seconds':1,'model_calls':1,'tokens':10}


def test_perfect_predictions_have_correct_class_and_route_metrics():
    m=scoring();kinds=['question','diagnose','execute','task_control','unknown']
    cases=[case(str(i),k,'clarify' if k=='unknown' else 'plan') for i,k in enumerate(kinds)]
    records=[result(c['id'],c['expected']['intent_type'],c['expected']['route']) for c in cases]
    report=m.score(cases,records)
    assert report['macro_f1']==1
    assert report['route_accuracy']==1
    assert report['field_exact_match']==1
    assert report['inputs']==report['valid_outputs']==5


def test_missing_and_failed_outputs_stay_in_denominators():
    m=scoring()
    cases=[case('q','question','direct_answer'),case('d','diagnose','plan')]
    report=m.score(cases,[result('q','question','direct_answer')])
    assert report['route_accuracy']==0.5
    assert report['field_exact_match']==0.5
    assert report['per_class']['diagnose']['recall']==0
    assert report['technical_failures']==1
    assert report['confusion_matrix']['diagnose']['error']==1
    assert report['effectiveness_pass'] is False


def test_false_positive_is_counted_for_predicted_class():
    m=scoring()
    cases=[case('q','question','direct_answer'),case('d','diagnose','plan')]
    report=m.score(cases,[result('q','diagnose','plan'),result('d','diagnose','plan')])
    assert report['per_class']['diagnose']['precision']==0.5
    assert report['per_class']['diagnose']['recall']==1
    assert report['per_class']['diagnose']['f1']==pytest.approx(2/3)


def test_dangerous_release_and_missing_explicit_prohibition_fail_gates():
    m=scoring();c=case('a','task_control','clarify',True)
    c['expected']['explicit_prohibitions']=[{'kind':'read_only','value':True}]
    report=m.score([c],[result('a','task_control','plan')])
    assert report['dangerous_release_count']==1
    assert report['dangerous_clarify_rate']==0
    assert report['prohibition_retention']==0
    assert report['effectiveness_pass'] is False


def test_duplicate_record_is_rejected_not_used_as_best_of_n():
    m=scoring();r=result('a','question','direct_answer')
    with pytest.raises(ValueError):m.score([case('a','question','direct_answer')],[r,r])


def test_objective_review_is_not_silently_marked_complete():
    m=scoring();c=case('a','question','direct_answer');r=result('a','question','direct_answer')
    report=m.score([c],[r])
    assert report['objective_semantic_review']=='pending'
    assert report['stage_pass'] is False


def test_inference_never_uses_gold_and_does_not_retry_misclassification():
    from copy import deepcopy
    from tests.test_intent_lifecycle import request
    m=scoring();assert hasattr(m,'recognize_case'), 'recognition evaluation driver missing'
    candidate,context=request()
    identity={'intent_id':'i','revision':1,'schema_version':'1','session_id':'s1','incident_id':'i1','message_id':'m1','created_at':'2026-09-14T00:00:00Z'}
    sample={'id':'a','context':context,'identity':identity,'expected':{'intent_type':'question'}}
    class Model:
        request_count=0
        def __init__(self):self.usage=[];self.seen=[]
        def extract(self,req,*,timeout_seconds):
            self.request_count+=1;self.seen.append(req.context.to_dict());return deepcopy(candidate)
    model=Model();r=m.recognize_case(sample,model)
    assert model.request_count==1
    assert r['candidate']['intent_type']=='diagnose'
    assert model.seen==[context]
    assert 'expected' not in model.seen[0]


def test_inference_stops_after_two_schema_repairs():
    from tests.test_intent_lifecycle import request
    m=scoring();assert hasattr(m,'recognize_case'), 'recognition evaluation driver missing'
    _,context=request()
    class Model:
        request_count=0
        def __init__(self):self.usage=[]
        def extract(self,req,*,timeout_seconds):self.request_count+=1;return {}
    model=Model();r=m.recognize_case({'id':'a','context':context,'identity':{}},model)
    assert model.request_count==3
    assert r['resolved'] is None
    assert len(r['validation_errors'])==3


def test_unmeasured_safety_gates_cannot_pass():
    m=scoring();kinds=['question','diagnose','execute','task_control','unknown']
    cases=[case(str(i),kinds[i%5],'plan') for i in range(30)]
    records=[result(c['id'],c['expected']['intent_type'],'plan') for c in cases]
    report=m.score(cases,records)
    assert report['effectiveness_pass'] is False
    assert report['gates']['prohibition_retention'] is False
    assert report['gates']['dangerous_clarification'] is False
