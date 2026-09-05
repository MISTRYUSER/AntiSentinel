"""Append-only durable conversation message storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class FileConversationStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "application" / "messages"
        self._lock = Lock()

    def append(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        message = {"session_id": session_id, "role": role, "content": content, "created_at": datetime.now(timezone.utc).isoformat()}
        path = self.root / f"{session_id}.jsonl"
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        return message

    def load(self, session_id: str) -> list[dict[str, Any]]:
        path = self.root / f"{session_id}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
