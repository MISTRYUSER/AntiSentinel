"""Local JSON StateStore adapter for rebuildable projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.primitives import require_non_empty


class FileStateStore:
    def __init__(self, storage_root: str | Path) -> None:
        self.root = Path(storage_root)

    def save_projection(self, scope_id: str, state: dict[str, Any]) -> None:
        require_non_empty(scope_id, "scope_id")
        if not isinstance(state, dict):
            raise InvalidInputError("state must be an object")
        path = self.root / "incidents" / scope_id / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    def load_projection(self, scope_id: str) -> dict[str, Any] | None:
        require_non_empty(scope_id, "scope_id")
        path = self.root / "incidents" / scope_id / "state.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise InvalidInputError("stored projection must be an object")
        return value

    def mark_projection_lag(self, scope_id: str, error: str) -> None:
        require_non_empty(error, "error")
        current = self.load_projection(scope_id) or {}
        current["projection_lag"] = {"error": error}
        self.save_projection(scope_id, current)
