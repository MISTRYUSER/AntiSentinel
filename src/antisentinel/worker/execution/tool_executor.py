"""Registry-backed, provider-neutral tool executor."""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass

from antisentinel.domain.errors import InvalidInputError
from antisentinel.tools.manifest import ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolExecutionScope:
    allowed_tools: frozenset[str]
    read_only_mode: bool = True
    allow_approval: bool = False


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def preflight(self, tool_name: str, arguments: dict[str, Any], *, scope: ToolExecutionScope | None = None) -> ToolExecutionResult | None:
        definition = self.registry.resolve(tool_name)
        if definition is None:
            return ToolExecutionResult(
                status="rejected",
                error={"code": "unknown_tool", "message": f"tool is not registered: {tool_name}"},
            )
        if scope is not None and tool_name not in scope.allowed_tools:
            return ToolExecutionResult(status="rejected", error={"code": "tool_not_disclosed", "message": f"tool is not available this turn: {tool_name}"})
        if scope is not None and scope.read_only_mode and not definition.read_only:
            return ToolExecutionResult(status="rejected", error={"code": "read_only_required", "message": f"tool is not read-only: {tool_name}"})
        if scope is not None and not scope.allow_approval and definition.requires_approval:
            return ToolExecutionResult(status="rejected", error={"code": "approval_not_allowed", "message": f"tool requires approval: {tool_name}"})
        errors = self.registry.validate_arguments(definition, arguments)
        if errors:
            return ToolExecutionResult(
                status="rejected",
                error={"code": "invalid_arguments", "message": "; ".join(errors)},
            )
        return None

    def execute(self, tool_name: str, arguments: dict[str, Any], *, scope: ToolExecutionScope | None = None) -> ToolExecutionResult:
        rejected = self.preflight(tool_name, arguments, scope=scope)
        if rejected is not None:
            return rejected
        definition = self.registry.resolve(tool_name)
        assert definition is not None
        if definition.requires_approval:
            return ToolExecutionResult(
                status="waiting_approval",
                error={"code": "approval_required", "tool_name": tool_name},
            )
        try:
            output = definition.handler(dict(arguments))
            if isinstance(output, ToolExecutionResult):
                return output
            return ToolExecutionResult(
                status="succeeded",
                result=output,
                result_summary=_summarize(output),
            )
        except Exception as exc:  # noqa: BLE001 - normalize adapter failures at the port boundary
            return ToolExecutionResult(
                status="failed",
                error={"code": "tool_execution_failed", "message": str(exc)},
                result_summary=str(exc),
            )


def _summarize(value: Any) -> str:
    if value is None:
        return "tool completed without a result"
    text = str(value)
    return text[:1000]
