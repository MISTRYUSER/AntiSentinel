"""Lossless BFCL question mapping into AntiSentinel message/tool shapes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AdaptedBFCLCase:
    case_id: str
    category: str
    source_hash: str
    turns: tuple[tuple[dict[str, Any], ...], ...]
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...]


def adapt_case(raw: dict[str, Any], *, category: str, source_hash: str) -> AdaptedBFCLCase:
    turns = tuple(tuple(deepcopy(message) for message in turn) for turn in raw["question"])
    messages = tuple(message for turn in turns for message in turn)
    tools = []
    for function in raw.get("function", []):
        schema = normalize_schema(deepcopy(function.get("parameters", {})))
        tools.append({"name": function["name"], "description": function.get("description", ""), "argument_schema": schema})
    return AdaptedBFCLCase(str(raw["id"]), category, source_hash, turns, messages, tuple(tools))


def normalize_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: normalize_schema(item) for key, item in value.items()}
    if "type" in result:
        result["type"] = {"dict": "object", "float": "number", "list": "array", "tuple": "array", "bool": "boolean"}.get(result["type"], result["type"])
    return result
