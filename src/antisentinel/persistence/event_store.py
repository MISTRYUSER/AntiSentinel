"""Local append-only JSONL EventStore adapter."""

from __future__ import annotations

import json
from pathlib import Path

from antisentinel.domain.errors import DomainError, InvalidInputError
from antisentinel.domain.event import Event
from antisentinel.domain.primitives import require_non_empty


class FileEventStore:
    def __init__(self, storage_root: str | Path) -> None:
        self.root = Path(storage_root)

    def append(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise InvalidInputError("event must be an Event")
        path = self._path_for(event)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._read_all(path)
        for stored in existing:
            if stored.event_id != event.event_id:
                continue
            if stored.to_dict() != event.to_dict():
                raise DomainError(f"event conflict: {event.event_id}")
            return
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")

    def list_by_aggregate(self, aggregate_type: str, aggregate_id: str) -> list[Event]:
        require_non_empty(aggregate_type, "aggregate_type")
        require_non_empty(aggregate_id, "aggregate_id")
        return [
            event
            for event in self._all_events()
            if event.aggregate_type == aggregate_type and event.aggregate_id == aggregate_id
        ]

    def list_by_correlation(self, correlation_id: str) -> list[Event]:
        require_non_empty(correlation_id, "correlation_id")
        return [event for event in self._all_events() if event.correlation_id == correlation_id]

    def _path_for(self, event: Event) -> Path:
        incident_id = event.related_ids.get("incident_id")
        if not incident_id and event.aggregate_type == "Incident":
            incident_id = event.aggregate_id
        incident_id = incident_id or "unscoped"
        return self.root / "incidents" / incident_id / "events.jsonl"

    def _all_events(self) -> list[Event]:
        events: list[Event] = []
        for path in sorted((self.root / "incidents").glob("*/events.jsonl")):
            events.extend(self._read_all(path))
        return events

    @staticmethod
    def _read_all(path: Path) -> list[Event]:
        if not path.exists():
            return []
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(Event.from_dict(json.loads(line)))
        return events
