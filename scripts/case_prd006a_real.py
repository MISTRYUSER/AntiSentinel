#!/usr/bin/env python3
"""DeepSeek + authenticated HTTP + persisted real gateways. No business tools run."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from threading import Event
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def configuration():
    values={}
    env=ROOT/'.env.local'
    if env.exists():
        for line in env.read_text().splitlines():
            m=re.match(r'\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*)',line)
            if m:values[m[1]]=m[2].strip().strip('\"\'')
    for key in ('ANTISENTINEL_MODEL_API_KEY','ANTISENTINEL_MODEL_BASE_URL','ANTISENTINEL_MODEL_NAME'):
        if os.environ.get(key):values[key]=os.environ[key]
    return values


def worker(output):
    from fastapi.testclient import TestClient
    from antisentinel.adapters.llm.deepseek_intent import DeepSeekIntentAdapter
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.entry.application import DiagnosisApplicationService
    from antisentinel.entry.live_intents import LiveIntentRuntime
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.api.app import create_app
    started=time.monotonic();report={'case_pass':False,'provider':'deepseek','tool_handler_calls':0,'background_exceptions':0}
    cfg=configuration();api_token=secrets.token_urlsafe(32)
    model=DeepSeekIntentAdapter(base_url=cfg['ANTISENTINEL_MODEL_BASE_URL'],api_key=cfg['ANTISENTINEL_MODEL_API_KEY'],model=cfg['ANTISENTINEL_MODEL_NAME'])
    application=DiagnosisApplicationService.default_fake()
    runtime=LiveIntentRuntime(application,root=output/'state',model=model,api_token=api_token,actor='case-operator')
    application.intent_runtime=runtime
    client=TestClient(create_app(application));headers={'Authorization':'Bearer '+api_token}
    requests=[];responses=[]
    release=Event()
    try:
        assert client.post('/api/intent/sessions',json={'title':'probe'}).status_code==401
        sid=client.post('/api/intent/sessions',headers=headers,json={'title':'DeepSeek意图接入Case'}).json()['session_id']
        url=f'/api/intent/sessions/{sid}/messages'
        for index,message in enumerate(['连接池是什么？','分析请求超时的可能原因，只读，不要重启。','查询刚才计划的状态。','取消刚才的计划。']):
            payload={'message_id':f'live-{index}','content':message};requests.append(payload)
            response=client.post(url,headers=headers,json=payload)
            assert response.status_code==200,f'HTTP {response.status_code}: {response.text}'
            value=response.json();responses.append(value)
            assert value['receipt'] is not None and value['receipt']['receiver_kind']=='real'
            assert value['receipt']['route']==['direct_answer','plan','task_control','task_control'][index]
        assert responses[0]['content']
        plan_id=responses[1]['receipt']['downstream_id']
        plan=client.get(f'/api/intent/sessions/{sid}/artifacts/{plan_id}',headers=headers).json()
        assert plan['status']=='cancelled' and plan['execution_status']=='not_submitted'
        assert {c['kind'] for c in plan['definition']['constraints']}=={'read_only','forbid_restart'}
        calls_before=len(model.usage)
        assert client.post(url,headers=headers,json=requests[-1]).json()==responses[-1]
        assert len(model.usage)==calls_before
        # A live runtime thread is stopped through its HTTP control endpoint.
        entered=Event()
        class BlockingModel:
            def complete(self,request):
                entered.set()
                assert release.wait(10),'case release timed out'
                return FakeProviderModel([{'final':{'summary':'done','diagnosis':'done','confidence':1,'evidence_refs':[]}}]).complete(request)
        application._runtime_builder=lambda:(BlockingModel(),ToolRegistry(auto_discover=False))
        incident=client.post('/api/incidents',headers=headers,json={'title':'cancel actual worker','source':'case'}).json()
        running=client.post(f"/api/incidents/{incident['incident_id']}/sessions",headers=headers,
                            json={'participant_ids':['ignored-client-actor'],'model_mode':'fake'}).json()['session_id']
        assert entered.wait(2)
        cancel=client.post(f'/api/sessions/{running}/cancel',headers=headers)
        assert cancel.status_code==202 and cancel.json()['status']=='cancel_requested'
        release.set()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            status=client.get(f'/api/sessions/{running}/control',headers=headers).json()
            if status['status']=='cancelled':break
            time.sleep(.01)
        assert status['status']=='cancelled'
        business_completed=time.time()
        (output/'responses.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2)+'\n')
        persisted=time.time()
        # Reload application/session and gateway stores into new instances.
        reloaded=DiagnosisApplicationService.default_fake()
        restored=LiveIntentRuntime(reloaded,root=output/'state',model=model,api_token=api_token,actor='case-operator')
        assert restored.routes.artifact('case-operator',sid,plan_id)==plan
        assert reloaded.session_control_status(running,'case-operator')['status']=='cancelled'
        for result in responses:
            identity=result['intent']['identity']
            assert restored.store.get(identity['intent_id'],'case-operator',sid,identity['incident_id']).to_dict()==result['intent']
        with runtime.routes.db() as db:
            deliveries=db.execute('SELECT count(*) FROM deliveries').fetchone()[0]
        assert deliveries==4
        report.update(case_pass=True,inputs=4,outputs=4,replayed_requests=1,deliveries=deliveries,
                      restored_intents=4,restored_plans=1,restored_cancelled_runs=1,
                      real_model_calls=model.request_count,token_usage=model.usage,
                      runtime_cancel_requests=1,total_seconds=time.monotonic()-started,
                      business_completed_at=business_completed,persistence_completed_at=persisted,
                      persistence_lag_seconds=persisted-business_completed,
                      note='Real DeepSeek routing/answer/plan; runtime cancellation uses a controlled blocking model, not business tools.')
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}',total_seconds=time.monotonic()-started,
                      real_model_calls=model.request_count)
        (output/'responses.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2)+'\n')
    finally:release.set()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['case_pass'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker:return worker(args.output)
    args.output.mkdir(parents=True,exist_ok=False)
    # Isolate API module-level initialization from any live deployment configuration.
    env={**os.environ,'ANTISENTINEL_INTENTS_ENABLED':'0','ANTISENTINEL_MODEL_MODE':'fake',
         'ANTISENTINEL_STORAGE_ROOT':'','ANTISENTINEL_REDIS_URL':''}
    try:
        result=subprocess.run([sys.executable,__file__,'--worker','--output',str(args.output)],
                              env=env,capture_output=True,text=True,timeout=120)
        print(result.stdout)
        if result.returncode and not (args.output/'report.json').exists():
            (args.output/'report.json').write_text(json.dumps({'case_pass':False,'error':'worker_failed'})+'\n')
        return result.returncode
    except subprocess.TimeoutExpired:
        (args.output/'report.json').write_text(json.dumps({'case_pass':False,'error':'case_timeout','total_seconds':120})+'\n')
        print('Case exceeded 120 seconds; isolated worker terminated.')
        return 1


if __name__=='__main__':raise SystemExit(main())
