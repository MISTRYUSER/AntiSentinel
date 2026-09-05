"""Tool call entity within a Task."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidInputError, InvalidTransitionError
from .event import Event
from .ids import AttemptId, TaskId, ToolCallId
from .primitives import (
    decode_datetime,
    encode_datetime,
    new_stable_id,
    require_json,
    require_non_empty,
    require_utc,
    utc_now,
)


class ToolCallStatus(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


@dataclass
class ToolCall:
    tool_call_id: ToolCallId
    task_id: TaskId
    tool_name: str
    arguments: Any
    target: str | None
    status: ToolCallStatus
    created_at: datetime
    updated_at: datetime
    attempt_ids: list[AttemptId] = field(default_factory=list)
    pending_events: list[Event] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        task_id: TaskId,
        tool_name: str,
        arguments: Any,
        target: str | None = None,
        tool_call_id: ToolCallId | str | None = None,
        created_at: datetime | None = None,
    ) -> "ToolCall":
        require_non_empty(task_id, "task_id")
        require_non_empty(tool_name, "tool_name")
        require_json(arguments, "arguments")
        timestamp = require_utc(created_at or utc_now(), "created_at")
        tool_call = cls(
            tool_call_id=ToolCallId(tool_call_id or new_stable_id()),
            task_id=task_id,
            tool_name=tool_name,
            arguments=deepcopy(arguments),
            target=target,
            status=ToolCallStatus.REQUESTED,
            created_at=timestamp,
            updated_at=timestamp,
        )
        tool_call._record(
            "tool_call.requested",
            {"task_id": task_id, "tool_name": tool_name, "target": target},
        )
        return tool_call

    def add_attempt(self, attempt_id: AttemptId) -> None:
        require_non_empty(attempt_id, "attempt_id")
        if attempt_id not in self.attempt_ids:
            self.attempt_ids.append(attempt_id)
            self.updated_at = utc_now()

    def start(self) -> None:
        self._transition(ToolCallStatus.REQUESTED, ToolCallStatus.RUNNING, "tool_call.started")

    def succeed(self) -> None:
        self._transition(ToolCallStatus.RUNNING, ToolCallStatus.SUCCEEDED, "tool_call.succeeded")

    def fail(self, error: dict[str, Any]) -> None:
        if not isinstance(error, dict) or not error:
            raise InvalidInputError("error must be a non-empty object")
        self._transition(ToolCallStatus.RUNNING, ToolCallStatus.FAILED, "tool_call.failed", error=error)

    def deny(self, reason: str) -> None:
        require_non_empty(reason, "reason")
        self._transition(
            (ToolCallStatus.REQUESTED, ToolCallStatus.RUNNING),
            ToolCallStatus.DENIED,
            "tool_call.denied",
            reason=reason,
        )

    def time_out(self, reason: str) -> None:
        require_non_empty(reason, "reason")
        self._transition(ToolCallStatus.RUNNING, ToolCallStatus.TIMED_OUT, "tool_call.timed_out", reason=reason)

    def _transition(
        self,
        expected: ToolCallStatus | tuple[ToolCallStatus, ...],
        target: ToolCallStatus,
        event_type: str,
        **payload: Any,
    ) -> None:
        allowed = expected if isinstance(expected, tuple) else (expected,)
        if self.status not in allowed:
            raise InvalidTransitionError(
                f"cannot transition ToolCall {self.tool_call_id} from {self.status} via {event_type}"
            )
        self.status = target
        self.updated_at = utc_now()
        self._record(event_type, {"status": target.value, **payload})

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.pending_events.append(
            Event.create(
                type=event_type,
                aggregate_type="ToolCall",
                aggregate_id=self.tool_call_id,
                correlation_id=self.task_id,
                occurred_at=utc_now(),
                payload=payload,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "target": self.target,
            "status": self.status.value,
            "created_at": encode_datetime(self.created_at),
            "updated_at": encode_datetime(self.updated_at),
            "attempt_ids": self.attempt_ids,
            "pending_events": [event.to_dict() for event in self.pending_events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolCall":
        if not isinstance(value, dict):
            raise InvalidInputError("tool call payload must be an object")
        try:
            tool_call = cls(
                tool_call_id=ToolCallId(require_non_empty(value["tool_call_id"], "tool_call_id")),
                task_id=TaskId(require_non_empty(value["task_id"], "task_id")),
                tool_name=require_non_empty(value["tool_name"], "tool_name"),
                arguments=deepcopy(require_json(value["arguments"], "arguments")),
                target=value.get("target"),
                status=ToolCallStatus(value["status"]),
                created_at=decode_datetime(value["created_at"], "created_at"),
                updated_at=decode_datetime(value["updated_at"], "updated_at"),
                attempt_ids=[AttemptId(item) for item in value.get("attempt_ids", [])],
            )
        except KeyError as exc:
            raise InvalidInputError(f"missing tool call field: {exc.args[0]}") from exc
        for raw_event in value.get("pending_events", []):
            tool_call.pending_events.append(
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
        return tool_call
