"""Durable application projections for projects, sessions, and conversation results."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class FileApplicationStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "application"
        self._lock = Lock()

    def save_incident(self, incident: dict[str, Any]) -> None:
        self._write(self.root / "incidents" / f"{incident['incident_id']}.json", incident)

    def save_session(self, session: dict[str, Any]) -> None:
        self._write(self.root / "sessions" / f"{session['session_id']}.json", session)

    def save_result(self, session_id: str, result: dict[str, Any]) -> None:
        self._write(self.root / "results" / f"{session_id}.json", result)

    def load_incidents(self) -> list[dict[str, Any]]:
        return self._read_many(self.root / "incidents")

    def load_sessions(self) -> list[dict[str, Any]]:
        return self._read_many(self.root / "sessions")

    def load_result(self, session_id: str) -> dict[str, Any] | None:
        return self._read(self.root / "results" / f"{session_id}.json")

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def _read_many(cls, directory: Path) -> list[dict[str, Any]]:
        return [value for path in sorted(directory.glob("*.json")) if (value := cls._read(path)) is not None]
