import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import InvalidInputError, InvalidTransitionError
from antisentinel.domain.ids import IncidentId, TurnId
from antisentinel.domain.session import Session, SessionStatus


def test_session_creates_active_and_records_created_event():
    session = Session.create(
        incident_id=IncidentId("incident-1"),
        participant_ids=["user-1", "agent-1"],
    )

    assert session.status is SessionStatus.ACTIVE
    assert session.incident_id == "incident-1"
    assert session.participant_ids == ["user-1", "agent-1"]
    assert [event.type for event in session.pending_events] == ["session.created"]
    assert session.pending_events[0].aggregate_id == session.session_id
    assert session.pending_events[0].correlation_id == session.session_id


def test_session_requires_incident_and_participant():
    with pytest.raises(InvalidInputError):
        Session.create(incident_id=IncidentId(""), participant_ids=["user-1"])

    with pytest.raises(InvalidInputError):
        Session.create(incident_id=IncidentId("incident-1"), participant_ids=[])


def test_session_adds_turn_reference_once():
    session = Session.create(incident_id=IncidentId("incident-1"), participant_ids=["user-1"])

    session.add_turn(TurnId("turn-1"))
    session.add_turn(TurnId("turn-1"))

    assert session.turn_ids == ["turn-1"]


def test_session_waits_then_completes():
    session = Session.create(incident_id=IncidentId("incident-1"), participant_ids=["user-1"])

    session.wait()
    session.complete(summary="diagnosis finished")

    assert session.status is SessionStatus.COMPLETED
    assert session.summary == "diagnosis finished"
    assert [event.type for event in session.pending_events] == [
        "session.created",
        "session.waiting",
        "session.completed",
    ]


def test_session_rejects_illegal_transition_without_event():
    session = Session.create(incident_id=IncidentId("incident-1"), participant_ids=["user-1"])
    session.complete()
    event_count = len(session.pending_events)

    with pytest.raises(InvalidTransitionError):
        session.wait()

    assert session.status is SessionStatus.COMPLETED
    assert len(session.pending_events) == event_count


def test_session_json_round_trip_preserves_state():
    created_at = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)
    session = Session.create(
        incident_id=IncidentId("incident-1"),
        participant_ids=["user-1"],
        session_id="session-1",
        created_at=created_at,
    )
    session.add_turn(TurnId("turn-1"))

    restored = Session.from_dict(json.loads(json.dumps(session.to_dict())))

    assert restored == session
    assert restored.created_at.tzinfo == timezone.utc
