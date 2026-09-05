import pytest

from antisentinel.domain.attempt import Attempt, AttemptStatus
from antisentinel.domain.errors import InvalidTransitionError
from antisentinel.domain.evidence import Evidence, EvidenceRef
from antisentinel.domain.ids import (
    AttemptId,
    EvidenceId,
    IncidentId,
    SessionId,
    TaskId,
    ToolCallId,
    TurnId,
)
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.task import Task
from antisentinel.domain.tool_call import ToolCall
from antisentinel.domain.turn import ModelOutputKind, Turn


def test_complete_domain_chain_is_linked_and_auditable():
    incident = Incident.create(title="checkout degraded", source="monitor", incident_id=IncidentId("incident-1"))
    session = Session.create(incident_id=incident.incident_id, participant_ids=["agent-1"], session_id=SessionId("session-1"))
    incident.add_session(session.session_id)
    turn = Turn.create(session_id=session.session_id, turn_id=TurnId("turn-1"))
    session.add_turn(turn.turn_id)
    task = Task.create(turn_id=turn.turn_id, objective="read checkout logs", task_id=TaskId("task-1"))
    turn.add_task(task.task_id)
    tool_call = ToolCall.create(
        task_id=task.task_id,
        tool_name="read_logs",
        arguments={"service": "checkout"},
        tool_call_id=ToolCallId("tool-call-1"),
    )
    task.add_tool_call(tool_call.tool_call_id)
    attempt = Attempt.create(tool_call_id=tool_call.tool_call_id, attempt_id=AttemptId("attempt-1"))
    tool_call.add_attempt(attempt.attempt_id)
    evidence = Evidence.create(kind="log", content_ref="logs://attempt-1", evidence_id=EvidenceId("evidence-1"))

    attempt.start()
    attempt.succeed(result={"lines": ["ERROR timeout"]}, result_summary="one error")
    attempt.add_evidence(EvidenceRef(evidence.evidence_id, role="diagnostic"))
    tool_call.start()
    tool_call.succeed()
    turn.start()
    turn.complete(ModelOutputKind.FINAL_ANSWER, output_summary="root cause found")
    task.start()
    task.succeed("diagnosis complete")

    assert incident.session_ids == [session.session_id]
    assert session.turn_ids == [turn.turn_id]
    assert turn.task_ids == [task.task_id]
    assert task.tool_call_ids == [tool_call.tool_call_id]
    assert tool_call.attempt_ids == [attempt.attempt_id]
    assert attempt.evidence_refs == [EvidenceRef(evidence.evidence_id, role="diagnostic")]
    assert event_chain(incident, session, turn, task, tool_call, attempt, evidence) == [
        "incident.created",
        "session.created",
        "turn.created",
        "task.created",
        "tool_call.requested",
        "attempt.created",
        "evidence.recorded",
        "attempt.started",
        "attempt.completed",
        "tool_call.started",
        "tool_call.succeeded",
        "turn.started",
        "turn.completed",
        "task.started",
        "task.succeeded",
    ]


def event_chain(*objects):
    events = [event for obj in objects for event in getattr(obj, "pending_events", ())]
    return [event.type for event in sorted(events, key=lambda item: item.occurred_at)]


@pytest.mark.parametrize(
    ("factory", "start", "finish"),
    [
        (lambda: Session.create(incident_id=IncidentId("incident-1"), participant_ids=["agent-1"]), lambda x: x.complete(), lambda x: x.wait()),
        (lambda: Turn.create(session_id=SessionId("session-1")), lambda x: x.start(), lambda x: x.start()),
        (lambda: Task.create(turn_id=TurnId("turn-1"), objective="inspect"), lambda x: x.start(), lambda x: x.start()),
        (lambda: ToolCall.create(task_id=TaskId("task-1"), tool_name="read_logs", arguments={}), lambda x: x.start(), lambda x: x.start()),
        (lambda: Attempt.create(tool_call_id=ToolCallId("tool-call-1")), lambda x: x.start(), lambda x: x.start()),
    ],
)
def test_terminal_or_completed_state_rejects_further_transition(factory, start, finish):
    obj = factory()
    start(obj)
    if isinstance(obj, (Turn, Task, ToolCall, Attempt)):
        if isinstance(obj, Turn):
            obj.complete(ModelOutputKind.TEXT)
        elif isinstance(obj, Task):
            obj.succeed("done")
        elif isinstance(obj, ToolCall):
            obj.succeed()
        else:
            obj.succeed()
    with pytest.raises(InvalidTransitionError):
        finish(obj)


def test_timed_out_attempt_can_retry_and_failed_retry_has_dump_evidence():
    tool_call = ToolCall.create(task_id=TaskId("task-1"), tool_name="read_logs", arguments={})
    first = Attempt.create(tool_call_id=tool_call.tool_call_id, retry_index=0)
    tool_call.add_attempt(first.attempt_id)
    first.start()
    first.time_out({"code": "timeout", "message": "worker deadline exceeded"})

    retry = Attempt.create(tool_call_id=tool_call.tool_call_id, retry_index=1)
    tool_call.add_attempt(retry.attempt_id)
    retry.start()
    retry.fail({"code": "worker_error", "message": "connection refused"})
    dump = Evidence.create(
        kind="retry_failure_dump",
        content_ref="dump://tool-call-1/retry-1",
        metadata={"attempt_id": retry.attempt_id, "retry_index": retry.retry_index},
    )
    retry.add_evidence(EvidenceRef(dump.evidence_id, role="retry_failure_dump"))

    assert first.status is AttemptStatus.TIMED_OUT
    assert retry.status is AttemptStatus.FAILED
    assert retry.retry_index == first.retry_index + 1
    assert retry.evidence_refs == [EvidenceRef(dump.evidence_id, role="retry_failure_dump")]
    assert tool_call.attempt_ids == [first.attempt_id, retry.attempt_id]
