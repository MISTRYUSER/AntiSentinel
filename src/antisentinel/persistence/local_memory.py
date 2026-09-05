"""Local-first long-term memory facade."""

from __future__ import annotations

from typing import Any

from .sqlite_database import SQLiteDatabase
from .sqlite_stores import SQLiteMemoryStore


class LocalLongTermMemory:
    """Durable local Memory interface; cache and vector indexes stay optional."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.store = SQLiteMemoryStore(database)

    def remember(self, record: dict[str, Any]) -> dict[str, Any]:
        self.store.append(record)
        return self.store.get(record["memory_id"]) or record

    def get(self, memory_id: str) -> dict[str, Any] | None:
        return self.store.get(memory_id)

    def search(self, query: str, *, operator_id: str | None = None, incident_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.search(query, operator_id=operator_id, incident_id=incident_id, limit=limit)
