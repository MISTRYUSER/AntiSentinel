"""Registry-backed, provider-neutral tool executor."""

from __future__ import annotations

from typing import Any

from antisentinel.domain.errors import InvalidInputError
from antisentinel.tools.manifest import ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        definition = self.registry.resolve(tool_name)
        if definition is None:
            return ToolExecutionResult(
                status="rejected",
                error={"code": "unknown_tool", "message": f"tool is not registered: {tool_name}"},
            )
        errors = self.registry.validate_arguments(definition, arguments)
        if errors:
            return ToolExecutionResult(
                status="rejected",
                error={"code": "invalid_arguments", "message": "; ".join(errors)},
            )
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
