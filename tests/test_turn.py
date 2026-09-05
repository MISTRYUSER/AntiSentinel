import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import InvalidInputError, InvalidTransitionError
from antisentinel.domain.ids import SessionId, TaskId
from antisentinel.domain.turn import ModelOutputKind, Turn, TurnStatus


def test_turn_creates_pending_and_records_created_event():
    turn = Turn.create(session_id=SessionId("session-1"), context_summary="inspect API errors")

    assert turn.status is TurnStatus.PENDING
    assert turn.session_id == "session-1"
    assert turn.context_summary == "inspect API errors"
    assert [event.type for event in turn.pending_events] == ["turn.created"]


def test_turn_requires_session_id():
    with pytest.raises(InvalidInputError):
        Turn.create(session_id=SessionId(""))


def test_turn_starts_waits_for_tool_and_completes_with_output_kind():
    turn = Turn.create(session_id=SessionId("session-1"))

    turn.start()
    turn.wait_for_tool()
    turn.complete(ModelOutputKind.FINAL_ANSWER, output_summary="root cause identified")

    assert turn.status is TurnStatus.COMPLETED
    assert turn.model_output_kind is ModelOutputKind.FINAL_ANSWER
    assert turn.output_summary == "root cause identified"
    assert [event.type for event in turn.pending_events] == [
        "turn.created",
        "turn.started",
        "turn.waiting_tool",
        "turn.completed",
    ]


def test_turn_can_record_multiple_task_references_without_duplicates():
    turn = Turn.create(session_id=SessionId("session-1"))

    turn.add_task(TaskId("task-1"))
    turn.add_task(TaskId("task-1"))
    turn.add_task(TaskId("task-2"))

    assert turn.task_ids == ["task-1", "task-2"]


def test_turn_rejects_illegal_transition_without_event():
    turn = Turn.create(session_id=SessionId("session-1"))
    turn.start()
    turn.complete(ModelOutputKind.TEXT)
    event_count = len(turn.pending_events)

    with pytest.raises(InvalidTransitionError):
        turn.start()

    assert turn.status is TurnStatus.COMPLETED
    assert len(turn.pending_events) == event_count


def test_turn_json_round_trip_preserves_output_kind_and_time():
    created_at = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)
    turn = Turn.create(
        session_id=SessionId("session-1"),
        turn_id="turn-1",
        created_at=created_at,
    )
    turn.add_task(TaskId("task-1"))
    turn.start()
    turn.complete(ModelOutputKind.TOOL_CALL, output_summary="requested logs")

    restored = Turn.from_dict(json.loads(json.dumps(turn.to_dict())))

    assert restored == turn
    assert restored.model_output_kind is ModelOutputKind.TOOL_CALL
    assert restored.created_at.tzinfo == timezone.utc
