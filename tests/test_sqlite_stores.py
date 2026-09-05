from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import DomainError
from antisentinel.domain.event import Event
from antisentinel.domain.evidence import Evidence
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import (
    SQLiteApplicationStore,
    SQLiteConversationStore,
    SQLiteEventStore,
    SQLiteEvidenceStore,
    SQLiteMemoryStore,
    SQLiteStateStore,
)


@pytest.fixture
def database(tmp_path):
    value = SQLiteDatabase(tmp_path / "antisentinel.db")
    value.initialize()
    return value


def test_application_store_round_trips_incident_session_and_result(database):
    store = SQLiteApplicationStore(database)
    incident = Incident.create(title="SQLite project", source="operator", summary="background")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])
    incident.add_session(session.session_id)

    store.save_incident(incident.to_dict())
    store.save_session(session.to_dict())
    store.save_result(str(session.session_id), {"session_id": str(session.session_id), "status": "completed"})

    assert store.load_incidents() == [incident.to_dict()]
    assert store.load_sessions() == [session.to_dict()]
    assert store.load_result(str(session.session_id)) == {"session_id": str(session.session_id), "status": "completed"}


def test_conversation_store_preserves_append_order(database):
    application = SQLiteApplicationStore(database)
    incident = Incident.create(title="chat", source="operator")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])
    application.save_incident(incident.to_dict())
    application.save_session(session.to_dict())
    store = SQLiteConversationStore(database)

    first = store.append(str(session.session_id), "user", "先看日志")
    second = store.append(str(session.session_id), "assistant", "收到")

    assert [item["role"] for item in store.load(str(session.session_id))] == ["user", "assistant"]
    assert first["session_id"] == str(session.session_id)
    assert second["created_at"] >= first["created_at"]


def test_event_store_is_idempotent_and_rejects_same_id_with_different_content(database):
    store = SQLiteEventStore(database)
    event = Event.create(
        type="incident.created",
        aggregate_type="Incident",
        aggregate_id="incident-1",
        correlation_id="correlation-1",
        occurred_at=datetime.now(timezone.utc),
        payload={"status": "open"},
    )
    conflicting = Event.from_dict({**event.to_dict(), "payload": {"status": "closed"}})

    store.append(event)
    store.append(event)

    assert store.list_by_aggregate("Incident", "incident-1") == [event]
    assert store.list_by_correlation("correlation-1") == [event]
    with pytest.raises(DomainError, match="event conflict"):
        store.append(conflicting)


def test_evidence_store_is_immutable_and_round_trips(database):
    store = SQLiteEvidenceStore(database)
    evidence = Evidence.create(
        evidence_id="evidence-1",
        kind="health_probe",
        content_ref="redis://127.0.0.1:6379/PING",
        content_hash="sha256:one",
        source="redis",
        metadata={"incident_id": "incident-1"},
    )
    conflicting = Evidence.from_dict({**evidence.to_dict(), "content_hash": "sha256:two"})

    store.put_once(evidence, incident_id="incident-1")
    store.put_once(evidence, incident_id="incident-1")

    assert store.get("evidence-1") == evidence
    with pytest.raises(DomainError, match="evidence hash conflict"):
        store.put_once(conflicting, incident_id="incident-1")


def test_state_store_saves_and_marks_projection_lag(database):
    store = SQLiteStateStore(database)
    store.save_projection("incident-1", {"status": "running"})
    store.mark_projection_lag("incident-1", "worker timeout")

    assert store.load_projection("incident-1") == {
        "status": "running",
        "projection_lag": {"error": "worker timeout"},
    }


def test_memory_store_appends_replaces_and_queries(database):
    store = SQLiteMemoryStore(database)
    first = {
        "memory_id": "memory-1",
        "memory_type": "preference",
        "operator_id": "operator-1",
        "object": "logs_before_metrics",
        "confidence": 0.8,
        "content_version": 1,
    }
    replaced = {**first, "confidence": 0.9, "content_version": 2}

    store.append(first)
    store.append(first)
    store.replace(replaced)

    assert store.get("memory-1") == replaced
    assert store.list_by_operator("operator-1") == [replaced]
    assert store.list_by_type("preference") == [replaced]
