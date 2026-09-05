from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import DomainError
from antisentinel.domain.event import Event
from antisentinel.domain.evidence import Evidence
from antisentinel.persistence import event_store, evidence_store, state_store


def make_event(*, event_id: str | None = None, aggregate_id: str = "incident-1") -> Event:
    event = Event.create(
        type="incident.created",
        aggregate_type="Incident",
        aggregate_id=aggregate_id,
        correlation_id="trace-1",
        occurred_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        payload={"title": "checkout 502"},
    )
    if event_id is not None:
        return Event(
            event_id=event_id,
            type=event.type,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            correlation_id=event.correlation_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
            causation_id=event.causation_id,
            related_ids=event.related_ids,
        )
    return event


def test_event_store_appends_and_recovers_events_from_disk(tmp_path):
    first = make_event()
    second = make_event(aggregate_id="session-1")
    store = event_store.FileEventStore(tmp_path)

    store.append(first)
    store.append(second)
    reopened = event_store.FileEventStore(tmp_path)

    assert reopened.list_by_aggregate("Incident", "incident-1") == [first]
    assert reopened.list_by_correlation("trace-1") == [first, second]


def test_event_store_replaying_same_event_is_idempotent(tmp_path):
    event = make_event(event_id="event-1")
    store = event_store.FileEventStore(tmp_path)

    store.append(event)
    store.append(event)

    assert store.list_by_correlation("trace-1") == [event]


def test_event_store_rejects_same_id_with_different_payload(tmp_path):
    store = event_store.FileEventStore(tmp_path)
    store.append(make_event(event_id="event-1"))
    conflicting = make_event(event_id="event-1")
    conflicting = Event(
        event_id=conflicting.event_id,
        type=conflicting.type,
        aggregate_type=conflicting.aggregate_type,
        aggregate_id=conflicting.aggregate_id,
        correlation_id=conflicting.correlation_id,
        occurred_at=conflicting.occurred_at,
        payload={"title": "different"},
        causation_id=conflicting.causation_id,
        related_ids=conflicting.related_ids,
    )

    with pytest.raises(DomainError, match="event conflict"):
        store.append(conflicting)


def test_evidence_store_is_idempotent_only_for_matching_hash(tmp_path):
    evidence = Evidence.create(
        evidence_id="evidence-1", kind="log", content_ref="vault://log", content_hash="hash-1"
    )
    store = evidence_store.FileEvidenceStore(tmp_path)

    store.put_once(evidence)
    store.put_once(evidence)

    assert store.get("evidence-1") == evidence

    conflicting = Evidence.create(
        evidence_id="evidence-1", kind="log", content_ref="vault://other", content_hash="hash-2"
    )
    with pytest.raises(DomainError, match="evidence hash conflict"):
        store.put_once(conflicting)


def test_state_store_persists_projection_and_projection_lag(tmp_path):
    store = state_store.FileStateStore(tmp_path)
    store.save_projection("incident-1", {"status": "open", "event_count": 1})
    store.mark_projection_lag("incident-1", "invalid transition")
    reopened = state_store.FileStateStore(tmp_path)

    assert reopened.load_projection("incident-1") == {
        "status": "open",
        "event_count": 1,
        "projection_lag": {"error": "invalid transition"},
    }
