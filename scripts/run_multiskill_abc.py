from __future__ import annotations

import json, os, sys
from pathlib import Path
from time import monotonic

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
from antisentinel.capabilities.loader import SkillLoader
from antisentinel.capabilities.package import build_local_package
from antisentinel.capabilities.runtime_state import SkillRuntimeState
from antisentinel.capabilities.tools import InMemorySkillStateStore, SkillRuntime
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

def main():
    out=Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=False); src=out/'source'; src.mkdir()
    (src/'diagnosis.md').write_text('先调用 read_health 获取实际状态。')
    (src/'runbook.md').write_text('使用工具结果给出最终中文结论。')
    (src/'plugin.json').write_text(json.dumps({'schema_version':1,'plugin_id':'multi','version':'1.0.0','tool_exports':[],'skills':[{'skill_id':'multi/diagnosis','version':'1.0.0','name':'diagnosis','description':'诊断健康状态','use_cases':['health'],'instructions_path':'diagnosis.md','required_tools':['read_health'],'references':[]},{'skill_id':'multi/runbook','version':'1.0.0','name':'runbook','description':'根据证据总结','use_cases':['health'],'instructions_path':'runbook.md','required_tools':[],'references':[]}]}))
    package=build_local_package(src,out/'releases'); rows=[]
    for group in 'ABC':
        for trial in range(1,4):
            calls=[]; reg=ToolRegistry(auto_discover=False); reg.register(ToolDefinition('read_health','read health',{'type':'object','properties':{},'required':[]},lambda _,calls=calls:(calls.append(1) or ToolExecutionResult(status='succeeded',result={'status':'healthy'},result_summary='health healthy'))))
            rt=None
            if group in 'BC':
                rt=SkillRuntime(SkillLoader(package),SkillRuntimeState(run_id=f'{group}-{trial}',release_id=package.release_id),InMemorySkillStateStore(),frozenset({'read_health'}),frozenset())
                if group=='B': rt.select('multi/diagnosis'); rt.select('multi/runbook')
            prompt='请检查健康状态后给出最终中文结论。' if group!='C' else '先加载 multi/diagnosis 和 multi/runbook，再检查健康状态并给出最终中文结论。'
            model=OpenAICompatibleModelAdapter(base_url=os.environ['ANTISENTINEL_MODEL_BASE_URL'],api_key=os.environ['ANTISENTINEL_MODEL_API_KEY'],model=os.environ['ANTISENTINEL_MODEL_NAME']); inc=Incident.create(title=prompt,source='multiskill-abc'); ses=Session.create(incident_id=inc.incident_id,participant_ids=['eval']); t=monotonic(); res=RuntimeEngine().run(inc,ses,model,registry=reg,config=RuntimeConfig(max_turns=5),skill_runtime=rt); active=res.skill_usage.get('active_skill_ids',[]) if res.skill_usage else []; ok=res.status=='completed' and len(calls)==1 and ((group=='A' and not active) or (group in 'BC' and active==['multi/diagnosis','multi/runbook']))
            row={'group':group,'trial_id':trial,'status':'passed' if ok else 'failed','runtime_status':res.status,'active_skill_ids':active,'health_calls':len(calls),'input_tokens':res.token_usage.input_tokens,'output_tokens':res.token_usage.output_tokens,'elapsed_seconds':round(monotonic()-t,3),'error':res.error}; rows.append(row); (out/'trials.jsonl').open('a').write(json.dumps(row,ensure_ascii=False)+'\n')
    groups={g:[r for r in rows if r['group']==g] for g in 'ABC'}; report={'case_pass':len(rows)==9 and all(r['status']=='passed' for r in rows),'trial_count':9,'groups':{g:{'passed':sum(r['status']=='passed' for r in v),'trials':len(v),'input_tokens':sum(r['input_tokens'] for r in v),'output_tokens':sum(r['output_tokens'] for r in v),'elapsed_seconds':round(sum(r['elapsed_seconds'] for r in v),3)} for g,v in groups.items()}}; (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps(report,ensure_ascii=False)); return 0 if report['case_pass'] else 1
if __name__=='__main__': raise SystemExit(main())
