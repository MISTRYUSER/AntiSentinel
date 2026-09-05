"""Provider-neutral tool definitions and execution results."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import inspect
import types
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

from antisentinel.domain.evidence import Evidence
from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.primitives import require_json, require_non_empty


ToolStatus = Literal["succeeded", "failed", "rejected", "waiting_approval"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    argument_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    requires_approval: bool = False
    read_only: bool = True

    def __post_init__(self) -> None:
        require_non_empty(self.name, "name")
        require_non_empty(self.description, "description")
        if not callable(self.handler):
            raise InvalidInputError("handler must be callable")
        if not isinstance(self.argument_schema, dict):
            raise InvalidInputError("argument_schema must be an object")
        require_json(self.argument_schema, "argument_schema")
        object.__setattr__(self, "argument_schema", deepcopy(self.argument_schema))

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "argument_schema": deepcopy(self.argument_schema),
            "requires_approval": self.requires_approval,
            "read_only": self.read_only,
        }


@dataclass(frozen=True)
class ToolExecutionResult:
    status: ToolStatus
    result: Any = None
    result_summary: str | None = None
    evidence: Evidence | None = None
    error: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "rejected", "waiting_approval"}:
            raise InvalidInputError(f"unsupported tool result status: {self.status}")
        if self.status == "succeeded":
            require_json(self.result, "result")
        if self.error is not None:
            require_json(self.error, "error")
        if self.evidence is not None and not isinstance(self.evidence, Evidence):
            raise InvalidInputError("evidence must be an Evidence")


def tool(
    function: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    requires_approval: bool = False,
    read_only: bool = True,
) -> Callable[..., Any] | ToolDefinition:
    """Turn a typed function into a discoverable ToolDefinition."""

    def decorate(handler: Callable[..., Any]) -> ToolDefinition:
        if not callable(handler):
            raise InvalidInputError("tool decorator requires a callable")
        return ToolDefinition(
            name=name or handler.__name__,
            description=description or inspect.getdoc(handler) or handler.__name__,
            argument_schema=infer_argument_schema(handler),
            handler=handler,
            requires_approval=requires_approval,
            read_only=read_only,
        )

    return decorate(function) if function is not None else decorate


def infer_argument_schema(handler: Callable[..., Any]) -> dict[str, Any]:
    try:
        hints = get_type_hints(handler)
    except (NameError, TypeError) as exc:
        raise InvalidInputError(f"cannot resolve type hints for {handler.__name__}") from exc
    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in inspect.signature(handler).parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise InvalidInputError("tool arguments cannot use *args or **kwargs")
        annotation = hints.get(parameter.name, Any)
        properties[parameter.name] = _schema_for_type(annotation)
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _schema_for_type(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (types.UnionType, Union):
        non_none = [item for item in args if item is not type(None)]
        if len(non_none) == 1:
            schema = _schema_for_type(non_none[0])
            schema["nullable"] = True
            return schema
    if annotation is Any:
        return {}
    mapping = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}
    if annotation in mapping:
        return {"type": mapping[annotation]}
    if origin in mapping:
        return {"type": mapping[origin]}
    raise InvalidInputError(f"unsupported tool argument type: {annotation}")
