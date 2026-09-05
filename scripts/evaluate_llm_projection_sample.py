"""Compare raw and completed LLM projections from a Stage-1 checkpoint."""

from __future__ import annotations

import argparse, json, re
from math import ceil
from pathlib import Path
from time import monotonic

from antisentinel.evaluation.longmemeval import LongMemEvalLoader


def tokens(text): return set(re.findall(r"[a-z0-9_:-]+|[\u4e00-\u9fff]", text.lower()))

def score(cases, checkpoint):
    latest={}
    for line in checkpoint.read_text(encoding='utf-8').splitlines():
        if line.strip():
            row=json.loads(line); latest[row['id']]=row
    results={name:[] for name in ('raw','llm_projection','raw_plus_llm')}
    failed=0
    for case in cases:
        expected=set(case.answer_session_ids); query=tokens(case.question); corpora={'raw':{},'llm_projection':{},'raw_plus_llm':{}}
        for session in case.sessions:
            raw=session.text; row=latest.get(f'{case.question_id}:{session.session_id}'); projections=[] if row is None else [p['content'] for p in row['projections'] if p['status']=='active']
            failed += int(row is None or any(p['status']=='failed' for p in row['projections'])) if row else 1
            corpora['raw'][session.session_id]=raw; corpora['llm_projection'][session.session_id]=' '.join(projections); corpora['raw_plus_llm'][session.session_id]=raw+' '+' '.join(projections)
        for name,docs in corpora.items():
            started=monotonic(); ranked=[sid for sid,_ in sorted(((sid,len(query & tokens(text))) for sid,text in docs.items()),key=lambda x:(-x[1],x[0]))[:5]]; hits=len(set(ranked)&expected)
            results[name].append({'r':hits/len(expected),'p':hits/5,'m':next((1/i for i,v in enumerate(ranked,1) if v in expected),0),'latency':(monotonic()-started)*1000})
    report={'cases':len(cases),'failed_session_projections':failed,'variants':{}}
    for name,rows in results.items():
        latency=sorted(row['latency'] for row in rows); report['variants'][name]={'recall_at_5':sum(r['r'] for r in rows)/len(rows),'precision_at_5':sum(r['p'] for r in rows)/len(rows),'mrr_at_5':sum(r['m'] for r in rows)/len(rows),'p95_ms':latency[max(0,ceil(len(latency)*.95)-1)]}
    return report

def main():
    p=argparse.ArgumentParser(); p.add_argument('--data',type=Path,required=True); p.add_argument('--checkpoint',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args()
    report=score([c for c in LongMemEvalLoader(a.data) if not c.question_id.endswith('_abs')],a.checkpoint); a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
