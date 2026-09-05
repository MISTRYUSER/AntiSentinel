"""Serializable runtime checkpoints and an in-memory checkpoint store."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.primitives import require_json, require_non_empty


@dataclass
class RuntimeSnapshot:
    session_id: str
    incident: dict[str, Any]
    session: dict[str, Any]
    turns: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    attempts: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    turn_count: int
    current_task_index: int | None
    current_tool_call_index: int | None
    successful_tool_call_ids: tuple[str, ...]
    last_error: dict[str, Any] | None
    pending_results: list[dict[str, Any]] = field(default_factory=list)
    completed_invocations: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty(self.session_id, "session_id")
        if not isinstance(self.turn_count, int) or isinstance(self.turn_count, bool) or self.turn_count < 0:
            raise InvalidInputError("turn_count must be a non-negative integer")
        if not isinstance(self.successful_tool_call_ids, tuple):
            self.successful_tool_call_ids = tuple(self.successful_tool_call_ids)
        require_json(self.to_dict(), "checkpoint")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["successful_tool_call_ids"] = list(self.successful_tool_call_ids)
        return deepcopy(value)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RuntimeSnapshot":
        if not isinstance(value, dict):
            raise InvalidInputError("checkpoint must be an object")
        required = {
            "session_id", "incident", "session", "turns", "tasks", "tool_calls",
            "attempts", "messages", "turn_count", "current_task_index",
            "current_tool_call_index", "successful_tool_call_ids", "last_error",
            "pending_results", "completed_invocations",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise InvalidInputError(f"missing checkpoint fields: {', '.join(missing)}")
        return cls(
            session_id=value["session_id"],
            incident=deepcopy(value["incident"]),
            session=deepcopy(value["session"]),
            turns=deepcopy(value["turns"]),
            tasks=deepcopy(value["tasks"]),
            tool_calls=deepcopy(value["tool_calls"]),
            attempts=deepcopy(value["attempts"]),
            messages=deepcopy(value["messages"]),
            turn_count=value["turn_count"],
            current_task_index=value["current_task_index"],
            current_tool_call_index=value["current_tool_call_index"],
            successful_tool_call_ids=tuple(value["successful_tool_call_ids"]),
            last_error=deepcopy(value["last_error"]),
            pending_results=deepcopy(value["pending_results"]),
            completed_invocations=deepcopy(value["completed_invocations"]),
        )


class CheckpointStore(Protocol):
    def save(self, snapshot: RuntimeSnapshot) -> None:
        """Persist the latest runtime snapshot for a Session."""

    def load(self, session_id: str) -> RuntimeSnapshot | None:
        """Load the latest snapshot, if one exists."""

    def clear(self, session_id: str) -> None:
        """Remove a Session checkpoint."""


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, Any]] = {}

    def save(self, snapshot: RuntimeSnapshot) -> None:
        if not isinstance(snapshot, RuntimeSnapshot):
            raise InvalidInputError("snapshot must be a RuntimeSnapshot")
        self._snapshots[snapshot.session_id] = deepcopy(snapshot.to_dict())

    def load(self, session_id: str) -> RuntimeSnapshot | None:
        require_non_empty(session_id, "session_id")
        value = self._snapshots.get(session_id)
        return RuntimeSnapshot.from_dict(deepcopy(value)) if value is not None else None

    def clear(self, session_id: str) -> None:
        require_non_empty(session_id, "session_id")
        self._snapshots.pop(session_id, None)
