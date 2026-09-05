import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import InvalidInputError, InvalidTransitionError
from antisentinel.domain.ids import ToolCallId, TurnId
from antisentinel.domain.task import Task, TaskStatus


def test_task_creates_pending_and_records_created_event():
    task = Task.create(turn_id=TurnId("turn-1"), objective="collect checkout logs")

    assert task.status is TaskStatus.PENDING
    assert task.turn_id == "turn-1"
    assert task.objective == "collect checkout logs"
    assert [event.type for event in task.pending_events] == ["task.created"]


def test_task_requires_turn_id_and_objective():
    with pytest.raises(InvalidInputError):
        Task.create(turn_id=TurnId(""), objective="inspect")

    with pytest.raises(InvalidInputError):
        Task.create(turn_id=TurnId("turn-1"), objective="")


def test_task_waits_for_tool_and_resumes():
    task = Task.create(turn_id=TurnId("turn-1"), objective="collect logs")

    task.start()
    task.wait_for_tool()
    task.resume()
    task.succeed("logs collected")

    assert task.status is TaskStatus.SUCCEEDED
    assert task.result_summary == "logs collected"
    assert [event.type for event in task.pending_events] == [
        "task.created",
        "task.started",
        "task.waiting_tool",
        "task.resumed",
        "task.succeeded",
    ]


def test_task_waits_for_approval_then_resumes():
    task = Task.create(turn_id=TurnId("turn-1"), objective="restart service")

    task.start()
    task.wait_for_approval()
    task.resume()

    assert task.status is TaskStatus.RUNNING
    assert [event.type for event in task.pending_events][-2:] == [
        "task.waiting_approval",
        "task.resumed",
    ]


def test_task_adds_tool_call_reference_once():
    task = Task.create(turn_id=TurnId("turn-1"), objective="collect logs")

    task.add_tool_call(ToolCallId("tool-call-1"))
    task.add_tool_call(ToolCallId("tool-call-1"))

    assert task.tool_call_ids == ["tool-call-1"]


def test_task_rejects_illegal_transition_without_event():
    task = Task.create(turn_id=TurnId("turn-1"), objective="collect logs")
    task.start()
    task.succeed("done")
    event_count = len(task.pending_events)

    with pytest.raises(InvalidTransitionError):
        task.cancel("too late")

    assert task.status is TaskStatus.SUCCEEDED
    assert len(task.pending_events) == event_count


def test_task_json_round_trip_preserves_state():
    created_at = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)
    task = Task.create(
        turn_id=TurnId("turn-1"),
        objective="collect logs",
        task_id="task-1",
        created_at=created_at,
    )
    task.add_tool_call(ToolCallId("tool-call-1"))
    task.start()
    task.succeed("done")

    restored = Task.from_dict(json.loads(json.dumps(task.to_dict())))

    assert restored == task
    assert restored.created_at.tzinfo == timezone.utc
