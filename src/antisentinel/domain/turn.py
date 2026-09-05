"""Model Turn entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidInputError, InvalidTransitionError
from .event import Event
from .ids import SessionId, TaskId, TurnId
from .primitives import (
    decode_datetime,
    encode_datetime,
    new_stable_id,
    require_non_empty,
    require_utc,
    utc_now,
)


class TurnStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelOutputKind(StrEnum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    APPROVAL_REQUEST = "approval_request"
    FINAL_ANSWER = "final_answer"


@dataclass
class Turn:
    turn_id: TurnId
    session_id: SessionId
    context_summary: str | None
    model_output_kind: ModelOutputKind | None
    output_summary: str | None
    status: TurnStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    task_ids: list[TaskId] = field(default_factory=list)
    pending_events: list[Event] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        session_id: SessionId,
        context_summary: str | None = None,
        turn_id: TurnId | str | None = None,
        created_at: datetime | None = None,
    ) -> "Turn":
        require_non_empty(session_id, "session_id")
        timestamp = require_utc(created_at or utc_now(), "created_at")
        turn = cls(
            turn_id=TurnId(turn_id or new_stable_id()),
            session_id=session_id,
            context_summary=context_summary,
            model_output_kind=None,
            output_summary=None,
            status=TurnStatus.PENDING,
            created_at=timestamp,
            updated_at=timestamp,
        )
        turn._record("turn.created", {"session_id": session_id})
        return turn

    def add_task(self, task_id: TaskId) -> None:
        require_non_empty(task_id, "task_id")
        if task_id not in self.task_ids:
            self.task_ids.append(task_id)
            self.updated_at = utc_now()

    def start(self) -> None:
        if self.status is not TurnStatus.PENDING:
            raise InvalidTransitionError(
                f"cannot transition Turn {self.turn_id} from {self.status} via turn.started"
            )
        self.status = TurnStatus.RUNNING
        self.started_at = utc_now()
        self.updated_at = self.started_at
        self._record("turn.started", {})

    def wait_for_tool(self) -> None:
        self._transition(TurnStatus.RUNNING, TurnStatus.WAITING_TOOL, "turn.waiting_tool")

    def complete(self, output_kind: ModelOutputKind | str, output_summary: str | None = None) -> None:
        try:
            kind = ModelOutputKind(output_kind)
        except ValueError as exc:
            raise InvalidInputError(f"unsupported model output kind: {output_kind}") from exc
        self._transition(
            (TurnStatus.RUNNING, TurnStatus.WAITING_TOOL),
            TurnStatus.COMPLETED,
            "turn.completed",
            model_output_kind=kind,
            output_summary=output_summary,
        )

    def fail(self, error: dict[str, Any]) -> None:
        if not isinstance(error, dict) or not error:
            raise InvalidInputError("error must be a non-empty object")
        self._transition(
            (TurnStatus.RUNNING, TurnStatus.WAITING_TOOL),
            TurnStatus.FAILED,
            "turn.failed",
            error=error,
        )

    def _transition(
        self,
        expected: TurnStatus | tuple[TurnStatus, ...],
        target: TurnStatus,
        event_type: str,
        **payload: Any,
    ) -> None:
        allowed = expected if isinstance(expected, tuple) else (expected,)
        if self.status not in allowed:
            raise InvalidTransitionError(
                f"cannot transition Turn {self.turn_id} from {self.status} via {event_type}"
            )
        self.status = target
        self.updated_at = utc_now()
        self.model_output_kind = payload.pop("model_output_kind", self.model_output_kind)
        self.output_summary = payload.pop("output_summary", self.output_summary)
        if target in (TurnStatus.COMPLETED, TurnStatus.FAILED):
            self.completed_at = self.updated_at
        self._record(event_type, {"status": target.value, **payload})

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.pending_events.append(
            Event.create(
                type=event_type,
                aggregate_type="Turn",
                aggregate_id=self.turn_id,
                correlation_id=self.session_id,
                occurred_at=utc_now(),
                payload=payload,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "context_summary": self.context_summary,
            "model_output_kind": self.model_output_kind.value if self.model_output_kind else None,
            "output_summary": self.output_summary,
            "status": self.status.value,
            "created_at": encode_datetime(self.created_at),
            "updated_at": encode_datetime(self.updated_at),
            "started_at": encode_datetime(self.started_at) if self.started_at else None,
            "completed_at": encode_datetime(self.completed_at) if self.completed_at else None,
            "task_ids": self.task_ids,
            "pending_events": [event.to_dict() for event in self.pending_events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Turn":
        if not isinstance(value, dict):
            raise InvalidInputError("turn payload must be an object")
        try:
            turn = cls(
                turn_id=TurnId(require_non_empty(value["turn_id"], "turn_id")),
                session_id=SessionId(require_non_empty(value["session_id"], "session_id")),
                context_summary=value.get("context_summary"),
                model_output_kind=(
                    ModelOutputKind(value["model_output_kind"])
                    if value.get("model_output_kind")
                    else None
                ),
                output_summary=value.get("output_summary"),
                status=TurnStatus(value["status"]),
                created_at=decode_datetime(value["created_at"], "created_at"),
                updated_at=decode_datetime(value["updated_at"], "updated_at"),
                started_at=(
                    decode_datetime(value["started_at"], "started_at")
                    if value.get("started_at")
                    else None
                ),
                completed_at=(
                    decode_datetime(value["completed_at"], "completed_at")
                    if value.get("completed_at")
                    else None
                ),
                task_ids=[TaskId(item) for item in value.get("task_ids", [])],
            )
        except KeyError as exc:
            raise InvalidInputError(f"missing turn field: {exc.args[0]}") from exc
        for raw_event in value.get("pending_events", []):
            turn.pending_events.append(
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
        return turn
