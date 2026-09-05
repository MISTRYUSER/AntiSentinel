"""Provider-neutral model contract and strict structured response parser."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.evidence import EvidenceRef
from antisentinel.domain.primitives import require_json, require_non_empty


class ModelError(Exception):
    """Base error raised by a model adapter."""


class ModelTimeoutError(ModelError):
    """Raised when a model adapter exceeds its configured timeout."""


@dataclass(frozen=True)
class PlannedToolCall:
    tool_name: str
    arguments: dict[str, Any]
    target_ref: str | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.tool_name, "tool_name")
        if not isinstance(self.arguments, dict):
            raise InvalidInputError("arguments must be an object")
        require_json(self.arguments, "arguments")
        object.__setattr__(self, "arguments", deepcopy(self.arguments))
        if self.target_ref is not None:
            require_non_empty(self.target_ref, "target_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": deepcopy(self.arguments),
            "target_ref": self.target_ref,
        }


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    objective: str
    tool_calls: tuple[PlannedToolCall, ...]

    def __post_init__(self) -> None:
        require_non_empty(self.task_id, "task_id")
        require_non_empty(self.objective, "objective")
        if not isinstance(self.tool_calls, tuple) or any(
            not isinstance(call, PlannedToolCall) for call in self.tool_calls
        ):
            raise InvalidInputError("tool_calls must contain PlannedToolCall values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
        }


@dataclass(frozen=True)
class FinalDiagnosis:
    summary: str
    diagnosis: str
    confidence: float
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        require_non_empty(self.summary, "summary")
        require_non_empty(self.diagnosis, "diagnosis")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise InvalidInputError("confidence must be a number")
        if not 0 <= self.confidence <= 1:
            raise InvalidInputError("confidence must be between 0 and 1")
        if not isinstance(self.evidence_refs, tuple) or any(
            not isinstance(ref, EvidenceRef) for ref in self.evidence_refs
        ):
            raise InvalidInputError("evidence_refs must contain EvidenceRef values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "diagnosis": self.diagnosis,
            "confidence": self.confidence,
            "evidence_refs": [
                {"evidence_id": ref.evidence_id, "role": ref.role}
                for ref in self.evidence_refs
            ],
        }


@dataclass(frozen=True)
class ModelRequest:
    incident_id: str
    session_id: str
    turn_id: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]

    def __post_init__(self) -> None:
        require_non_empty(self.incident_id, "incident_id")
        require_non_empty(self.session_id, "session_id")
        require_non_empty(self.turn_id, "turn_id")
        require_json(self.messages, "messages")
        require_json(self.tools, "tools")
        object.__setattr__(self, "messages", deepcopy(self.messages))
        object.__setattr__(self, "tools", deepcopy(self.tools))


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    tool_tokens: int = 0

    def __post_init__(self) -> None:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in self.__dict__.values()):
            raise InvalidInputError("token usage values must be non-negative integers")

    def add(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
            self.tool_tokens + other.tool_tokens,
        )


@dataclass(frozen=True)
class ModelResponse:
    final: FinalDiagnosis | None = None
    tasks: tuple[TaskPlan, ...] = ()
    raw_summary: str = ""
    usage: TokenUsage = TokenUsage()

    def __post_init__(self) -> None:
        if (self.final is None) == (not self.tasks):
            raise InvalidInputError("model response must contain exactly one final or tasks branch")
        if any(not isinstance(task, TaskPlan) for task in self.tasks):
            raise InvalidInputError("tasks must contain TaskPlan values")

    def to_dict(self) -> dict[str, Any]:
        if self.final is not None:
            return {"final": self.final.to_dict()}
        return {"tasks": [task.to_dict() for task in self.tasks]}


class ModelPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one strict, structured response for a model request."""


def _object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidInputError(f"{field_name} must be an object")
    return value


def _required(value: dict[str, Any], field_name: str) -> Any:
    if field_name not in value:
        raise InvalidInputError(f"missing model field: {field_name}")
    return value[field_name]


def _parse_evidence_refs(value: object) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, list):
        raise InvalidInputError("evidence_refs must be an array")
    refs: list[EvidenceRef] = []
    for item in value:
        if isinstance(item, str):
            refs.append(EvidenceRef(evidence_id=require_non_empty(item, "evidence_id")))
            continue
        raw = _object(item, "evidence_ref")
        refs.append(
            EvidenceRef(
                evidence_id=require_non_empty(_required(raw, "evidence_id"), "evidence_id"),
                role=raw.get("role"),
            )
        )
    return tuple(refs)


def parse_model_response(
    value: object,
    *,
    max_tasks: int = 8,
    max_tool_calls: int = 8,
    usage: TokenUsage | None = None,
) -> ModelResponse:
    raw = _object(value, "model response")
    has_final = "final" in raw
    has_tasks = "tasks" in raw
    if has_final == has_tasks:
        raise InvalidInputError("model response must contain exactly one of final or tasks")

    if has_final:
        final = _object(raw["final"], "final")
        confidence = _required(final, "confidence")
        diagnosis = FinalDiagnosis(
            summary=require_non_empty(_required(final, "summary"), "summary"),
            diagnosis=require_non_empty(_required(final, "diagnosis"), "diagnosis"),
            confidence=confidence,
            evidence_refs=_parse_evidence_refs(_required(final, "evidence_refs")),
        )
        return ModelResponse(final=diagnosis, raw_summary=str(raw)[:2000], usage=usage or TokenUsage())

    tasks_value = raw["tasks"]
    if not isinstance(tasks_value, list):
        raise InvalidInputError("tasks must be an array")
    if not 1 <= max_tasks <= 8 or len(tasks_value) > max_tasks:
        raise InvalidInputError("model response exceeds max_tasks")
    tasks: list[TaskPlan] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(tasks_value):
        task = _object(item, f"tasks[{index}]")
        task_id = require_non_empty(_required(task, "task_id"), "task_id")
        if task_id in seen_ids:
            raise InvalidInputError(f"duplicate task_id: {task_id}")
        seen_ids.add(task_id)
        calls_value = _required(task, "tool_calls")
        if not isinstance(calls_value, list) or len(calls_value) > max_tool_calls:
            raise InvalidInputError("task tool_calls exceeds max_tool_calls")
        calls: list[PlannedToolCall] = []
        for call_index, item_call in enumerate(calls_value):
            call = _object(item_call, f"tasks[{index}].tool_calls[{call_index}]")
            calls.append(
                PlannedToolCall(
                    tool_name=require_non_empty(_required(call, "tool_name"), "tool_name"),
                    arguments=_object(_required(call, "arguments"), "arguments"),
                    target_ref=call.get("target_ref"),
                )
            )
        tasks.append(
            TaskPlan(
                task_id=task_id,
                objective=require_non_empty(_required(task, "objective"), "objective"),
                tool_calls=tuple(calls),
            )
        )
    if not tasks:
        raise InvalidInputError("tasks must not be empty")
    return ModelResponse(tasks=tuple(tasks), raw_summary=str(raw)[:2000], usage=usage or TokenUsage())
