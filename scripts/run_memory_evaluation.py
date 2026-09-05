#!/usr/bin/env python3
"""Persist and score replayable baseline/optimized memory evaluation outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from antisentinel.evaluation.harness import MemoryEvaluationHarness
from antisentinel.evaluation.models import EvaluationCase, EvaluationConfig, GroundTruthFact, ReplayOutput
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvaluationStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score paired AntiSentinel memory evaluation replays")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--prompt-revision", required=True)
    args = parser.parse_args(argv)

    rows = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [
        EvaluationCase(
            case_id=row["case_id"], incident_id=row["incident_id"], session_id=row["session_id"],
            query=row["query"], cutoff_at=row["cutoff_at"],
            facts=tuple(GroundTruthFact(**fact) for fact in row.get("facts", ())),
        )
        for row in rows
    ]
    by_id = {row["case_id"]: row for row in rows}

    def recorded(mode: str):
        def run(case: EvaluationCase, config: EvaluationConfig) -> ReplayOutput:
            return ReplayOutput(**by_id[case.case_id][mode])
        return run

    database = SQLiteDatabase(args.database); database.initialize()
    config = EvaluationConfig(
        run_id=args.run_id, model=args.model, prompt_revision=args.prompt_revision,
        provider=args.provider, parameters={"source": "recorded_paired_replay"},
    )
    summary = MemoryEvaluationHarness(recorded("baseline"), recorded("optimized"), SQLiteEvaluationStore(database)).run(cases, config)
    print(json.dumps({
        "run_id": args.run_id, "paired_success_count": summary.paired_success_count,
        "execution_error_count": summary.execution_error_count,
        "cache_hit_rate": summary.cache_hit_rate.value,
        "token_reduction": summary.token_reduction.value,
        "accuracy": summary.accuracy.value,
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
