"""SQLite connection, transaction, and schema lifecycle."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 1

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_incident_updated ON sessions(incident_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session_order ON messages(session_id, message_id);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, ordinal)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES turns(turn_id),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    tool_call_id TEXT NOT NULL REFERENCES tool_calls(tool_call_id),
    retry_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tool_call_id, retry_index)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_aggregate_order ON events(aggregate_type, aggregate_id, occurred_at, event_id);
CREATE INDEX IF NOT EXISTS idx_events_correlation_order ON events(correlation_id, occurred_at, event_id);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    incident_id TEXT,
    content_hash TEXT,
    content_uri TEXT,
    media_type TEXT,
    byte_count INTEGER,
    preview TEXT,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(content_hash);
CREATE INDEX IF NOT EXISTS idx_evidence_incident ON evidence(incident_id, created_at);

CREATE TABLE IF NOT EXISTS evidence_refs (
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
    role TEXT NOT NULL,
    PRIMARY KEY(owner_type, owner_id, evidence_id, role)
);

CREATE TABLE IF NOT EXISTS states (
    scope_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_records (
    memory_id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    operator_id TEXT,
    incident_id TEXT,
    session_id TEXT,
    entity_key TEXT,
    status TEXT,
    confidence REAL,
    content_version INTEGER NOT NULL DEFAULT 1,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_operator_type ON memory_records(operator_id, memory_type, updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_entity ON memory_records(entity_key, status, confidence);

CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id TEXT PRIMARY KEY REFERENCES memory_records(memory_id),
    embedding_model TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    content_version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_vectors_model ON memory_vectors(embedding_model, dimension);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts USING fts5(
    memory_id UNINDEXED,
    searchable_text,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS preference_nodes (
    node_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    canonical_value TEXT NOT NULL,
    record_json TEXT NOT NULL,
    UNIQUE(operator_id, node_type, canonical_value)
);

CREATE TABLE IF NOT EXISTS preference_edges (
    edge_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    subject_node_id TEXT NOT NULL REFERENCES preference_nodes(node_id),
    predicate TEXT NOT NULL,
    object_node_id TEXT NOT NULL REFERENCES preference_nodes(node_id),
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_preference_edges_active ON preference_edges(operator_id, predicate, status);

CREATE TABLE IF NOT EXISTS memory_conflicts (
    conflict_id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    active_memory_id TEXT,
    candidate_memory_id TEXT NOT NULL,
    resolution TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    lease_until TEXT,
    payload_json TEXT NOT NULL,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_claim ON memory_jobs(status, available_at);

CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY,
    incident_id TEXT,
    session_id TEXT,
    started_at_ns INTEGER,
    ended_at_ns INTEGER,
    attributes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id, started_at_ns);

CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces(trace_id),
    parent_span_id TEXT,
    session_id TEXT,
    turn_id TEXT,
    name TEXT NOT NULL,
    start_time_ns INTEGER NOT NULL,
    end_time_ns INTEGER,
    attributes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spans_trace_time ON spans(trace_id, start_time_ns);
CREATE INDEX IF NOT EXISTS idx_spans_session_turn ON spans(session_id, turn_id, name);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    summary_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS evaluation_cases (
    case_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(run_id),
    incident_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    query TEXT NOT NULL,
    cutoff_at TEXT NOT NULL,
    ground_truth_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluation_cases_scope ON evaluation_cases(run_id, incident_id, session_id);

CREATE TABLE IF NOT EXISTS evaluation_results (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(run_id),
    case_id TEXT NOT NULL REFERENCES evaluation_cases(case_id),
    mode TEXT NOT NULL CHECK(mode IN ('baseline', 'optimized')),
    status TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, case_id, mode)
);

CREATE TABLE IF NOT EXISTS import_ledger (
    fingerprint TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    stable_id TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteDatabase:
    """Own SQLite configuration and transaction semantics behind one interface."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA_V1)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return list(connection.execute(sql, tuple(params)).fetchall())
        finally:
            connection.close()

    def close(self) -> None:
        """Connections are operation-scoped, so there is no shared handle to close."""
