"""In-process, session-scoped runtime event bus for SSE consumers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from threading import Condition
from time import monotonic
from typing import Any, Iterator

from antisentinel.domain.event import Event


@dataclass(frozen=True)
class RuntimeStreamEvent:
    sequence: int
    event: Event

    def sse(self) -> str:
        payload = dict(self.event.payload)
        payload.update({"event_id": self.event.event_id, "event_type": self.event.type})
        return f"id: {self.sequence}\nevent: {self.event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class RuntimeEventBus:
    def __init__(self, *, max_events_per_session: int = 256) -> None:
        self.max_events_per_session = max_events_per_session
        self._events: dict[str, list[RuntimeStreamEvent]] = {}
        self._terminal: set[str] = set()
        self._sequence = 0
        self._condition = Condition()

    def publish(self, session_id: str, event: Event) -> None:
        with self._condition:
            self._sequence += 1
            events = self._events.setdefault(session_id, [])
            events.append(RuntimeStreamEvent(self._sequence, event))
            del events[:-self.max_events_per_session]
            if event.type in {"diagnosis.completed", "runtime.failed"}:
                self._terminal.add(session_id)
            self._condition.notify_all()

    def mark_terminal(self, session_id: str) -> None:
        with self._condition:
            self._terminal.add(session_id)
            self._condition.notify_all()

    def stream(
        self,
        session_id: str,
        *,
        last_event_id: str | None = None,
        timeout: float = 30.0,
    ) -> Iterator[str]:
        cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        while True:
            with self._condition:
                deadline = monotonic() + timeout
                while True:
                    available = [item for item in self._events.get(session_id, []) if item.sequence > cursor]
                    if available or session_id in self._terminal:
                        break
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        yield ": keep-alive\n\n"
                        break
                    self._condition.wait(remaining)
                available = [item for item in self._events.get(session_id, []) if item.sequence > cursor]
                terminal = session_id in self._terminal
            for item in available:
                cursor = item.sequence
                yield item.sse()
            if terminal and not available:
                return
