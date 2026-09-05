import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import InvalidInputError, InvalidTransitionError
from antisentinel.domain.incident import Incident, IncidentStatus


def test_incident_creates_open_and_records_created_event():
    incident = Incident.create(title="API 5xx", source="monitor")

    assert incident.status is IncidentStatus.OPEN
    assert incident.title == "API 5xx"
    assert incident.source == "monitor"
    assert [event.type for event in incident.pending_events] == ["incident.created"]
    assert incident.pending_events[0].aggregate_id == incident.incident_id
    assert incident.pending_events[0].correlation_id == incident.incident_id


def test_incident_rejects_empty_title_or_source():
    with pytest.raises(InvalidInputError):
        Incident.create(title="", source="monitor")

    with pytest.raises(InvalidInputError):
        Incident.create(title="API 5xx", source="")


def test_incident_resolves_then_closes():
    incident = Incident.create(title="API 5xx", source="monitor")

    incident.resolve()
    incident.close()

    assert incident.status is IncidentStatus.CLOSED
    assert [event.type for event in incident.pending_events] == [
        "incident.created",
        "incident.resolved",
        "incident.closed",
    ]


def test_incident_rejects_illegal_transition_without_event():
    incident = Incident.create(title="API 5xx", source="monitor")
    incident.resolve()
    incident.close()
    event_count = len(incident.pending_events)

    with pytest.raises(InvalidTransitionError):
        incident.resolve()

    assert incident.status is IncidentStatus.CLOSED
    assert len(incident.pending_events) == event_count


def test_incident_json_round_trip_preserves_identity_and_utc_time():
    created_at = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)
    incident = Incident.create(
        title="API 5xx",
        source="monitor",
        summary="checkout degraded",
        created_at=created_at,
    )

    restored = Incident.from_dict(json.loads(json.dumps(incident.to_dict())))

    assert restored == incident
    assert restored.created_at == created_at
    assert restored.created_at.tzinfo == timezone.utc


def test_incident_rejects_naive_datetime():
    with pytest.raises(InvalidInputError):
        Incident.create(
            title="API 5xx",
            source="monitor",
            created_at=datetime(2026, 9, 3, 4, 0),
        )
