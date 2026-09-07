"""Snapshot-free parsed-file fact cache for incremental code-map builds."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
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
