"""Snapshot-free parsed-file fact cache for incremental code-map builds."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any


@dataclass(frozen=True)
class AstFactKey:
    blob_sha256: str
    path: str
    module_root: str
    parser_revision: str
    rules_digest: str


class AstFactCache:
    def __init__(self) -> None:
        self._facts: dict[AstFactKey, Any] = {}

    def get(self, key: AstFactKey):
        value = self._facts.get(key)
        return deepcopy(value) if value is not None else None

    def put(self, key: AstFactKey, value: Any) -> None:
        self._facts[key] = deepcopy(value)


def normalize_semantics(output: Any) -> Any:
    value = asdict(output) if is_dataclass(output) else deepcopy(output)
    if isinstance(value, dict):
        value.pop("cache_hits", None)
        for key in ("created_at", "published_at"):
            value.pop(key, None)
        return {key: normalize_semantics(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return tuple(normalize_semantics(item) for item in value)
    return value
