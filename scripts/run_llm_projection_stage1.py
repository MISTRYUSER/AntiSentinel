"""Resumable concurrent Stage-1 projection extraction for LongMemEval-S."""

from __future__ import annotations

import argparse, json, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from antisentinel.evaluation.projection_corpus import evaluation_time
from antisentinel.memory.projection import LLMMemoryProjector, ProjectionSource


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--data',type=Path,required=True); parser.add_argument('--root',type=Path,required=True); parser.add_argument('--workers',type=int,default=8); args=parser.parse_args()
    args.root.mkdir(parents=True,exist_ok=True); output=args.root/'projections.jsonl'; state=args.root/'state.json'
    base=os.getenv('ANTISENTINEL_MEMORY_MODEL_BASE_URL') or os.getenv('ANTISENTINEL_MODEL_BASE_URL'); key=os.getenv('ANTISENTINEL_MEMORY_MODEL_API_KEY') or os.getenv('ANTISENTINEL_MODEL_API_KEY'); model=os.getenv('ANTISENTINEL_MEMORY_MODEL_NAME') or os.getenv('ANTISENTINEL_MODEL_NAME')
    if not all((base,key,model)): raise SystemExit('projection model configuration missing')
    completed=set()
    if output.exists():
        latest={}
        for line in output.read_text(encoding='utf-8').splitlines():
            if line.strip():
                row=json.loads(line); latest[row['id']]=row
        completed={item_id for item_id,row in latest.items() if row['projections'] and all(item['status']=='active' for item in row['projections'])}
    jobs=[]
    for case in LongMemEvalLoader(args.data):
        if case.question_id.endswith('_abs'): continue
        now=evaluation_time(case.question_date)
        for session in case.sessions:
            item_id=f'{case.question_id}:{session.session_id}'
            if item_id not in completed: jobs.append((item_id,case.question_id,session,now))
    started=monotonic(); done=len(completed); failed=0
    def extract(job):
        item_id,qid,session,now=job
        projector=LLMMemoryProjector(base_url=base,api_key=key,model=model,timeout=120)
        source=ProjectionSource('benchmark',qid,session.session_id,now,tuple((turn.turn_id,turn.content) for turn in session.turns))
        items=projector.project(source)
        return {'id':item_id,'question_id':qid,'session_id':session.session_id,'projections':[{'projection_id':p.projection_id,'kind':p.kind,'content':p.content,'turn_ids':[r.ref_id for r in p.source_refs],'status':p.status,'content_version':p.content_version,'projection_revision':p.projection_revision,'failure_reason':p.failure_reason,'input_truncated':p.input_truncated} for p in items]}
    with output.open('a',encoding='utf-8') as handle, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(extract,job) for job in jobs]
        for future in as_completed(futures):
            row=future.result(); handle.write(json.dumps(row,ensure_ascii=False)+'\n'); handle.flush(); done+=1
            failed += int(any(p['status']=='failed' for p in row['projections']))
            if done % 25 == 0:
                state.write_text(json.dumps({'completed_sessions':done,'failed_sessions':failed,'remaining_sessions':len(jobs)-(done-len(completed)),'duration_ms':(monotonic()-started)*1000},ensure_ascii=False,indent=2),encoding='utf-8')
    state.write_text(json.dumps({'completed_sessions':done,'failed_sessions':failed,'remaining_sessions':0,'duration_ms':(monotonic()-started)*1000,'complete':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print(state.read_text(encoding='utf-8'))

if __name__=='__main__': main()
