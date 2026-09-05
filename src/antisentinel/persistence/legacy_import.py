"""Idempotent import from the legacy JSON/JSONL storage layout."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from antisentinel.domain.event import Event
from antisentinel.domain.evidence import Evidence

from .sqlite_database import SQLiteDatabase
from .sqlite_stores import (
    SQLiteApplicationStore,
    SQLiteConversationStore,
    SQLiteEventStore,
    SQLiteEvidenceStore,
    SQLiteMemoryStore,
    SQLiteStateStore,
)


@dataclass(frozen=True)
class ImportReport:
    scanned: int
    inserted: int
    skipped: int
    failed: int
    errors: tuple[str, ...]


class LegacyImporter:
    def __init__(self, source_root: str | Path, database: SQLiteDatabase) -> None:
        self.source_root = Path(source_root)
        self.database = database
        self.application = SQLiteApplicationStore(database)
        self.conversations = SQLiteConversationStore(database)
        self.events = SQLiteEventStore(database)
        self.evidence = SQLiteEvidenceStore(database)
        self.states = SQLiteStateStore(database)
        self.memories = SQLiteMemoryStore(database)
        self._scanned = 0
        self._inserted = 0
        self._skipped = 0
        self._errors: list[str] = []

    def run(self) -> ImportReport:
        self._scanned = self._inserted = self._skipped = 0
        self._errors = []
        self._import_json_directory("application/incidents", "incident", self.application.save_incident, "incident_id")
        self._import_json_directory("application/sessions", "session", self.application.save_session, "session_id")
        self._import_json_directory("application/results", "result", self._save_result, "session_id")
        self._import_message_files()
        self._import_event_files()
        self._import_evidence_files()
        self._import_state_files()
        self._import_memory_file()
        self._import_trace_file()
        return ImportReport(
            scanned=self._scanned,
            inserted=self._inserted,
            skipped=self._skipped,
            failed=len(self._errors),
            errors=tuple(self._errors),
        )

    def _import_json_directory(
        self,
        relative: str,
        kind: str,
        save: Callable[[dict[str, Any]], None],
        id_key: str,
    ) -> None:
        for path in sorted((self.source_root / relative).glob("*.json")):
            self._process(path, kind, json.loads(path.read_text(encoding="utf-8")), save, id_key=id_key)

    def _save_result(self, value: dict[str, Any]) -> None:
        self.application.save_result(str(value["session_id"]), value)

    def _import_message_files(self) -> None:
        directory = self.source_root / "application" / "messages"
        for path in sorted(directory.glob("*.jsonl")):
            for line_number, value in self._jsonl(path):
                self._process(
                    path, "message", value,
                    lambda item: self.conversations.append(str(item["session_id"]), item["role"], item["content"]),
                    id_key=None, line_number=line_number,
                )

    def _import_event_files(self) -> None:
        for path in sorted((self.source_root / "incidents").glob("*/events.jsonl")):
            for line_number, value in self._jsonl(path):
                self._process(path, "event", value, lambda item: self.events.append(Event.from_dict(item)), id_key="event_id", line_number=line_number)

    def _import_evidence_files(self) -> None:
        for path in sorted((self.source_root / "incidents").glob("*/evidence/*.json")):
            incident_id = path.parents[1].name
            self._process(
                path, "evidence", json.loads(path.read_text(encoding="utf-8")),
                lambda item, scope=incident_id: self.evidence.put_once(Evidence.from_dict(item), incident_id=scope),
                id_key="evidence_id",
            )

    def _import_state_files(self) -> None:
        for path in sorted((self.source_root / "incidents").glob("*/state.json")):
            scope_id = path.parent.name
            self._process(
                path, "state", json.loads(path.read_text(encoding="utf-8")),
                lambda item, scope=scope_id: self.states.save_projection(scope, item),
                id_key=None, stable_id=scope_id,
            )

    def _import_memory_file(self) -> None:
        path = self.source_root / "memory" / "records.jsonl"
        for line_number, value in self._jsonl(path):
            self._process(path, "memory", value, self.memories.append, id_key="memory_id", line_number=line_number)

    def _import_trace_file(self) -> None:
        path = self.source_root / "observability" / "traces.jsonl"
        for line_number, value in self._jsonl(path):
            attributes = value.get("attributes") or {}
            stable_id = str(value.get("span_id", f"line-{line_number}"))
            self._process(
                path, "span", value, self._save_span,
                id_key=None, stable_id=stable_id, line_number=line_number,
            )

    def _save_span(self, value: dict[str, Any]) -> None:
        attributes = dict(value.get("attributes") or {})
        trace_id = str(attributes.get("trace_id") or value["trace_id"])
        span_id = str(value["span_id"])
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO traces(trace_id,incident_id,session_id,started_at_ns,ended_at_ns,attributes_json)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    started_at_ns=MIN(COALESCE(traces.started_at_ns,excluded.started_at_ns),excluded.started_at_ns),
                    ended_at_ns=MAX(COALESCE(traces.ended_at_ns,excluded.ended_at_ns),excluded.ended_at_ns)
                """,
                (
                    trace_id, attributes.get("incident_id"), attributes.get("session_id"),
                    value.get("start_time_ns"), value.get("end_time_ns"), self._canonical(attributes),
                ),
            )
            connection.execute(
                """
                INSERT INTO spans(span_id,trace_id,parent_span_id,session_id,turn_id,name,start_time_ns,end_time_ns,attributes_json)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(span_id) DO NOTHING
                """,
                (
                    span_id, trace_id, attributes.get("parent_span_id"), attributes.get("session_id"),
                    attributes.get("turn_id"), value["name"], value["start_time_ns"],
                    value.get("end_time_ns"), self._canonical(attributes),
                ),
            )

    def _process(
        self,
        path: Path,
        kind: str,
        value: dict[str, Any],
        save: Callable[[dict[str, Any]], None],
        *,
        id_key: str | None,
        stable_id: str | None = None,
        line_number: int | None = None,
    ) -> None:
        self._scanned += 1
        source_path = str(path.relative_to(self.source_root)) + (f":{line_number}" if line_number is not None else "")
        encoded = self._canonical(value)
        fingerprint = sha256(f"{source_path}\n{encoded}".encode("utf-8")).hexdigest()
        if self.database.query("SELECT 1 FROM import_ledger WHERE fingerprint=?", (fingerprint,)):
            self._skipped += 1
            return
        try:
            resolved_id = stable_id or (str(value[id_key]) if id_key else None)
            save(value)
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO import_ledger(fingerprint,source_path,record_kind,stable_id) VALUES(?,?,?,?)",
                    (fingerprint, source_path, kind, resolved_id),
                )
            self._inserted += 1
        except Exception as exc:  # noqa: BLE001 - one bad legacy record must be reported precisely
            self._errors.append(f"{source_path}: {type(exc).__name__}: {exc}")

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _jsonl(path: Path):
        if not path.exists():
            return
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                yield line_number, json.loads(line)
