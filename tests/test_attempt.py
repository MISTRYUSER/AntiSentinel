import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.attempt import Attempt, AttemptStatus
from antisentinel.domain.errors import InvalidInputError, InvalidTransitionError
from antisentinel.domain.evidence import EvidenceRef
from antisentinel.domain.ids import EvidenceId, ToolCallId


def test_attempt_creates_with_retry_index_zero():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))

    assert attempt.retry_index == 0
    assert attempt.status is AttemptStatus.CREATED
    assert [event.type for event in attempt.pending_events] == ["attempt.created"]


def test_attempt_rejects_missing_tool_call_or_negative_retry_index():
    with pytest.raises(InvalidInputError):
        Attempt.create(tool_call_id=ToolCallId(""))

    with pytest.raises(InvalidInputError):
        Attempt.create(tool_call_id=ToolCallId("tool-call-1"), retry_index=-1)


def test_attempt_records_success_with_start_and_end_times():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))

    attempt.start()
    attempt.succeed()

    assert attempt.status is AttemptStatus.SUCCEEDED
    assert attempt.started_at is not None
    assert attempt.ended_at is not None
    assert attempt.error is None
    assert attempt.result is None
    assert [event.type for event in attempt.pending_events] == [
        "attempt.created",
        "attempt.started",
        "attempt.completed",
    ]


def test_attempt_records_failure_error():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))

    attempt.start()
    attempt.fail({"code": "worker_error", "message": "connection refused"})

    assert attempt.status is AttemptStatus.FAILED
    assert attempt.error == {"code": "worker_error", "message": "connection refused"}
    assert attempt.ended_at is not None


def test_attempt_records_small_function_output():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))
    attempt.start()

    attempt.succeed(
        result={"status_code": 200, "items": ["error A"]},
        result_summary="one error found",
    )

    assert attempt.result == {"status_code": 200, "items": ["error A"]}
    assert attempt.result_summary == "one error found"
    assert attempt.result_ref is None


def test_attempt_records_external_function_output_reference():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))
    attempt.start()

    attempt.succeed(result_ref="s3://diagnosis/tool-output/attempt-1")

    assert attempt.result is None
    assert attempt.result_ref == "s3://diagnosis/tool-output/attempt-1"


def test_attempt_can_record_evidence_reference_once():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))
    evidence_ref = EvidenceRef(evidence_id=EvidenceId("evidence-1"), role="diagnostic")

    attempt.add_evidence(evidence_ref)
    attempt.add_evidence(evidence_ref)

    assert attempt.evidence_refs == [evidence_ref]


def test_attempt_rejects_illegal_transition_without_event():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))
    attempt.start()
    attempt.succeed()
    event_count = len(attempt.pending_events)

    with pytest.raises(InvalidTransitionError):
        attempt.start()

    assert attempt.status is AttemptStatus.SUCCEEDED
    assert len(attempt.pending_events) == event_count


def test_attempt_json_round_trip_preserves_failure_state():
    created_at = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)
    attempt = Attempt.create(
        tool_call_id=ToolCallId("tool-call-1"),
        retry_index=2,
        attempt_id="attempt-1",
        created_at=created_at,
    )
    attempt.start()
    attempt.fail({"code": "timeout", "message": "deadline exceeded"})

    restored = Attempt.from_dict(json.loads(json.dumps(attempt.to_dict())))

    assert restored == attempt
    assert restored.created_at.tzinfo == timezone.utc


def test_attempt_json_round_trip_preserves_function_output():
    attempt = Attempt.create(tool_call_id=ToolCallId("tool-call-1"))
    attempt.start()
    attempt.succeed(result={"rows": 2}, result_summary="two rows")

    restored = Attempt.from_dict(json.loads(json.dumps(attempt.to_dict())))

    assert restored == attempt
