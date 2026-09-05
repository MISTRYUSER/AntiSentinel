"""Exact-name registry and small JSON schema validator for tools."""

from __future__ import annotations

from typing import Any

from antisentinel.domain.errors import InvalidInputError
from .manifest import ToolDefinition


class ToolRegistry:
    @classmethod
    def discover(cls, package_name: str = "antisentinel.tools") -> "ToolRegistry":
        from .discovery import ToolDiscovery

        return ToolDiscovery.discover(package_name)

    def __init__(self, *, package_name: str | None = None, auto_discover: bool = True) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        if auto_discover:
            from .discovery import ToolDiscovery

            discovered = ToolDiscovery.discover(package_name or "antisentinel.tools")
            self._tools.update(discovered._tools)

    def register(self, definition: ToolDefinition) -> None:
        if not isinstance(definition, ToolDefinition):
            raise InvalidInputError("definition must be a ToolDefinition")
        if definition.name in self._tools:
            raise InvalidInputError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def resolve(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def manifests(self) -> list[dict[str, Any]]:
        return [self._tools[name].manifest() for name in sorted(self._tools)]

    def validate_arguments(self, definition: ToolDefinition, arguments: object) -> list[str]:
        schema = definition.argument_schema
        if not isinstance(arguments, dict):
            return ["arguments must be an object"]
        if schema.get("type") not in (None, "object"):
            return ["root schema must be an object"]
        errors: list[str] = []
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in arguments:
                errors.append(f"missing required argument: {key}")
        if schema.get("additionalProperties") is False:
            errors.extend(f"unexpected argument: {key}" for key in arguments if key not in properties)
        for key, rule in properties.items():
            if key not in arguments:
                continue
            if not isinstance(rule, dict):
                errors.append(f"invalid schema for argument: {key}")
                continue
            expected = rule.get("type")
            if expected and not _matches_type(arguments[key], expected):
                errors.append(f"argument {key} must be {expected}")
        return errors


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return False
