"""Incident aggregate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .errors import InvalidInputError, InvalidTransitionError
from .event import Event
from .ids import IncidentId, SessionId
from .primitives import decode_datetime, encode_datetime, new_stable_id, require_non_empty, require_utc, utc_now


class IncidentStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CLOSED = "closed"


@dataclass
class Incident:
    incident_id: IncidentId
    title: str
    source: str
    status: IncidentStatus
    created_at: datetime
    summary: str | None = None
    session_ids: list[SessionId] = field(default_factory=list)
    pending_events: list[Event] = field(default_factory=list, repr=False)

    @classmethod
    def create(
        cls,
        *,
        title: str,
        source: str,
        summary: str | None = None,
        incident_id: IncidentId | None = None,
        created_at: datetime | None = None,
    ) -> "Incident":
        require_non_empty(title, "title")
        require_non_empty(source, "source")
        timestamp = require_utc(created_at or utc_now(), "created_at")
        incident = cls(
            incident_id=incident_id or IncidentId(new_stable_id()),
            title=title,
            source=source,
            status=IncidentStatus.OPEN,
            created_at=timestamp,
            summary=summary,
        )
        incident._record("incident.created", {"title": title, "source": source})
        return incident

    def resolve(self) -> None:
        self._transition(IncidentStatus.OPEN, IncidentStatus.RESOLVED, "incident.resolved")

    def close(self) -> None:
        self._transition(IncidentStatus.RESOLVED, IncidentStatus.CLOSED, "incident.closed")

    def add_session(self, session_id: SessionId) -> None:
        require_non_empty(session_id, "session_id")
        if session_id not in self.session_ids:
            self.session_ids.append(session_id)

    def _transition(self, expected: IncidentStatus, target: IncidentStatus, event_type: str) -> None:
        if self.status is not expected:
            raise InvalidTransitionError(
                f"cannot transition Incident {self.incident_id} from {self.status} via {event_type}"
            )
        self.status = target
        self._record(event_type, {"status": target.value})

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.pending_events.append(
            Event.create(
                type=event_type,
                aggregate_type="Incident",
                aggregate_id=self.incident_id,
                correlation_id=self.incident_id,
                occurred_at=utc_now(),
                payload=payload,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "source": self.source,
            "status": self.status.value,
            "created_at": encode_datetime(self.created_at),
            "summary": self.summary,
            "session_ids": self.session_ids,
            "pending_events": [event.to_dict() for event in self.pending_events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Incident":
        if not isinstance(value, dict):
            raise InvalidInputError("incident payload must be an object")
        try:
            incident = cls(
                incident_id=IncidentId(require_non_empty(value["incident_id"], "incident_id")),
                title=require_non_empty(value["title"], "title"),
                source=require_non_empty(value["source"], "source"),
                status=IncidentStatus(value["status"]),
            created_at=decode_datetime(value["created_at"], "created_at"),
            summary=value.get("summary"),
            session_ids=[SessionId(item) for item in value.get("session_ids", [])],
        )
        except KeyError as exc:
            raise InvalidInputError(f"missing incident field: {exc.args[0]}") from exc

        for raw_event in value.get("pending_events", []):
            incident.pending_events.append(
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
        return incident
