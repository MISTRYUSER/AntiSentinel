import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import InvalidInputError, InvalidTransitionError
from antisentinel.domain.ids import AttemptId, TaskId
from antisentinel.domain.tool_call import ToolCall, ToolCallStatus


def test_tool_call_is_requested_with_serializable_arguments():
    tool_call = ToolCall.create(
        task_id=TaskId("task-1"),
        tool_name="read_logs",
        arguments={"service": "checkout", "limit": 20},
        target="prod",
    )

    assert tool_call.status is ToolCallStatus.REQUESTED
    assert tool_call.arguments == {"service": "checkout", "limit": 20}
    assert [event.type for event in tool_call.pending_events] == ["tool_call.requested"]


def test_tool_call_requires_task_name_and_json_arguments():
    with pytest.raises(InvalidInputError):
        ToolCall.create(task_id=TaskId(""), tool_name="read_logs", arguments={})

    with pytest.raises(InvalidInputError):
        ToolCall.create(task_id=TaskId("task-1"), tool_name="", arguments={})

    with pytest.raises(InvalidInputError):
        ToolCall.create(task_id=TaskId("task-1"), tool_name="read_logs", arguments={"bad": object()})


def test_tool_call_succeeds_after_running():
    tool_call = ToolCall.create(task_id=TaskId("task-1"), tool_name="read_logs", arguments={})

    tool_call.start()
    tool_call.succeed()

    assert tool_call.status is ToolCallStatus.SUCCEEDED
    assert [event.type for event in tool_call.pending_events] == [
        "tool_call.requested",
        "tool_call.started",
        "tool_call.succeeded",
    ]


def test_tool_call_can_be_denied_before_start():
    tool_call = ToolCall.create(task_id=TaskId("task-1"), tool_name="restart", arguments={})

    tool_call.deny("approval required")

    assert tool_call.status is ToolCallStatus.DENIED


def test_tool_call_records_attempt_reference_once():
    tool_call = ToolCall.create(task_id=TaskId("task-1"), tool_name="read_logs", arguments={})

    tool_call.add_attempt(AttemptId("attempt-1"))
    tool_call.add_attempt(AttemptId("attempt-1"))

    assert tool_call.attempt_ids == ["attempt-1"]


def test_tool_call_rejects_illegal_transition_without_event():
    tool_call = ToolCall.create(task_id=TaskId("task-1"), tool_name="read_logs", arguments={})
    tool_call.start()
    tool_call.succeed()
    event_count = len(tool_call.pending_events)

    with pytest.raises(InvalidTransitionError):
        tool_call.start()

    assert tool_call.status is ToolCallStatus.SUCCEEDED
    assert len(tool_call.pending_events) == event_count


def test_tool_call_json_round_trip_preserves_arguments_and_state():
    created_at = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)
    tool_call = ToolCall.create(
        task_id=TaskId("task-1"),
        tool_name="read_logs",
        arguments={"service": "checkout"},
        tool_call_id="tool-call-1",
        created_at=created_at,
    )
    tool_call.add_attempt(AttemptId("attempt-1"))
    tool_call.start()
    tool_call.time_out("worker deadline exceeded")

    restored = ToolCall.from_dict(json.loads(json.dumps(tool_call.to_dict())))

    assert restored == tool_call
    assert restored.created_at.tzinfo == timezone.utc
