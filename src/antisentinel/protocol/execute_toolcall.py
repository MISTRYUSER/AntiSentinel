"""Control-to-Worker ToolCall protocol skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True)
class ExecuteToolCallRequest:
    protocol_version: str
    request_id: str
    trace_id: str
    incident_id: str
    task_id: str
    step_id: str
    attempt_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    target_ref: str
    idempotency_key: str
    deadline_at: datetime
    actor: str
    policy_context: dict[str, Any]


@dataclass(frozen=True)
class ExecuteToolCallResponse:
    request_id: str
    tool_call_id: str
    status: Literal[
        "succeeded",
        "failed",
        "denied",
        "waiting_approval",
        "timed_out",
        "unknown",
    ]
    evidence_refs: list[str]
    execution_receipt: dict[str, Any] | None
    error: dict[str, Any] | None
    metrics: dict[str, Any]
