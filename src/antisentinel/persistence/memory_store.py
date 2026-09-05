"""Durable append-only store for structured long-term memory records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from antisentinel.domain.errors import InvalidInputError
from antisentinel.memory.models import MemoryRecord


class FileMemoryStore:
    def __init__(self, storage_root: str | Path) -> None:
        self.root = Path(storage_root)
        self.path = self.root / "memory" / "records.jsonl"

    def append(self, record: dict[str, Any]) -> None:
        if not isinstance(record, dict) or not record.get("memory_id"):
            raise InvalidInputError("memory record must contain memory_id")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._read()
        if any(item.get("memory_id") == record["memory_id"] for item in existing):
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def append_record(self, record: MemoryRecord) -> None:
        self.append(record.to_dict())

    def replace(self, record: dict[str, Any]) -> None:
        if not isinstance(record, dict) or not record.get("memory_id"):
            raise InvalidInputError("memory record must contain memory_id")
        records = self._read()
        replaced = False
        for index, item in enumerate(records):
            if item.get("memory_id") == record["memory_id"]:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in records), encoding="utf-8")

    def replace_record(self, record: MemoryRecord) -> None:
        self.replace(record.to_dict())

    def list_by_operator(self, operator_id: str) -> list[dict[str, Any]]:
        return [item for item in self._read() if item.get("operator_id") == operator_id]

    def get(self, memory_id: str) -> dict[str, Any] | None:
        return next((item for item in self._read() if item.get("memory_id") == memory_id), None)

    def get_record(self, memory_id: str) -> MemoryRecord | None:
        record = self.get(memory_id)
        return MemoryRecord.from_legacy_or_dict(record) if record is not None else None

    def list_by_type(self, memory_type: str) -> list[dict[str, Any]]:
        return [item for item in self._read() if item.get("memory_type") == memory_type]

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class _CacheEntry:
    def __init__(self, value: Any, version: int, expires_at: float) -> None:
        self.value = value
        self.version = version
        self.expires_at = expires_at


class InMemoryMemoryCache:
    def __init__(self) -> None:
        self._values: dict[str, _CacheEntry] = {}

    def get(self, key: str, version: int | None = None) -> _CacheEntry | None:
        import time
        entry = self._values.get(key)
        if entry is None or entry.expires_at <= time.monotonic() or (version is not None and entry.version != version):
            self._values.pop(key, None)
            return None
        return entry

    def set(self, key: str, value: Any, *, ttl_seconds: int, version: int) -> None:
        import time
        self._values[key] = _CacheEntry(value, version, time.monotonic() + ttl_seconds)

    def delete(self, key: str) -> None:
        self._values.pop(key, None)
