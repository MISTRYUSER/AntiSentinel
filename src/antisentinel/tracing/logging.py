"""Safe JSONL runtime logging."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from .telemetry import current_trace_context


class SafeJsonFormatter(logging.Formatter):
    _blocked = ("api_key", "auth_token", "access_token", "password", "secret", "raw_tool_output", "evidence_content")

    def format(self, record: logging.LogRecord) -> str:
        standard = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__)
        value = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = current_trace_context()
        if context is not None:
            value.update({key: item for key, item in {
                "trace_id": context.trace_id, "session_id": context.session_id,
                "turn_id": context.turn_id, "request_id": context.request_id,
                "parent_request_id": context.parent_request_id,
            }.items() if item is not None})
        for key, item in record.__dict__.items():
            if key.startswith("_") or key in standard or any(blocked in key.lower() for blocked in self._blocked):
                continue
            if isinstance(item, (str, int, float, bool)) or item is None:
                value[key] = item
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def configure_runtime_logging(path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(SafeJsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
