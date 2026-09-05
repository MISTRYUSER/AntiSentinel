"""Conversation session aggregate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidInputError, InvalidTransitionError
from .event import Event
from .ids import IncidentId, SessionId, TurnId
from .primitives import (
    decode_datetime,
    encode_datetime,
    new_stable_id,
    require_non_empty,
    require_utc,
    utc_now,
)


class SessionStatus(StrEnum):
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Session:
    session_id: SessionId
    incident_id: IncidentId
    participant_ids: list[str]
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    turn_ids: list[TurnId] = field(default_factory=list)
    pending_events: list[Event] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        incident_id: IncidentId,
        participant_ids: list[str],
        session_id: SessionId | str | None = None,
        created_at: datetime | None = None,
    ) -> "Session":
        require_non_empty(incident_id, "incident_id")
        if not participant_ids or any(not isinstance(item, str) or not item.strip() for item in participant_ids):
            raise InvalidInputError("participant_ids must contain at least one non-empty ID")
        timestamp = require_utc(created_at or utc_now(), "created_at")
        session = cls(
            session_id=SessionId(session_id or new_stable_id()),
            incident_id=incident_id,
            participant_ids=list(participant_ids),
            status=SessionStatus.ACTIVE,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session._record("session.created", {"incident_id": incident_id})
        return session

    def add_turn(self, turn_id: TurnId) -> None:
        require_non_empty(turn_id, "turn_id")
        if turn_id not in self.turn_ids:
            self.turn_ids.append(turn_id)
            self.updated_at = utc_now()

    def wait(self) -> None:
        self._transition(SessionStatus.ACTIVE, SessionStatus.WAITING, "session.waiting")

    def complete(self, summary: str | None = None) -> None:
        self._transition(
            (SessionStatus.ACTIVE, SessionStatus.WAITING),
            SessionStatus.COMPLETED,
            "session.completed",
            summary=summary,
        )

    def fail(self, error: dict[str, Any]) -> None:
        if not isinstance(error, dict) or not error:
            raise InvalidInputError("error must be a non-empty object")
        self._transition(
            (SessionStatus.ACTIVE, SessionStatus.WAITING),
            SessionStatus.FAILED,
            "session.failed",
            error=error,
        )

    def _transition(
        self,
        expected: SessionStatus | tuple[SessionStatus, ...],
        target: SessionStatus,
        event_type: str,
        **payload: Any,
    ) -> None:
        allowed = expected if isinstance(expected, tuple) else (expected,)
        if self.status not in allowed:
            raise InvalidTransitionError(
                f"cannot transition Session {self.session_id} from {self.status} via {event_type}"
            )
        self.status = target
        self.updated_at = utc_now()
        if payload.get("summary") is not None:
            self.summary = payload["summary"]
        self._record(event_type, {"status": target.value, **payload})

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.pending_events.append(
            Event.create(
                type=event_type,
                aggregate_type="Session",
                aggregate_id=self.session_id,
                correlation_id=self.session_id,
                occurred_at=utc_now(),
                payload=payload,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "incident_id": self.incident_id,
            "participant_ids": self.participant_ids,
            "status": self.status.value,
            "created_at": encode_datetime(self.created_at),
            "updated_at": encode_datetime(self.updated_at),
            "summary": self.summary,
            "turn_ids": self.turn_ids,
            "pending_events": [event.to_dict() for event in self.pending_events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Session":
        if not isinstance(value, dict):
            raise InvalidInputError("session payload must be an object")
        try:
            session = cls(
                session_id=SessionId(require_non_empty(value["session_id"], "session_id")),
                incident_id=IncidentId(require_non_empty(value["incident_id"], "incident_id")),
                participant_ids=list(value["participant_ids"]),
                status=SessionStatus(value["status"]),
                created_at=decode_datetime(value["created_at"], "created_at"),
                updated_at=decode_datetime(value["updated_at"], "updated_at"),
                summary=value.get("summary"),
                turn_ids=[TurnId(item) for item in value.get("turn_ids", [])],
            )
        except KeyError as exc:
            raise InvalidInputError(f"missing session field: {exc.args[0]}") from exc
        for raw_event in value.get("pending_events", []):
            session.pending_events.append(
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
        return session
