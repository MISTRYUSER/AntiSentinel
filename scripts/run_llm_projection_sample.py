"""Run bounded LongMemEval projection sample with the configured extractor model."""

from __future__ import annotations

import argparse, json, os
from pathlib import Path
from time import monotonic

from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from antisentinel.evaluation.projection_corpus import build_projection_corpora
from antisentinel.memory.projection import LLMMemoryProjector


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--data',type=Path,required=True); parser.add_argument('--limit',type=int,default=1); parser.add_argument('--out',type=Path,required=True); args=parser.parse_args()
    base=os.getenv('ANTISENTINEL_MEMORY_MODEL_BASE_URL') or os.getenv('ANTISENTINEL_MODEL_BASE_URL'); key=os.getenv('ANTISENTINEL_MEMORY_MODEL_API_KEY') or os.getenv('ANTISENTINEL_MODEL_API_KEY'); model=os.getenv('ANTISENTINEL_MEMORY_MODEL_NAME') or os.getenv('ANTISENTINEL_MODEL_NAME')
    if not all((base,key,model)): raise SystemExit('projection model configuration missing')
    projector=LLMMemoryProjector(base_url=base,api_key=key,model=model); cases=[]
    for case in LongMemEvalLoader(args.data):
        if not case.question_id.endswith('_abs'): cases.append(case)
        if len(cases)==args.limit: break
    started=monotonic(); rows=[]
    for case in cases:
        corpora=build_projection_corpora(case,projector=projector)
        projections=corpora['session_summ']+corpora['keyphrase_userfact']
        rows.append({'question_id':case.question_id,'sessions':len(case.sessions),'projection_count':len(projections),'failed_projection_count':sum(item.get('status')=='failed' for item in projections),'projection_kinds':sorted({item.get('projection_kind') for item in projections})})
    report={'cases':len(cases),'duration_ms':(monotonic()-started)*1000,'rows':rows,'failed_projection_count':sum(row['failed_projection_count'] for row in rows),'projection_count':sum(row['projection_count'] for row in rows)}
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False)); return 0

if __name__=='__main__': raise SystemExit(main())
