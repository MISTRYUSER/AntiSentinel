from threading import Event

from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.tools.registry import ToolRegistry
from antisentinel.tools.manifest import ToolDefinition
from antisentinel.worker.runtime.engine import RuntimeEngine


def test_cancel_after_model_prevents_tool_execution():
    cancel=Event();called=[]
    model=FakeProviderModel([{'tasks':[{'task_id':'t','objective':'read','tool_calls':[{'tool_name':'read','arguments':{}}]}]}])
    complete=model.complete
    def finish(request):
        result=complete(request);cancel.set();return result
    model.complete=finish
    registry=ToolRegistry(auto_discover=False)
    registry.register(ToolDefinition('read','read',{'type':'object'},lambda args:called.append(1)))
    incident=Incident.create(title='cancel',source='test')
    session=Session.create(incident_id=incident.incident_id,participant_ids=['actor'])
    result=RuntimeEngine().run(incident,session,model,registry=registry,cancel_requested=cancel.is_set)
    assert result.status=='cancelled'
    assert session.status.value=='cancelled'
    assert called==[]
    assert any(e.type=='runtime.cancelled' for e in result.events)


def test_cancel_during_tool_preserves_completed_effect_and_skips_next():
    cancel=Event();called=[]
    def first(args):
        called.append('first');cancel.set();return {'ok':True}
    registry=ToolRegistry(auto_discover=False)
    registry.register(ToolDefinition('first','read',{'type':'object'},first))
    registry.register(ToolDefinition('second','read',{'type':'object'},lambda args:called.append('second')))
    model=FakeProviderModel([{'tasks':[{'task_id':'t','objective':'read','tool_calls':[{'tool_name':'first','arguments':{}},{'tool_name':'second','arguments':{}}]}]}])
    incident=Incident.create(title='cancel',source='test')
    session=Session.create(incident_id=incident.incident_id,participant_ids=['actor'])
    result=RuntimeEngine().run(incident,session,model,registry=registry,cancel_requested=cancel.is_set)
    assert result.status=='cancelled'
    assert called==['first']
    assert len(result.attempts)==1 and result.attempts[0].status.value=='succeeded'
