#!/usr/bin/env python3
"""Run a deterministic LongMemEval retrieval baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from antisentinel.evaluation.longmemeval import KeywordMemoryRetriever, LongMemEvalLoader, evaluate_retrieval


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run LongMemEval retrieval evaluation")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 5, 10])
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    cases = LongMemEvalLoader(args.data)
    if args.limit is not None:
        cases = iter(list(cases)[:args.limit])
    summary = evaluate_retrieval(cases, KeywordMemoryRetriever(), top_ks=tuple(args.top_k))
    print(json.dumps({
        "total_cases": summary.total_cases, "evaluated_cases": summary.evaluated_cases,
        "skipped_abstentions": summary.skipped_abstentions, "error_count": summary.error_count,
        "metrics": summary.metrics,
    }, ensure_ascii=False, indent=2))
    return 1 if summary.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
