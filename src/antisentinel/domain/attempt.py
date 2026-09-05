"""Tool execution attempt entity."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidInputError, InvalidTransitionError
from .event import Event
from .evidence import EvidenceRef
from .ids import AttemptId, ToolCallId
from .primitives import (
    decode_datetime,
    encode_datetime,
    new_stable_id,
    require_json,
    require_non_empty,
    require_utc,
    utc_now,
)


class AttemptStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class Attempt:
    attempt_id: AttemptId
    tool_call_id: ToolCallId
    retry_index: int
    status: AttemptStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: dict[str, Any] | None = None
    result: Any = None
    result_ref: str | None = None
    result_summary: str | None = None
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    pending_events: list[Event] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        tool_call_id: ToolCallId,
        retry_index: int = 0,
        attempt_id: AttemptId | str | None = None,
        created_at: datetime | None = None,
    ) -> "Attempt":
        require_non_empty(tool_call_id, "tool_call_id")
        if not isinstance(retry_index, int) or isinstance(retry_index, bool) or retry_index < 0:
            raise InvalidInputError("retry_index must be a non-negative integer")
        timestamp = require_utc(created_at or utc_now(), "created_at")
        attempt = cls(
            attempt_id=AttemptId(attempt_id or new_stable_id()),
            tool_call_id=tool_call_id,
            retry_index=retry_index,
            status=AttemptStatus.CREATED,
            created_at=timestamp,
            updated_at=timestamp,
        )
        attempt._record("attempt.created", {"tool_call_id": tool_call_id, "retry_index": retry_index})
        return attempt

    def add_evidence(self, evidence_ref: EvidenceRef) -> None:
        if not isinstance(evidence_ref, EvidenceRef):
            raise InvalidInputError("evidence_ref must be an EvidenceRef")
        if evidence_ref not in self.evidence_refs:
            self.evidence_refs.append(evidence_ref)
            self.updated_at = utc_now()

    def start(self) -> None:
        self._transition(AttemptStatus.CREATED, AttemptStatus.RUNNING, "attempt.started")
        self.started_at = self.updated_at

    def succeed(
        self,
        *,
        result: Any = None,
        result_ref: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        if result_ref is not None:
            require_non_empty(result_ref, "result_ref")
        require_json(result, "result")
        self._complete(
            AttemptStatus.SUCCEEDED,
            "succeeded",
            result=deepcopy(result),
            result_ref=result_ref,
            result_summary=result_summary,
        )

    def fail(self, error: dict[str, Any]) -> None:
        self._complete(AttemptStatus.FAILED, "failed", error=error)

    def time_out(self, error: dict[str, Any]) -> None:
        self._complete(AttemptStatus.TIMED_OUT, "timed_out", error=error)

    def _complete(
        self,
        target: AttemptStatus,
        result_code: str,
        error: dict[str, Any] | None = None,
        **output: Any,
    ) -> None:
        if error is not None:
            if not isinstance(error, dict) or not error:
                raise InvalidInputError("error must be a non-empty object")
            require_json(error, "error")
        self._transition(
            AttemptStatus.RUNNING,
            target,
            "attempt.completed",
            result_status=result_code,
            error=deepcopy(error),
            **output,
        )
        self.ended_at = self.updated_at

    def _transition(
        self,
        expected: AttemptStatus | tuple[AttemptStatus, ...],
        target: AttemptStatus,
        event_type: str,
        **payload: Any,
    ) -> None:
        allowed = expected if isinstance(expected, tuple) else (expected,)
        if self.status not in allowed:
            raise InvalidTransitionError(
                f"cannot transition Attempt {self.attempt_id} from {self.status} via {event_type}"
            )
        self.status = target
        self.updated_at = utc_now()
        self.error = payload.get("error", self.error)
        self.result = payload.get("result", self.result)
        self.result_ref = payload.get("result_ref", self.result_ref)
        self.result_summary = payload.get("result_summary", self.result_summary)
        self._record(event_type, {"status": target.value, **payload})

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.pending_events.append(
            Event.create(
                type=event_type,
                aggregate_type="Attempt",
                aggregate_id=self.attempt_id,
                correlation_id=self.tool_call_id,
                occurred_at=utc_now(),
                payload=payload,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "tool_call_id": self.tool_call_id,
            "retry_index": self.retry_index,
            "status": self.status.value,
            "created_at": encode_datetime(self.created_at),
            "updated_at": encode_datetime(self.updated_at),
            "started_at": encode_datetime(self.started_at) if self.started_at else None,
            "ended_at": encode_datetime(self.ended_at) if self.ended_at else None,
            "error": self.error,
            "result": self.result,
            "result_ref": self.result_ref,
            "result_summary": self.result_summary,
            "evidence_refs": [
                {"evidence_id": ref.evidence_id, "role": ref.role} for ref in self.evidence_refs
            ],
            "pending_events": [event.to_dict() for event in self.pending_events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Attempt":
        if not isinstance(value, dict):
            raise InvalidInputError("attempt payload must be an object")
        try:
            attempt = cls(
                attempt_id=AttemptId(require_non_empty(value["attempt_id"], "attempt_id")),
                tool_call_id=ToolCallId(require_non_empty(value["tool_call_id"], "tool_call_id")),
                retry_index=value["retry_index"],
                status=AttemptStatus(value["status"]),
                created_at=decode_datetime(value["created_at"], "created_at"),
                updated_at=decode_datetime(value["updated_at"], "updated_at"),
                started_at=(
                    decode_datetime(value["started_at"], "started_at")
                    if value.get("started_at")
                    else None
                ),
                ended_at=(
                    decode_datetime(value["ended_at"], "ended_at")
                    if value.get("ended_at")
                    else None
                ),
                error=deepcopy(value.get("error")),
                result=deepcopy(require_json(value.get("result"), "result")),
                result_ref=value.get("result_ref"),
                result_summary=value.get("result_summary"),
                evidence_refs=[
                    EvidenceRef(evidence_id=item["evidence_id"], role=item.get("role"))
                    for item in value.get("evidence_refs", [])
                ],
            )
        except KeyError as exc:
            raise InvalidInputError(f"missing attempt field: {exc.args[0]}") from exc
        for raw_event in value.get("pending_events", []):
            attempt.pending_events.append(
                Event(
                    event_id=raw_event["event_id"],
                    type=raw_event["type"],
                    aggregate_type=raw_event["aggregate_type"],
                    aggregate_id=raw_event["aggregate_id"],
                    correlation_id=raw_event["correlation_id"],
                    occurred_at=decode_datetime(raw_event["occurred_at"], "occurred_at"),
                    payload=raw_event["payload"],
                )
            )
        return attempt
