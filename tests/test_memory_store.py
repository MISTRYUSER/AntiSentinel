from dataclasses import replace
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import DomainError
from antisentinel.memory.models import MemoryRecord, MemorySourceRef
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteMemoryStore


def test_file_memory_store_appends_rollout_and_rebuilds_by_operator(tmp_path):
    from antisentinel.persistence import memory_store
    store = memory_store.FileMemoryStore(tmp_path)
    record = {
        "memory_id": "memory-1",
        "memory_type": "preference",
        "operator_id": "operator-1",
        "subject": "diagnosis_order",
        "predicate": "prefers",
        "object": "logs_before_metrics",
        "source_ids": ["session-1"],
        "confidence": 0.9,
        "content_version": 1,
    }

    store.append(record)
    store.append(record)

    assert store.list_by_operator("operator-1") == [record]
    assert store.list_by_operator("operator-2") == []


def test_cache_aside_rebuilds_after_cache_clear(tmp_path):
    from antisentinel.persistence import memory_store
    durable = memory_store.FileMemoryStore(tmp_path)
    cache = memory_store.InMemoryMemoryCache()
    record = {"memory_id": "memory-2", "operator_id": "operator-1", "memory_type": "rollout", "content_version": 1}
    durable.append(record)
    cache.set("operator:operator-1:preference_graph", [record], ttl_seconds=60, version=1)
    assert cache.get("operator:operator-1:preference_graph").value == [record]
    cache.delete("operator:operator-1:preference_graph")
    rebuilt = durable.list_by_operator("operator-1")
    cache.set("operator:operator-1:preference_graph", rebuilt, ttl_seconds=60, version=1)
    assert cache.get("operator:operator-1:preference_graph").value == [record]


def test_rollout_memory_persists_structured_summary_and_rebuilds_cache(tmp_path):
    from antisentinel.memory.rollout import RolloutMemory
    from antisentinel.persistence.memory_store import FileMemoryStore, InMemoryMemoryCache

    durable = FileMemoryStore(tmp_path)
    cache = InMemoryMemoryCache()
    rollout = RolloutMemory(durable, cache)
    fake_result = type("Result", (), {
        "session_id": "session-1", "incident_id": "incident-1", "status": "completed",
        "final": type("Final", (), {"summary": "fixed", "diagnosis": "timeout"})(),
        "evidence_refs": (),
    })()

    record = rollout.record(fake_result)
    cache.delete("rollout:session-1")

    assert rollout.get("session-1") == record


def test_rollout_memory_records_canonical_event_provenance_and_version(tmp_path):
    from antisentinel.memory.rollout import RolloutMemory
    from antisentinel.persistence.memory_store import FileMemoryStore, InMemoryMemoryCache
    from antisentinel.ports.model import FinalDiagnosis
    from antisentinel.worker.runtime.loop import RuntimeResult
    from antisentinel.domain.event import Event
    from datetime import datetime, timezone

    event = Event.create(type="session.completed", aggregate_type="Session", aggregate_id="session-1", correlation_id="session-1", occurred_at=datetime.now(timezone.utc), payload={"status": "completed"})
    result = RuntimeResult(status="completed", incident_id="incident-1", session_id="session-1", turn_count=1, final=FinalDiagnosis(summary="fixed", diagnosis="healthy", confidence=.9, evidence_refs=()), evidence_refs=(), task_summaries=(), error=None, events=(event,))
    record = RolloutMemory(FileMemoryStore(tmp_path), InMemoryMemoryCache()).record(result)

    assert record["source_event_ids"] == [str(event.event_id)]
    assert record["source_refs"] == [{"type": "event", "id": str(event.event_id)}]
    assert record["content_version"] == 1


def test_sqlite_typed_memory_record_round_trips_and_is_idempotent(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    record = MemoryRecord.create(
        memory_id="memory-typed-1", memory_type="episodic", operator_id="operator-1",
        incident_id="incident-1", session_id="session-1", content="upstream timeout",
        source_refs=(MemorySourceRef("evidence", "evidence-1", "supporting", "sha256:abc", 3),),
        extraction_confidence=0.8,
        valid_from=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    store = SQLiteMemoryStore(database)

    store.append_record(record)
    store.append_record(record)

    assert store.get_record("memory-typed-1") == record
    assert database.query("SELECT COUNT(*) FROM memory_records")[0][0] == 1


def test_sqlite_typed_memory_record_rejects_changed_content_for_same_id(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    record = MemoryRecord.create(
        memory_id="memory-typed-1", memory_type="episodic", operator_id="operator-1",
        incident_id="incident-1", session_id="session-1", content="upstream timeout",
        source_refs=(MemorySourceRef("event", "event-1"),), extraction_confidence=0.8,
        valid_from=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    store = SQLiteMemoryStore(database)
    store.append_record(record)

    with pytest.raises(DomainError, match="memory conflict"):
        store.append_record(replace(record, content="different", content_version=2))


def test_legacy_source_ids_are_preserved_as_unknown_sources(tmp_path):
    from antisentinel.persistence.memory_store import FileMemoryStore

    store = FileMemoryStore(tmp_path)
    store.append({
        "memory_id": "legacy-1", "memory_type": "episodic", "operator_id": "operator-1",
        "incident_id": "incident-1", "session_id": "session-1", "content": "timeout",
        "source_ids": ["session-1"], "confidence": 0.6,
        "valid_from": "2026-09-05T00:00:00+00:00",
    })

    assert store.get_record("legacy-1").source_refs == (MemorySourceRef("unknown", "session-1"),)
