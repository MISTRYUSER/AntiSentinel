"""Deterministic projection from canonical Events."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from antisentinel.domain.event import Event


class EventReplayProjector:
    """Rebuild a small query projection without depending on mutable runtime state."""

    def project(self, events: Iterable[Event]) -> dict[str, object]:
        unique: dict[str, Event] = {}
        for event in events:
            if not isinstance(event, Event):
                raise TypeError("events must contain Event values")
            event_id = str(event.event_id)
            previous = unique.get(event_id)
            if previous is not None and previous.to_dict() != event.to_dict():
                raise ValueError(f"event conflict: {event_id}")
            unique[event_id] = event
        ordered = sorted(unique.values(), key=lambda item: (item.occurred_at, str(item.event_id)))
        status = "unknown"
        for event in ordered:
            candidate = event.payload.get("status")
            if isinstance(candidate, str) and candidate:
                status = candidate
            elif event.type.endswith(".completed"):
                status = "completed"
            elif event.type.endswith(".failed"):
                status = "failed"
        return {
            "status": status,
            "event_count": len(ordered),
            "event_types": dict(Counter(event.type for event in ordered)),
            "last_event_id": str(ordered[-1].event_id) if ordered else None,
            "last_occurred_at": ordered[-1].occurred_at.isoformat() if ordered else None,
        }
