from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec

from fastapi.testclient import TestClient

from antisentinel.entry.application import DiagnosisApplicationService
from tests.test_intent_lifecycle import request


class Model:
    def __init__(self): self.calls=0
    def extract(self,req,*,timeout_seconds):
        self.calls+=1
        ctx=req.context.to_dict();message=ctx['messages'][ctx['message_id']]
        candidate,_=request(ctx['message_id'],message)
        candidate['intent_type']='question' if '是什么' in message else 'task_control' if '取消' in message else 'diagnose'
        if candidate['intent_type']=='task_control':candidate['control']={'operation':'cancel','target':None}
        return candidate
    def complete_json(self,instructions,payload,**kwargs):
        if 'hypotheses' in instructions:return {'hypotheses':['连接资源不足'],'checks':[]}
        return {'answer':'连接池复用已建立的连接。','needs_evidence':False}


def rig(tmp_path):
    assert find_spec('antisentinel.entry.live_intents'), 'live integration missing'
    module=import_module('antisentinel.entry.live_intents')
    service=DiagnosisApplicationService.default_fake()
    model=Model()
    runtime=module.LiveIntentRuntime(service,root=tmp_path,model=model,api_token='test-token',actor='owner')
    service.intent_runtime=runtime
    from antisentinel.api.app import create_app
    return service,runtime,model,TestClient(create_app(service)),{'Authorization':'Bearer test-token'}


def test_authenticated_question_creates_real_answer_once(tmp_path):
    service,runtime,model,client,headers=rig(tmp_path)
    assert client.post('/api/intent/sessions',json={'title':'test'}).status_code==401
    created=client.post('/api/intent/sessions',headers=headers,json={'title':'test','actor_id':'attacker'})
    assert created.status_code==201
    sid=created.json()['session_id']
    assert service.sessions[sid].participant_ids==['owner']
    url=f'/api/intent/sessions/{sid}/messages'
    first=client.post(url,headers=headers,json={'message_id':'m1','content':'连接池是什么？'})
    assert first.status_code==200,first.text
    assert first.json()['receipt']['receiver_kind']=='real'
    assert first.json()['content']=='连接池复用已建立的连接。'
    replay=client.post(url,headers=headers,json={'message_id':'m1','content':'连接池是什么？'})
    assert replay.json()==first.json()
    assert model.calls==1


def test_plan_is_persisted_and_natural_language_cancel_updates_it(tmp_path):
    service,runtime,model,client,headers=rig(tmp_path)
    sid=client.post('/api/intent/sessions',headers=headers,json={'title':'test'}).json()['session_id']
    url=f'/api/intent/sessions/{sid}/messages'
    plan=client.post(url,headers=headers,json={'message_id':'m1','content':'分析超时原因'})
    assert plan.status_code==200,plan.text
    pid=plan.json()['receipt']['downstream_id']
    stored=client.get(f'/api/intent/sessions/{sid}/artifacts/{pid}',headers=headers)
    assert stored.json()['execution_status']=='not_submitted'
    cancelled=client.post(url,headers=headers,json={'message_id':'m2','content':'取消这个任务'})
    assert cancelled.status_code==200,cancelled.text
    assert cancelled.json()['receipt']['status']=='completed'
    stored=client.get(f'/api/intent/sessions/{sid}/artifacts/{pid}',headers=headers)
    assert stored.json()['status']=='cancelled'
    again=client.post(url,headers=headers,json={'message_id':'m2','content':'取消这个任务'})
    assert again.status_code==200 and again.json()==cancelled.json()


def test_other_session_artifact_and_legacy_messages_are_protected(tmp_path):
    service,runtime,model,client,headers=rig(tmp_path)
    sid=client.post('/api/intent/sessions',headers=headers,json={'title':'test'}).json()['session_id']
    assert client.get(f'/api/sessions/{sid}/messages').status_code==401
    result=client.post(f'/api/intent/sessions/{sid}/messages',headers=headers,json={'message_id':'m1','content':'连接池是什么？'}).json()
    other=client.post('/api/intent/sessions',headers=headers,json={'title':'other'}).json()['session_id']
    assert client.get(f"/api/intent/sessions/{other}/artifacts/{result['receipt']['downstream_id']}",headers=headers).status_code==404


def test_cannot_create_session_inside_another_actors_incident(tmp_path):
    from antisentinel.domain.session import Session
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.tools.registry import ToolRegistry
    service,runtime,model,client,headers=rig(tmp_path)
    incident=service.create_incident(title='private',summary=None,source='test')
    session=Session.create(incident_id=incident.incident_id,participant_ids=['someone-else'])
    session.complete('existing private session completed')
    incident.add_session(session.session_id);service.sessions[str(session.session_id)]=session
    service._runtime_builder=lambda:(FakeProviderModel([{'final':{'summary':'done','diagnosis':'done','confidence':1,'evidence_refs':[]}}]),ToolRegistry(auto_discover=False))
    response=client.post(f'/api/incidents/{incident.incident_id}/sessions',headers=headers,
                         json={'participant_ids':['owner'],'model_mode':'fake'})
    assert response.status_code==403
