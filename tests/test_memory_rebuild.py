import json
from datetime import datetime, timezone

from antisentinel.domain.event import Event
from antisentinel.persistence.event_replay import EventReplayProjector
from antisentinel.persistence.memory_rebuild import MemoryProjectionRebuilder
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteApplicationStore, SQLiteEventStore, SQLiteMemoryStore


def test_memory_projection_rebuilds_rollout_from_session_result_and_events(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    app = SQLiteApplicationStore(database)
    event_store = SQLiteEventStore(database)
    incident = {"incident_id": "incident-1", "title": "Redis", "source": "operator", "status": "open", "created_at": "2026-01-01T00:00:00+00:00", "summary": None, "session_ids": ["session-1"], "pending_events": []}
    session = {"session_id": "session-1", "incident_id": "incident-1", "participant_ids": ["operator-1"], "status": "completed", "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00", "summary": "Redis 正常", "turn_ids": [], "pending_events": []}
    app.save_incident(incident); app.save_session(session)
    app.save_result("session-1", {"session_id": "session-1", "status": "completed", "final": {"summary": "Redis 正常", "diagnosis": "Redis 可达"}, "evidence_refs": [{"evidence_id": "evidence-1", "role": "tool_result"}]})
    event_store.append(Event.create(type="session.completed", aggregate_type="Session", aggregate_id="session-1", correlation_id="session-1", occurred_at=datetime.now(timezone.utc), payload={"status": "completed"}))
    event_store.append(Event.create(type="diagnosis.completed", aggregate_type="Session", aggregate_id="session-1", correlation_id="session-1", occurred_at=datetime.now(timezone.utc), payload={"status": "completed"}))

    report = MemoryProjectionRebuilder(database).rebuild()
    record = SQLiteMemoryStore(database).get("rollout:session-1")

    assert report.sessions_seen == 1
    assert report.records_written == 1
    assert record["source_event_ids"] and len(record["source_event_ids"]) == 2
    assert record["source_refs"][-1] == {"type": "evidence", "id": "evidence-1", "role": "tool_result"}
    assert EventReplayProjector().project([Event.from_dict(json.loads(row["record_json"])) for row in database.query("SELECT record_json FROM events")])["event_count"] == 2


def test_memory_projection_rebuild_is_idempotent(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    database.query("SELECT 1")
    report = MemoryProjectionRebuilder(database).rebuild()

    assert report.sessions_seen == 0
    assert report.records_written == 0
    assert report.errors == ()
