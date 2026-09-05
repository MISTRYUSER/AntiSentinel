"""Task entity within a model Turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidInputError, InvalidTransitionError
from .event import Event
from .ids import TaskId, ToolCallId, TurnId
from .primitives import (
    decode_datetime,
    encode_datetime,
    new_stable_id,
    require_non_empty,
    require_utc,
    utc_now,
)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    task_id: TaskId
    turn_id: TurnId
    objective: str
    status: TaskStatus
    result_summary: str | None
    created_at: datetime
    updated_at: datetime
    tool_call_ids: list[ToolCallId] = field(default_factory=list)
    pending_events: list[Event] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        turn_id: TurnId,
        objective: str,
        task_id: TaskId | str | None = None,
        created_at: datetime | None = None,
    ) -> "Task":
        require_non_empty(turn_id, "turn_id")
        require_non_empty(objective, "objective")
        timestamp = require_utc(created_at or utc_now(), "created_at")
        task = cls(
            task_id=TaskId(task_id or new_stable_id()),
            turn_id=turn_id,
            objective=objective,
            status=TaskStatus.PENDING,
            result_summary=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        task._record("task.created", {"turn_id": turn_id, "objective": objective})
        return task

    def add_tool_call(self, tool_call_id: ToolCallId) -> None:
        require_non_empty(tool_call_id, "tool_call_id")
        if tool_call_id not in self.tool_call_ids:
            self.tool_call_ids.append(tool_call_id)
            self.updated_at = utc_now()

    def start(self) -> None:
        self._transition(TaskStatus.PENDING, TaskStatus.RUNNING, "task.started")

    def wait_for_tool(self) -> None:
        self._transition(TaskStatus.RUNNING, TaskStatus.WAITING_TOOL, "task.waiting_tool")

    def wait_for_approval(self) -> None:
        self._transition(TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL, "task.waiting_approval")

    def resume(self) -> None:
        self._transition(
            (TaskStatus.WAITING_TOOL, TaskStatus.WAITING_APPROVAL),
            TaskStatus.RUNNING,
            "task.resumed",
        )

    def succeed(self, result_summary: str | None = None) -> None:
        self._transition(
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            "task.succeeded",
            result_summary=result_summary,
        )

    def fail(self, error: dict[str, Any]) -> None:
        if not isinstance(error, dict) or not error:
            raise InvalidInputError("error must be a non-empty object")
        self._transition(
            (TaskStatus.RUNNING, TaskStatus.WAITING_TOOL, TaskStatus.WAITING_APPROVAL),
            TaskStatus.FAILED,
            "task.failed",
            error=error,
        )

    def cancel(self, reason: str) -> None:
        require_non_empty(reason, "reason")
        self._transition(
            (
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.WAITING_TOOL,
                TaskStatus.WAITING_APPROVAL,
            ),
            TaskStatus.CANCELLED,
            "task.cancelled",
            reason=reason,
        )

    def _transition(
        self,
        expected: TaskStatus | tuple[TaskStatus, ...],
        target: TaskStatus,
        event_type: str,
        **payload: Any,
    ) -> None:
        allowed = expected if isinstance(expected, tuple) else (expected,)
        if self.status not in allowed:
            raise InvalidTransitionError(
                f"cannot transition Task {self.task_id} from {self.status} via {event_type}"
            )
        self.status = target
        self.updated_at = utc_now()
        self.result_summary = payload.pop("result_summary", self.result_summary)
        self._record(event_type, {"status": target.value, **payload})

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.pending_events.append(
            Event.create(
                type=event_type,
                aggregate_type="Task",
                aggregate_id=self.task_id,
                correlation_id=self.turn_id,
                occurred_at=utc_now(),
                payload=payload,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "objective": self.objective,
            "status": self.status.value,
            "result_summary": self.result_summary,
            "created_at": encode_datetime(self.created_at),
            "updated_at": encode_datetime(self.updated_at),
            "tool_call_ids": self.tool_call_ids,
            "pending_events": [event.to_dict() for event in self.pending_events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Task":
        if not isinstance(value, dict):
            raise InvalidInputError("task payload must be an object")
        try:
            task = cls(
                task_id=TaskId(require_non_empty(value["task_id"], "task_id")),
                turn_id=TurnId(require_non_empty(value["turn_id"], "turn_id")),
                objective=require_non_empty(value["objective"], "objective"),
                status=TaskStatus(value["status"]),
                result_summary=value.get("result_summary"),
                created_at=decode_datetime(value["created_at"], "created_at"),
                updated_at=decode_datetime(value["updated_at"], "updated_at"),
                tool_call_ids=[ToolCallId(item) for item in value.get("tool_call_ids", [])],
            )
        except KeyError as exc:
            raise InvalidInputError(f"missing task field: {exc.args[0]}") from exc
        for raw_event in value.get("pending_events", []):
            task.pending_events.append(
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
        return task
