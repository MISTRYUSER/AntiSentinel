"""Short-lived, event-derived Session timeline tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from antisentinel.domain.event import Event


@dataclass(frozen=True)
class TimelineNode:
    node_id: str
    session_id: str
    node_type: str
    aggregate_id: str
    parent_node_id: str | None
    event_type: str
    occurred_at: datetime
    summary: str
    related_ids: dict[str, str] = field(default_factory=dict)


class SessionTimelineTree:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._nodes: dict[str, TimelineNode] = {}

    def append_event(self, event: Event) -> TimelineNode | None:
        if event.event_id in self._nodes:
            return self._nodes[event.event_id]
        related = {**dict(event.related_ids), **dict(event.payload)}
        if related.get("session_id") != self.session_id and event.aggregate_id != self.session_id:
            return None
        parent_id = None
        for key in ("tool_call_id", "task_id", "turn_id", "session_id"):
            value = related.get(key)
            if value:
                parent_id = self._find_related(key, value)
                if parent_id is not None:
                    break
        node = TimelineNode(
            node_id=event.event_id,
            session_id=self.session_id,
            node_type=event.aggregate_type.lower(),
            aggregate_id=event.aggregate_id,
            parent_node_id=parent_id,
            event_type=event.type,
            occurred_at=event.occurred_at,
            summary=str(event.payload.get("summary") or event.type),
            related_ids=related,
        )
        self._nodes[node.node_id] = node
        return node

    def children(self, parent_node_id: str | None = None) -> list[TimelineNode]:
        return sorted(
            (node for node in self._nodes.values() if node.parent_node_id == parent_node_id),
            key=lambda node: node.occurred_at,
        )

    def nodes(self) -> list[TimelineNode]:
        return sorted(self._nodes.values(), key=lambda node: node.occurred_at)

    def rebuild(self, events: list[Event]) -> None:
        self._nodes.clear()
        for event in events:
            self.append_event(event)

    def digest(self, token_budget: int = 1200) -> dict[str, Any]:
        from antisentinel.worker.runtime.budget import estimate_tokens

        lines: list[str] = []
        used = 0
        for node in self.nodes():
            line = f"{node.node_type}: {node.summary}"
            cost = estimate_tokens(line)
            if used + cost > token_budget:
                break
            lines.append(line)
            used += cost
        return {"session_id": self.session_id, "summary": "\n".join(lines), "node_count": len(self._nodes)}

    def _find_related(self, relation: str, value: str) -> str | None:
        for node in self._nodes.values():
            if node.aggregate_id == value or node.related_ids.get(relation) == value:
                return node.node_id
        return None
