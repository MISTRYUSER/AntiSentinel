"""Rebuild derived rollout memories from durable session projections and events."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .sqlite_database import SQLiteDatabase
from .sqlite_stores import SQLiteMemoryStore


@dataclass(frozen=True)
class MemoryRebuildReport:
    sessions_seen: int
    records_written: int
    skipped: int
    errors: tuple[str, ...]


class MemoryProjectionRebuilder:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.memory = SQLiteMemoryStore(database)

    def rebuild(self) -> MemoryRebuildReport:
        seen = written = skipped = 0
        errors: list[str] = []
        sessions = self.database.query("SELECT session_id,incident_id,status,result_json FROM sessions ORDER BY session_id")
        for row in sessions:
            if row["status"] != "completed" or row["result_json"] is None:
                continue
            seen += 1
            session_id = str(row["session_id"])
            try:
                result = json.loads(row["result_json"])
                final = result.get("final") or {}
                events = self.database.query("SELECT event_id FROM events WHERE correlation_id=? ORDER BY occurred_at,event_id", (session_id,))
                source_event_ids = [str(event["event_id"]) for event in events]
                evidence_refs = [
                    {"type": "evidence", "id": str(ref.get("evidence_id")), "role": ref.get("role") or "supporting"}
                    for ref in result.get("evidence_refs", []) if ref.get("evidence_id")
                ]
                record: dict[str, Any] = {
                    "memory_id": f"rollout:{session_id}", "memory_type": "rollout",
                    "rollout_id": session_id, "incident_id": str(row["incident_id"]), "session_id": session_id,
                    "status": "active", "summary": final.get("summary"), "diagnosis": final.get("diagnosis"),
                    "content": " ".join(str(item) for item in (final.get("summary"), final.get("diagnosis")) if item),
                    "source_event_ids": source_event_ids,
                    "source_refs": [{"type": "event", "id": event_id} for event_id in source_event_ids] + evidence_refs,
                    "source_ids": [ref["id"] for ref in evidence_refs],
                    "confidence": final.get("confidence"), "content_version": 1,
                }
                existing = self.memory.get(record["memory_id"])
                self.memory.append(record)
                if existing is None:
                    written += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001 - report each corrupt projection
                errors.append(f"session {session_id}: {type(exc).__name__}: {exc}")
        return MemoryRebuildReport(seen, written, skipped, tuple(errors))
