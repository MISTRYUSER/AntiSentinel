"""Compare raw and projection corpora on LongMemEval session-recall labels."""

from __future__ import annotations

import argparse, json, re
from math import ceil
from pathlib import Path
from time import monotonic

from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from antisentinel.evaluation.projection_corpus import build_projection_corpora


def tokens(text): return set(re.findall(r"[a-z0-9_:-]+|[\u4e00-\u9fff]", text.lower()))

def evaluate(cases, *, top_k=5):
    reports = {name: [] for name in ("raw", "session_summ", "keyphrase_userfact", "raw_plus_projection")}
    for case in cases:
        if case.question_id.endswith("_abs"): continue
        for name, records in build_projection_corpora(case).items():
            started = monotonic(); query = tokens(case.question); per_session = {}
            for record in records:
                score = len(query & tokens(record["content"]))
                per_session[record["session_id"]] = max(per_session.get(record["session_id"], -1), score)
            ranked = [item[0] for item in sorted(per_session.items(), key=lambda item: (-item[1], item[0]))[:top_k]]
            expected = set(case.answer_session_ids); hits = len(set(ranked) & expected)
            reports[name].append({"recall": hits/len(expected), "precision": hits/top_k, "mrr": next((1/i for i,v in enumerate(ranked,1) if v in expected),0), "latency":(monotonic()-started)*1000})
    result = {}
    for name, rows in reports.items():
        latency=sorted(row["latency"] for row in rows)
        result[name] = {"cases":len(rows),"recall_at_5":sum(r["recall"] for r in rows)/len(rows),"precision_at_5":sum(r["precision"] for r in rows)/len(rows),"mrr_at_5":sum(r["mrr"] for r in rows)/len(rows),"p95_ms":latency[max(0,ceil(len(latency)*.95)-1)]}
    return result

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--data",type=Path,required=True); parser.add_argument("--out",type=Path,required=True); args=parser.parse_args()
    report=evaluate(LongMemEvalLoader(args.data)); args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(report,ensure_ascii=False)); return 0

if __name__=="__main__": raise SystemExit(main())
