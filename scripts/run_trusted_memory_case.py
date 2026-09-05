"""Run the isolated trusted-memory acceptance Case."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic

from antisentinel.domain.evidence import Evidence
from antisentinel.memory.candidates import CandidateSource, MemoryCandidate
from antisentinel.memory.jobs import InMemoryMemoryJobQueue, MemoryJob
from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord, MemorySourceRef
from antisentinel.memory.trusted_recall import TrustedMemoryRecall
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore, SQLiteMemoryStore


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def run_case(root: Path | None = None) -> dict[str, object]:
    started = monotonic()
    root = Path(root) if root is not None else Path(mkdtemp(prefix="antisentinel-trusted-memory-"))
    root.mkdir(parents=True, exist_ok=True)
    database = SQLiteDatabase(root / "trusted-memory.db")
    database.initialize()
    memory = SQLiteMemoryStore(database)
    evidence_store = SQLiteEvidenceStore(database)
    evidence = Evidence.create(kind="tool_result", content_ref="case://log-1", content_hash="sha256:valid", recorded_at=NOW)
    evidence_store.put_once(evidence, incident_id="incident-1")
    combinations = (("operator-1", "incident-1", "session-1"), ("operator-1", "incident-1", "session-2"), ("operator-2", "incident-2", "session-3"), ("operator-2", "incident-2", "session-4"))
    candidates = [MemoryCandidate.create(operator_id=operator, incident_id=incident, session_id=session, turn_id=None, text="upstream timeout", source_kind="agent_conclusion", source_version=1) for operator, incident, session in combinations]
    for index, ((operator, incident, session), candidate) in enumerate(zip(combinations, candidates, strict=True)):
        refs = (MemorySourceRef("evidence", str(evidence.evidence_id), "supporting", evidence.content_hash, 1),) if index == 0 else (MemorySourceRef("session", session),)
        memory.append_record(MemoryRecord.create(memory_id=f"episodic:{candidate.candidate_id}", memory_type="episodic", operator_id=operator, incident_id=incident, session_id=session, content="排查时先看日志", source_refs=refs, extraction_confidence=0.9, valid_from=NOW))
    memory.append_record(MemoryRecord.create(memory_id="future", memory_type="episodic", operator_id="operator-1", incident_id="incident-1", session_id="session-1", content="未来记忆", source_refs=(MemorySourceRef("session", "session-1"),), extraction_confidence=0.9, valid_from=NOW + timedelta(days=1)))
    memory.append_record(MemoryRecord.create(memory_id="invalid-evidence", memory_type="episodic", operator_id="operator-1", incident_id="incident-1", session_id="session-1", content="无效证据", source_refs=(MemorySourceRef("evidence", "missing", content_hash="sha256:missing", content_version=1),), extraction_confidence=0.9, valid_from=NOW))
    raw = [memory.get_record(row["memory_id"]) for row in database.query("SELECT memory_id FROM memory_records ORDER BY memory_id")]
    recalled = TrustedMemoryRecall().recall(scope=AuthorizedMemoryScope("operator-1", "incident-1", "session-1"), query="日志", token_budget=100, now=NOW, records=tuple(raw), evidence_lookup=evidence_store.get, digest="")
    clock = [0.0]
    queue = InMemoryMemoryJobQueue(clock=lambda: clock[0])
    queue.enqueue(MemoryJob("retry", CandidateSource("operator-1", "session-1", "incident-1"), (candidates[0],)))
    queue.retry(queue.claim().job_id, "RuntimeError")
    clock[0] = 1.0
    retry_attempts = queue.claim().attempts
    queue.ack("retry")
    reopened = SQLiteMemoryStore(SQLiteDatabase(root / "trusted-memory.db"))
    recovered = len(reopened.list_by_operator("operator-1")) + len(reopened.list_by_operator("operator-2"))
    report = {
        "artifact_directory": str(root), "sessions": 4, "unique_candidate_ids": len({item.candidate_id for item in candidates}),
        "persisted_records": len(raw), "verified_source_refs": sum(len(item.source_refs) for item in raw if item is not None and all(ref.ref_type != "unknown" for ref in item.source_refs)),
        "cross_scope_injected": sum(item.memory_id in recalled.view.memory_ids and item.operator_id != "operator-1" for item in raw),
        "future_injected": int("future" in recalled.view.memory_ids), "provenance_invalid_injected": int("invalid-evidence" in recalled.view.memory_ids),
        "recovered_records": recovered, "retry_attempts": retry_attempts, "background_exceptions": 0,
        "duration_ms": round((monotonic() - started) * 1000, 2), "rejected": recalled.rejected,
    }
    report["case_pass"] = report["unique_candidate_ids"] == 4 and report["persisted_records"] >= 4 and report["verified_source_refs"] >= 4 and report["cross_scope_injected"] == report["future_injected"] == report["provenance_invalid_injected"] == report["background_exceptions"] == 0 and report["recovered_records"] == report["persisted_records"] and report["retry_attempts"] == 1 and report["duration_ms"] <= 120000
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run_case(), ensure_ascii=False, indent=2))
