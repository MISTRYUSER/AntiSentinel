from importlib import import_module
from importlib.util import find_spec

import pytest

from antisentinel.tools.registry import ToolRegistry
from antisentinel.tools.manifest import ToolDefinition


class JSONModel:
    def __init__(self, result): self.result=result;self.calls=0
    def complete_json(self,*args,**kwargs): self.calls+=1;return self.result


def test_plan_generator_checks_actual_catalog_and_preserves_scope():
    assert find_spec('antisentinel.control.planning')
    module=import_module('antisentinel.control.planning')
    assert hasattr(module,'PlanningService'), 'real planning service missing'
    registry=ToolRegistry(auto_discover=False)
    registry.register(ToolDefinition('health','read',{'type':'object'},lambda a:{}))
    model=JSONModel({'hypotheses':['连接不可用'],'checks':[{'purpose':'检查健康','tool_name':'health','arguments':{}}]})
    request={'intent_ref':{'intent_id':'i','revision':1,'content_hash':'h','session_id':'s','incident_id':'incident'},'objective':'排查超时',
             'entities':{'environment':'staging'},'constraints':[{'kind':'read_only','value':True}],'provenance':[]}
    plan=module.PlanningService(model,registry).generate(request)
    assert plan['intent_ref']==request['intent_ref']
    assert plan['constraints']==request['constraints']
    assert plan['steps'][0]['tool_name']=='health'
    assert plan['execution_status']=='not_submitted'
    assert plan['plan_id']


def test_readonly_plan_rejects_write_tool():
    module=import_module('antisentinel.control.planning')
    assert hasattr(module,'PlanningService'), 'real planning service missing'
    registry=ToolRegistry(auto_discover=False)
    registry.register(ToolDefinition('restart','write',{'type':'object'},lambda a:{},read_only=False))
    model=JSONModel({'hypotheses':['x'],'checks':[{'purpose':'restart','tool_name':'restart','arguments':{}}]})
    request={'intent_ref':{},'objective':'x','entities':{},'constraints':[{'kind':'read_only','value':True}],'provenance':[]}
    with pytest.raises(ValueError): module.PlanningService(model,registry).generate(request)


def test_runtime_control_does_not_claim_cancel_before_worker_returns():
    from antisentinel.entry.application import DiagnosisApplicationService
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from threading import Event
    service=DiagnosisApplicationService.default_fake()
    assert hasattr(service,'request_session_cancel'), 'runtime control entry missing'
    incident=service.create_incident(title='x',summary=None,source='test')
    session=Session.create(incident_id=incident.incident_id,participant_ids=['owner'])
    service.sessions[str(session.session_id)]=session
    service._cancel_requests[str(session.session_id)]=Event()
    result=service.request_session_cancel(str(session.session_id),'owner')
    assert result['status']=='cancel_requested'
    assert session.status.value=='active'
    with pytest.raises(PermissionError): service.request_session_cancel(str(session.session_id),'other')


def test_execute_request_is_an_execution_draft_not_a_diagnosis():
    module=import_module('antisentinel.control.planning')
    model=JSONModel({'hypotheses':[],'checks':[]})
    payload={'intent_type':'execute','intent_ref':{},'objective':'将参数改为20','entities':{},'constraints':[],'provenance':[]}
    result=module.PlanningService(model,ToolRegistry(auto_discover=False)).generate(payload)
    assert result['plan_type']=='execution'
    assert result['status']=='needs_capability'
    assert result['execution_status']=='not_submitted'
