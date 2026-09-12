"""SQLite adapters for the existing persistence interfaces."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from antisentinel.domain.errors import DomainError, InvalidInputError
from antisentinel.domain.event import Event
from antisentinel.domain.evidence import Evidence
from antisentinel.domain.primitives import require_non_empty
from antisentinel.memory.models import MemoryRecord
from antisentinel.memory.models import AuthorizedMemoryScope
from antisentinel.memory.hybrid_ranker import RetrievalCandidate

from .sqlite_database import SQLiteDatabase


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteApplicationStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def save_incident(self, incident: dict[str, Any]) -> None:
        created_at = str(incident["created_at"])
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO incidents(incident_id,title,summary,source,status,payload_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    title=excluded.title, summary=excluded.summary, source=excluded.source,
                    status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (
                    str(incident["incident_id"]), incident["title"], incident.get("summary"),
                    incident["source"], incident["status"], _json(incident), created_at,
                    str(incident.get("updated_at", created_at)),
                ),
            )

    def save_session(self, session: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(session_id,incident_id,status,payload_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    incident_id=excluded.incident_id, status=excluded.status,
                    payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (
                    str(session["session_id"]), str(session["incident_id"]), session["status"],
                    _json(session), str(session["created_at"]), str(session["updated_at"]),
                ),
            )

    def save_result(self, session_id: str, result: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET result_json=?, updated_at=? WHERE session_id=?",
                (_json(result), _now(), session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)

    def load_incidents(self) -> list[dict[str, Any]]:
        return [json.loads(row["payload_json"]) for row in self.database.query("SELECT payload_json FROM incidents ORDER BY created_at, incident_id")]

    def load_sessions(self) -> list[dict[str, Any]]:
        return [json.loads(row["payload_json"]) for row in self.database.query("SELECT payload_json FROM sessions ORDER BY created_at, session_id")]

    def load_result(self, session_id: str) -> dict[str, Any] | None:
        rows = self.database.query("SELECT result_json FROM sessions WHERE session_id=?", (session_id,))
        if not rows or rows[0]["result_json"] is None:
            return None
        return json.loads(rows[0]["result_json"])


class SQLiteConversationStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def append(self, session_id: str, role: str, content: str) -> dict[str, Any]:
        require_non_empty(session_id, "session_id")
        require_non_empty(role, "role")
        require_non_empty(content, "content")
        message = {"session_id": session_id, "role": role, "content": content, "created_at": _now()}
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO messages(session_id,role,content,created_at) VALUES(?,?,?,?)",
                (session_id, role, content, message["created_at"]),
            )
        return message

    def load(self, session_id: str) -> list[dict[str, Any]]:
        return [
            {"session_id": row["session_id"], "role": row["role"], "content": row["content"], "created_at": row["created_at"]}
            for row in self.database.query(
                "SELECT session_id,role,content,created_at FROM messages WHERE session_id=? ORDER BY message_id",
                (session_id,),
            )
        ]


class SQLiteEventStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def append(self, event: Event) -> None:
        if not isinstance(event, Event):
            raise InvalidInputError("event must be an Event")
        record = event.to_dict()
        encoded = _json(record)
        rows = self.database.query("SELECT record_json FROM events WHERE event_id=?", (str(event.event_id),))
        if rows:
            if rows[0]["record_json"] != encoded:
                raise DomainError(f"event conflict: {event.event_id}")
            return
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO events(event_id,aggregate_type,aggregate_id,correlation_id,event_type,occurred_at,payload_json,record_json)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    str(event.event_id), event.aggregate_type, event.aggregate_id, event.correlation_id,
                    event.type, event.occurred_at.isoformat(), _json(dict(event.payload)), encoded,
                ),
            )

    def list_by_aggregate(self, aggregate_type: str, aggregate_id: str) -> list[Event]:
        require_non_empty(aggregate_type, "aggregate_type")
        require_non_empty(aggregate_id, "aggregate_id")
        rows = self.database.query(
            "SELECT record_json FROM events WHERE aggregate_type=? AND aggregate_id=? ORDER BY occurred_at,event_id",
            (aggregate_type, aggregate_id),
        )
        return [Event.from_dict(json.loads(row["record_json"])) for row in rows]

    def list_by_correlation(self, correlation_id: str) -> list[Event]:
        require_non_empty(correlation_id, "correlation_id")
        rows = self.database.query(
            "SELECT record_json FROM events WHERE correlation_id=? ORDER BY occurred_at,event_id",
            (correlation_id,),
        )
        return [Event.from_dict(json.loads(row["record_json"])) for row in rows]


class SQLiteEvidenceStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def put_once(self, evidence: Evidence, *, incident_id: str | None = None) -> None:
        if not isinstance(evidence, Evidence):
            raise InvalidInputError("evidence must be an Evidence")
        encoded = _json(evidence.to_dict())
        rows = self.database.query("SELECT record_json FROM evidence WHERE evidence_id=?", (str(evidence.evidence_id),))
        if rows:
            if rows[0]["record_json"] != encoded:
                raise DomainError(f"evidence hash conflict: {evidence.evidence_id}")
            return
        preview = evidence.content_ref[:500]
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evidence(evidence_id,incident_id,content_hash,content_uri,media_type,byte_count,preview,record_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(evidence.evidence_id), incident_id or evidence.metadata.get("incident_id"),
                    evidence.content_hash, evidence.content_ref, evidence.kind, None, preview,
                    encoded, evidence.recorded_at.isoformat(),
                ),
            )

    def get(self, evidence_id: str) -> Evidence | None:
        rows = self.database.query("SELECT record_json FROM evidence WHERE evidence_id=?", (evidence_id,))
        return Evidence.from_dict(json.loads(rows[0]["record_json"])) if rows else None


class SQLiteStateStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def save_projection(self, scope_id: str, state: dict[str, Any]) -> None:
        require_non_empty(scope_id, "scope_id")
        if not isinstance(state, dict):
            raise InvalidInputError("state must be an object")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO states(scope_id,state_json,updated_at) VALUES(?,?,?)
                ON CONFLICT(scope_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at
                """,
                (scope_id, _json(state), _now()),
            )

    def load_projection(self, scope_id: str) -> dict[str, Any] | None:
        require_non_empty(scope_id, "scope_id")
        rows = self.database.query("SELECT state_json FROM states WHERE scope_id=?", (scope_id,))
        return json.loads(rows[0]["state_json"]) if rows else None

    def mark_projection_lag(self, scope_id: str, error: str) -> None:
        require_non_empty(error, "error")
        state = self.load_projection(scope_id) or {}
        state["projection_lag"] = {"error": error}
        self.save_projection(scope_id, state)


class SQLiteMemoryStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    @staticmethod
    def _validate(record: dict[str, Any]) -> None:
        from antisentinel.memory.models import require_scope_id

        if not isinstance(record, dict) or not record.get("memory_id"):
            raise InvalidInputError("memory record must contain memory_id")
        if record.get("operator_id") is not None:
            require_scope_id("operator_id", str(record["operator_id"]))

    def append(self, record: dict[str, Any]) -> None:
        self._validate(record)
        existing = self.get(record["memory_id"])
        if existing is not None:
            if existing != record:
                raise DomainError(f"memory conflict: {record['memory_id']}")
            return
        timestamp = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(memory_id,memory_type,operator_id,incident_id,session_id,entity_key,status,confidence,content_version,record_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                self._values(record, timestamp, timestamp),
            )
            self._sync_search_index(connection, record)

    def append_record(self, record: MemoryRecord) -> None:
        self.append(record.to_dict())

    def append_many(self, records: list[dict[str, Any]]) -> None:
        unique_records: dict[str, dict[str, Any]] = {}
        for record in records:
            self._validate(record)
            previous = unique_records.get(record["memory_id"])
            if previous is not None and previous != record:
                raise DomainError(f"memory conflict: {record['memory_id']}")
            unique_records[record["memory_id"]] = record
        records = list(unique_records.values())
        timestamp = _now()
        with self.database.transaction() as connection:
            new_records: list[dict[str, Any]] = []
            for record in records:
                existing = connection.execute("SELECT record_json FROM memory_records WHERE memory_id=?", (record["memory_id"],)).fetchone()
                if existing is not None:
                    if existing["record_json"] != _json(record):
                        raise DomainError(f"memory conflict: {record['memory_id']}")
                    continue
                new_records.append(record)
            connection.executemany(
                "INSERT INTO memory_records(memory_id,memory_type,operator_id,incident_id,session_id,entity_key,status,confidence,content_version,record_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [self._values(record, timestamp, timestamp) for record in new_records],
            )
            connection.executemany(
                "INSERT INTO memory_records_fts(memory_id,searchable_text) VALUES(?,?)",
                [(record["memory_id"], " ".join(str(record.get(key, "")) for key in ("content", "subject", "predicate", "object") if record.get(key))) for record in new_records],
            )

    def replace(self, record: dict[str, Any]) -> None:
        self._validate(record)
        existing = self.database.query("SELECT created_at FROM memory_records WHERE memory_id=?", (record["memory_id"],))
        created_at = existing[0]["created_at"] if existing else _now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(memory_id,memory_type,operator_id,incident_id,session_id,entity_key,status,confidence,content_version,record_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    memory_type=excluded.memory_type,operator_id=excluded.operator_id,
                    incident_id=excluded.incident_id,session_id=excluded.session_id,
                    entity_key=excluded.entity_key,status=excluded.status,confidence=excluded.confidence,
                    content_version=excluded.content_version,record_json=excluded.record_json,updated_at=excluded.updated_at
                """,
                self._values(record, created_at, _now()),
            )
            self._sync_search_index(connection, record)

    def replace_record(self, record: MemoryRecord) -> None:
        self.replace(record.to_dict())

    def get(self, memory_id: str) -> dict[str, Any] | None:
        rows = self.database.query("SELECT record_json FROM memory_records WHERE memory_id=?", (memory_id,))
        return json.loads(rows[0]["record_json"]) if rows else None

    def get_record(self, memory_id: str) -> MemoryRecord | None:
        record = self.get(memory_id)
        return MemoryRecord.from_legacy_or_dict(record) if record is not None else None

    def list_by_operator(self, operator_id: str) -> list[dict[str, Any]]:
        from antisentinel.memory.models import require_scope_id

        require_scope_id("operator_id", operator_id)
        return self._list("operator_id=?", (operator_id,))

    def list_by_scope(self, scope, *, include_operator_wide: bool = True) -> list[dict[str, Any]]:
        from antisentinel.memory.models import require_scope_id
        from antisentinel.memory.scope_filter import filter_records

        require_scope_id("operator_id", scope.operator_id)
        where = ["operator_id=?"]
        params: list[Any] = [scope.operator_id]
        if scope.incident_id is not None:
            if include_operator_wide:
                where.append("(incident_id IS NULL OR incident_id=?)")
            else:
                where.append("incident_id=?")
            params.append(scope.incident_id)
        if scope.memory_type is not None:
            where.append("memory_type=?")
            params.append(scope.memory_type)
        return filter_records(self._list(" AND ".join(where), tuple(params)), scope, include_operator_wide=include_operator_wide)

    def list_by_type(self, memory_type: str) -> list[dict[str, Any]]:
        return self._list("memory_type=?", (memory_type,))

    def search(self, query: str, *, operator_id: str, incident_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        from antisentinel.memory.models import require_scope_id

        require_scope_id("operator_id", operator_id)
        if not query.strip():
            return []
        if limit < 1:
            raise ValueError("limit must be positive")
        where = ["m.status = 'active'", "m.operator_id = ?"]
        params: list[Any] = [operator_id]
        if incident_id is not None:
            where.append("(m.incident_id IS NULL OR m.incident_id = ?)"); params.append(incident_id)
        filter_sql = " AND ".join(where)
        rows = self.database.query(
            f"""
            SELECT m.record_json, -bm25(memory_records_fts) AS search_score
            FROM memory_records_fts JOIN memory_records m ON m.memory_id = memory_records_fts.memory_id
            WHERE memory_records_fts.searchable_text MATCH ? AND {filter_sql}
            ORDER BY bm25(memory_records_fts), m.confidence DESC, m.memory_id LIMIT ?
            """,
            [self._fts_query(query), *params, limit],
        )
        if not rows:
            if re.search(r"[\u4e00-\u9fff]", query):
                tokens = [query.strip()]
            else:
                tokens = [token for token in re.findall(r"[a-z0-9]{3,}", query.lower()) if token not in {"what", "where", "when", "which", "who", "how", "the", "are", "was", "is"}]
            if not tokens:
                return []
            like_sql = " OR ".join("m.record_json LIKE ?" for _ in tokens)
            rows = self.database.query(
                f"SELECT m.record_json, 0.0 AS search_score FROM memory_records m WHERE {filter_sql} AND ({like_sql}) ORDER BY m.confidence DESC,m.memory_id LIMIT ?",
                [*params, *[f"%{token}%" for token in tokens], limit],
            )
        return [{**json.loads(row["record_json"]), "search_score": float(row["search_score"])} for row in rows]

    def _list(self, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = self.database.query(f"SELECT record_json FROM memory_records WHERE {where} ORDER BY created_at,memory_id", params)
        return [json.loads(row["record_json"]) for row in rows]

    @staticmethod
    def _sync_search_index(connection, record: dict[str, Any]) -> None:
        connection.execute("DELETE FROM memory_records_fts WHERE memory_id=?", (record["memory_id"],))
        searchable = " ".join(str(record.get(key, "")) for key in ("content", "subject", "predicate", "object") if record.get(key))
        connection.execute("INSERT INTO memory_records_fts(memory_id,searchable_text) VALUES(?,?)", (record["memory_id"], searchable))

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", query.lower())
        return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens) or '""'

    @staticmethod
    def _values(record: dict[str, Any], created_at: str, updated_at: str) -> tuple[Any, ...]:
        entity_key = record.get("entity_key") or (
            f"{record.get('subject')}:{record.get('predicate')}" if record.get("subject") and record.get("predicate") else None
        )
        return (
            record["memory_id"], record.get("memory_type", "unknown"), record.get("operator_id"),
            record.get("incident_id"), record.get("session_id") or record.get("rollout_id"),
            entity_key, record.get("status", "active"), record.get("confidence"),
            int(record.get("content_version", 1)), _json(record), created_at, updated_at,
        )


class SQLiteMemoryCandidateStore:
    """Scope-filtered lexical and exact-identifier candidate channels."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database
        self.memory = SQLiteMemoryStore(database)
        self.channel_status: dict[str, str] = {"lexical": "active", "identifier": "active"}

    def lexical_candidates(self, query: str, scope: AuthorizedMemoryScope, *, limit: int) -> tuple[RetrievalCandidate, ...]:
        try:
            rows = self.memory.search(query, operator_id=scope.operator_id, incident_id=scope.incident_id, limit=limit)
        except Exception:
            self.channel_status["lexical"] = "bypass"
            return ()
        self.channel_status["lexical"] = "active"
        return tuple(RetrievalCandidate(str(row["memory_id"]), "lexical", rank, float(row.get("search_score", 0.0))) for rank, row in enumerate(rows, 1))

    def identifier_candidates(self, query: str, scope: AuthorizedMemoryScope, *, limit: int) -> tuple[RetrievalCandidate, ...]:
        identifiers = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b|\b[a-zA-Z][\w-]*(?::[\w-]+)+\b", query)
        if not identifiers:
            return ()
        rows: dict[str, dict[str, Any]] = {}
        try:
            for identifier in identifiers:
                for row in self.memory.search(identifier, operator_id=scope.operator_id, incident_id=scope.incident_id, limit=limit):
                    rows[str(row["memory_id"])] = row
        except Exception:
            self.channel_status["identifier"] = "bypass"
            return ()
        self.channel_status["identifier"] = "active"
        ordered = sorted(rows.values(), key=lambda row: (-float(row.get("search_score", 0.0)), str(row["memory_id"])))[:limit]
        return tuple(RetrievalCandidate(str(row["memory_id"]), "identifier", rank, float(row.get("search_score", 0.0))) for rank, row in enumerate(ordered, 1))


class SQLiteEvaluationStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def save_run(self, run_id: str, config: dict[str, Any], *, status: str = "running", summary: dict[str, Any] | None = None) -> None:
        timestamp = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs(run_id,status,config_json,summary_json,created_at,completed_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,summary_json=excluded.summary_json,completed_at=excluded.completed_at
                """,
                (run_id, status, _json(config), _json(summary) if summary is not None else None, timestamp, timestamp if status == "completed" else None),
            )

    def save_case(self, run_id: str, case: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_cases(case_id,run_id,incident_id,session_id,query,cutoff_at,ground_truth_json)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(case_id) DO UPDATE SET ground_truth_json=excluded.ground_truth_json
                """,
                (case["case_id"], run_id, case["incident_id"], case["session_id"], case["query"], case["cutoff_at"], _json(case["facts"])),
            )

    def save_result(self, run_id: str, case_id: str, mode: str, result: dict[str, Any]) -> None:
        result_id = f"{run_id}:{case_id}:{mode}"
        metrics = {
            "input_tokens": result.get("input_tokens", 0), "output_tokens": result.get("output_tokens", 0),
            "latency_ms": result.get("latency_ms", 0), "cache_outcomes": result.get("cache_outcomes", []),
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_results(result_id,run_id,case_id,mode,status,metrics_json,record_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(result_id) DO UPDATE SET status=excluded.status,metrics_json=excluded.metrics_json,record_json=excluded.record_json
                """,
                (result_id, run_id, case_id, mode, result["status"], _json(metrics), _json(result), _now()),
            )

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        runs = self.database.query("SELECT * FROM evaluation_runs WHERE run_id=?", (run_id,))
        if not runs:
            return None
        run = runs[0]
        cases = self.database.query("SELECT * FROM evaluation_cases WHERE run_id=? ORDER BY case_id", (run_id,))
        results = self.database.query("SELECT * FROM evaluation_results WHERE run_id=? ORDER BY case_id,mode", (run_id,))
        return {
            "run_id": run_id, "status": run["status"], "config": json.loads(run["config_json"]),
            "summary": json.loads(run["summary_json"]) if run["summary_json"] else None,
            "cases": [{**dict(row), "ground_truth": json.loads(row["ground_truth_json"])} for row in cases],
            "results": [{**dict(row), "metrics": json.loads(row["metrics_json"]), "record": json.loads(row["record_json"])} for row in results],
        }
