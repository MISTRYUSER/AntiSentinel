import json

from antisentinel.domain.evidence import Evidence
from antisentinel.domain.event import Event
from antisentinel.domain.incident import Incident
from antisentinel.domain.primitives import utc_now
from antisentinel.domain.session import Session
from antisentinel.entry.conversation_store import FileConversationStore
from antisentinel.persistence.application_store import FileApplicationStore
from antisentinel.persistence.event_store import FileEventStore
from antisentinel.persistence.evidence_store import FileEvidenceStore
from antisentinel.persistence.legacy_import import LegacyImporter
from antisentinel.persistence.memory_store import FileMemoryStore
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteApplicationStore, SQLiteConversationStore
from antisentinel.persistence.state_store import FileStateStore


def _build_legacy_storage(root):
    application = FileApplicationStore(root)
    incident = Incident.create(title="legacy project", source="operator", summary="background")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])
    incident.add_session(session.session_id)
    application.save_incident(incident.to_dict())
    application.save_session(session.to_dict())
    application.save_result(str(session.session_id), {"session_id": str(session.session_id), "status": "completed"})
    FileConversationStore(root).append(str(session.session_id), "user", "先看日志")
    event = Event.create(
        type="session.completed",
        aggregate_type="Session",
        aggregate_id=str(session.session_id),
        correlation_id=str(session.session_id),
        occurred_at=utc_now(),
        payload={"status": "completed"},
        related_ids={"incident_id": str(incident.incident_id), "session_id": str(session.session_id)},
    )
    FileEventStore(root).append(event)
    evidence = Evidence.create(
        evidence_id="evidence-legacy",
        kind="health_probe",
        content_ref="redis://PING",
        content_hash="sha256:legacy",
        metadata={"incident_id": str(incident.incident_id)},
    )
    FileEvidenceStore(root).put_once(evidence, incident_id=str(incident.incident_id))
    FileStateStore(root).save_projection(str(incident.incident_id), {"status": "completed"})
    FileMemoryStore(root).append({
        "memory_id": "memory-legacy", "memory_type": "preference", "operator_id": "operator-1",
        "object": "logs_before_metrics", "content_version": 1,
    })
    trace_path = root / "observability" / "traces.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps({
        "name": "session.run", "trace_id": 101, "span_id": 202,
        "start_time_ns": 1, "end_time_ns": 2,
        "attributes": {"trace_id": "trace-legacy", "session_id": str(session.session_id), "request_id": "request-1"},
    }) + "\n", encoding="utf-8")
    return incident, session


def test_legacy_import_is_idempotent_and_preserves_relationships(tmp_path):
    source = tmp_path / "legacy"
    incident, session = _build_legacy_storage(source)
    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    importer = LegacyImporter(source, database)

    first = importer.run()
    second = importer.run()

    assert first.failed == 0
    assert first.inserted == first.scanned
    assert first.inserted >= 9
    assert second.failed == 0
    assert second.inserted == 0
    assert second.skipped == second.scanned
    assert database.query("PRAGMA foreign_key_check") == []
    assert SQLiteApplicationStore(database).load_result(str(session.session_id))["status"] == "completed"
    assert SQLiteConversationStore(database).load(str(session.session_id))[0]["content"] == "先看日志"
    assert database.query("SELECT COUNT(*) AS count FROM events")[0]["count"] == 1
    assert database.query("SELECT COUNT(*) AS count FROM evidence")[0]["count"] == 1
    assert database.query("SELECT COUNT(*) AS count FROM memory_records")[0]["count"] == 1
    assert database.query("SELECT trace_id FROM traces")[0]["trace_id"] == "trace-legacy"
    assert database.query("SELECT session_id FROM spans")[0]["session_id"] == str(session.session_id)
    assert database.query("SELECT scope_id FROM states")[0]["scope_id"] == str(incident.incident_id)


def test_legacy_import_reports_invalid_record_without_marking_it_imported(tmp_path):
    source = tmp_path / "legacy"
    path = source / "memory" / "records.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"missing":"memory_id"}\n', encoding="utf-8")
    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()

    report = LegacyImporter(source, database).run()

    assert report.scanned == 1
    assert report.failed == 1
    assert report.inserted == 0
    assert report.errors
    assert database.query("SELECT * FROM import_ledger") == []
