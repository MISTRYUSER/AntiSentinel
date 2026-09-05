"""Fixed LongMemEval-S lexical baseline for hybrid retrieval comparisons."""

from __future__ import annotations

import argparse
import json
from math import ceil
from pathlib import Path
import re
from time import monotonic
from tempfile import TemporaryDirectory
from datetime import datetime, timezone

from antisentinel.evaluation.longmemeval import KeywordMemoryRetriever, LongMemEvalLoader
from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord, MemorySourceRef
from antisentinel.memory.retrieval import HybridMemoryRetriever
from antisentinel.memory.trusted_recall import TrustedMemoryRecall
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteMemoryCandidateStore, SQLiteMemoryStore


def evaluation_time(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    iso = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})(?:\s+\([A-Za-z]+\))?\s+(\d{2}):(\d{2})", value)
    if iso is None:
        raise ValueError(f"unsupported evaluation date: {value}")
    return datetime(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), int(iso.group(4)), int(iso.group(5)), tzinfo=timezone.utc)


def evaluate(cases, *, top_k: int = 5, top_ks: tuple[int, ...] = (1, 5, 10)) -> dict[str, object]:
    retriever = KeywordMemoryRetriever()
    rows, latencies = [], []
    skipped = errors = 0
    for case in cases:
        if case.question_id.endswith("_abs"):
            skipped += 1
            continue
        started = monotonic()
        try:
            result = retriever.retrieve(case, top_k=max(top_ks))
        except Exception:
            errors += 1
            continue
        latency = (monotonic() - started) * 1000
        latencies.append(latency)
        expected = set(case.answer_session_ids)
        row = {"question_id": case.question_id, "latency_ms": latency, "session_ids": result.session_ids}
        for k in top_ks:
            selected = result.session_ids[:k]
            hits = len(set(selected) & expected)
            row[f"recall_at_{k}"] = hits / len(expected) if expected else 0.0
            row[f"precision_at_{k}"] = hits / k
            row[f"mrr_at_{k}"] = next((1 / index for index, value in enumerate(selected, 1) if value in expected), 0.0)
        rows.append(row)
    metric_keys = tuple(metric for k in top_ks for metric in (f"recall_at_{k}", f"precision_at_{k}", f"mrr_at_{k}"))
    metrics = {key: (sum(row[key] for row in rows) / len(rows) if rows else 0.0) for key in metric_keys}
    ordered = sorted(latencies)
    p95 = ordered[max(0, ceil(len(ordered) * .95) - 1)] if ordered else 0.0
    return {"total_cases": len(rows) + skipped + errors, "evaluated_cases": len(rows), "skipped_abstentions": skipped, "error_count": errors, "metrics": metrics, "latency_ms": {"p50": ordered[(len(ordered) - 1) // 2] if ordered else 0.0, "p95": p95}, "channel_status": {"lexical": "active", "identifier": "bypass", "vector": "bypass"}, "rows": rows}


def evaluate_hybrid(cases, *, database_path: Path, top_k: int = 5, top_ks: tuple[int, ...] = (1, 5, 10)) -> dict[str, object]:
    trusted = TrustedMemoryRecall()
    rows, latencies = [], []
    skipped = errors = 0
    for case in cases:
        if case.question_id.endswith("_abs"):
            skipped += 1; continue
        now = evaluation_time(case.question_date); scope = AuthorizedMemoryScope("benchmark", case.question_id, case.sessions[-1].session_id)
        with TemporaryDirectory(prefix="antisentinel-hybrid-eval-", dir=database_path.parent) as temporary:
            database = SQLiteDatabase(Path(temporary) / "case.db"); database.initialize()
            memory = SQLiteMemoryStore(database); retriever = HybridMemoryRetriever(SQLiteMemoryCandidateStore(database))
            records, session_by_memory = [], {}
            for index, session in enumerate(case.sessions):
                record = MemoryRecord.create(memory_id=f"hybrid:{case.question_id}:{index}", memory_type="session_digest", operator_id="benchmark", incident_id=case.question_id, session_id=session.session_id, content=session.text, source_refs=(MemorySourceRef("session", session.session_id),), extraction_confidence=.9, valid_from=now)
                memory.append_record(record); records.append(record); session_by_memory[record.memory_id] = session.session_id
            started = monotonic()
            try:
                candidates = retriever.retrieve(case.question, scope, limit=max(top_ks)); by_id = {record.memory_id: record for record in records}
                ordered_records = tuple(by_id[item.memory_id] for item in candidates if item.memory_id in by_id)
                result = trusted.recall(scope=scope, query=case.question, token_budget=100000, now=now, records=ordered_records, evidence_lookup=None, digest="")
                sessions = tuple(session_by_memory[memory_id] for memory_id in result.view.memory_ids)
            except Exception:
                errors += 1; continue
        latency = (monotonic() - started) * 1000; latencies.append(latency)
        expected = set(case.answer_session_ids)
        row = {"question_id": case.question_id, "latency_ms": latency, "session_ids": sessions, "channels": [item.channels for item in candidates], "rejected": result.rejected}
        for k in top_ks:
            selected = sessions[:k]; hits = len(set(selected) & expected)
            row[f"recall_at_{k}"] = hits / len(expected) if expected else 0.0
            row[f"precision_at_{k}"] = hits / k
            row[f"mrr_at_{k}"] = next((1 / position for position, session_id in enumerate(selected, 1) if session_id in expected), 0.0)
        rows.append(row)
    metrics = {metric: (sum(row[metric] for row in rows) / len(rows) if rows else 0.0) for k in top_ks for metric in (f"recall_at_{k}", f"precision_at_{k}", f"mrr_at_{k}")}
    ordered = sorted(latencies); p95 = ordered[max(0, ceil(len(ordered) * .95) - 1)] if ordered else 0.0
    return {"total_cases": len(rows) + skipped + errors, "evaluated_cases": len(rows), "skipped_abstentions": skipped, "error_count": errors, "metrics": metrics, "latency_ms": {"p50": ordered[(len(ordered)-1)//2] if ordered else 0.0, "p95": p95}, "channel_status": {"lexical": "active", "identifier": "active", "vector": "bypass"}, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "hybrid"), default="baseline")
    args = parser.parse_args()
    report = evaluate_hybrid(LongMemEvalLoader(args.data), database_path=args.out.with_suffix(".sqlite")) if args.mode == "hybrid" else evaluate(LongMemEvalLoader(args.data))
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False))
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
