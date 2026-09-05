from datetime import datetime, timezone

from antisentinel.domain.event import Event
from antisentinel.persistence.event_replay import EventReplayProjector


def event(event_type, *, aggregate_id="incident-1", payload=None, occurred_at=None):
    return Event.create(
        type=event_type, aggregate_type="Incident", aggregate_id=aggregate_id,
        correlation_id="correlation-1", occurred_at=occurred_at or datetime.now(timezone.utc),
        payload=payload or {},
    )


def test_event_replay_is_deterministic_and_tracks_lifecycle_counts():
    events = [
        event("incident.created", payload={"status": "open"}),
        event("session.started", aggregate_id="session-1", payload={"session_id": "session-1"}),
        event("tool.completed", aggregate_id="session-1", payload={"status": "succeeded"}),
        event("diagnosis.completed", aggregate_id="session-1", payload={"status": "completed"}),
    ]

    first = EventReplayProjector().project(events)
    second = EventReplayProjector().project(list(reversed(events)))

    assert first == second
    assert first["event_count"] == 4
    assert first["status"] == "completed"
    assert first["event_types"] == {
        "incident.created": 1, "session.started": 1,
        "tool.completed": 1, "diagnosis.completed": 1,
    }


def test_event_replay_ignores_duplicate_event_ids_and_rejects_conflicts():
    original = event("incident.created", payload={"status": "open"})
    duplicate = Event.from_dict(original.to_dict())
    conflicting = Event.from_dict({**original.to_dict(), "payload": {"status": "closed"}})
    projector = EventReplayProjector()

    assert projector.project([original, duplicate])["event_count"] == 1
    try:
        projector.project([original, conflicting])
    except ValueError as exc:
        assert "event conflict" in str(exc)
    else:
        raise AssertionError("conflicting event was accepted")
