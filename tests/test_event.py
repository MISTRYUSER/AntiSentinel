import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.event import Event
from antisentinel.domain.ids import EventId, TaskId


def test_event_contains_aggregate_correlation_and_related_ids():
    event = Event.create(
        type="task.started",
        aggregate_type="Task",
        aggregate_id=TaskId("task-1"),
        correlation_id=TaskId("session-1"),
        causation_id=EventId("event-0"),
        related_ids={"task_id": TaskId("task-1"), "turn_id": "turn-1"},
        occurred_at=datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc),
        payload={"objective": "collect logs"},
    )

    assert event.type == "task.started"
    assert event.aggregate_id == "task-1"
    assert event.correlation_id == "session-1"
    assert event.causation_id == "event-0"
    assert event.related_ids["turn_id"] == "turn-1"


def test_event_rejects_empty_identity_fields_or_non_json_payload():
    common = {
        "type": "task.started",
        "aggregate_type": "Task",
        "aggregate_id": TaskId("task-1"),
        "correlation_id": TaskId("session-1"),
        "occurred_at": datetime.now(timezone.utc),
        "payload": {},
    }

    with pytest.raises(InvalidInputError):
        Event.create(**{**common, "type": ""})

    with pytest.raises(InvalidInputError):
        Event.create(**{**common, "payload": {"bad": object()}})


def test_event_is_immutable():
    event = Event.create(
        type="task.started",
        aggregate_type="Task",
        aggregate_id=TaskId("task-1"),
        correlation_id=TaskId("session-1"),
        occurred_at=datetime.now(timezone.utc),
        payload={"objective": "collect logs"},
    )

    with pytest.raises(TypeError):
        event.related_ids["task_id"] = "task-2"

    with pytest.raises((TypeError, AttributeError)):
        event.type = "task.failed"


def test_event_json_round_trip_preserves_event():
    event = Event.create(
        type="task.failed",
        aggregate_type="Task",
        aggregate_id=TaskId("task-1"),
        correlation_id=TaskId("session-1"),
        related_ids={"incident_id": "incident-1"},
        occurred_at=datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc),
        payload={"code": "worker_error"},
    )

    restored = Event.from_dict(json.loads(json.dumps(event.to_dict())))

    assert restored == event
    assert restored.occurred_at.tzinfo == timezone.utc
