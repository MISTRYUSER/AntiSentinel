import sqlite3

import pytest

from antisentinel.persistence.sqlite_database import SQLiteDatabase


EXPECTED_TABLES = {
    "attempts",
    "evaluation_cases",
    "evaluation_results",
    "evaluation_runs",
    "events",
    "evidence",
    "evidence_refs",
    "import_ledger",
    "incidents",
    "memory_conflicts",
    "memory_jobs",
    "memory_records",
    "messages",
    "preference_edges",
    "preference_nodes",
    "schema_migrations",
    "sessions",
    "spans",
    "states",
    "tasks",
    "tool_calls",
    "traces",
    "turns",
    "code_map_repositories",
    "code_map_scan_jobs",
    "code_map_job_attempts",
    "code_map_worker_slots",
    "code_map_snapshots",
    "code_map_blobs",
    "code_map_files",
    "code_map_nodes",
    "code_map_edges",
    "code_map_chunks",
    "code_map_deployments",
    "code_map_diagnosis_bindings",
}


def test_sqlite_database_initializes_schema_and_pragmas_idempotently(tmp_path):
    db = SQLiteDatabase(tmp_path / "antisentinel.db")

    db.initialize()
    db.initialize()

    tables = {
        row["name"]
        for row in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row["name"].startswith(("sqlite_", "memory_records_fts", "memory_vectors"))
    }
    assert tables == EXPECTED_TABLES
    assert db.query("PRAGMA journal_mode")[0][0].lower() == "wal"
    assert db.query("PRAGMA foreign_keys")[0][0] == 1
    assert db.query("PRAGMA busy_timeout")[0][0] == 5000
    assert [row["version"] for row in db.query("SELECT version FROM schema_migrations")] == [1, 2, 3, 4]


def test_sqlite_database_transaction_rolls_back_on_error(tmp_path):
    db = SQLiteDatabase(tmp_path / "antisentinel.db")
    db.initialize()

    with pytest.raises(RuntimeError, match="rollback"):
        with db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO incidents(
                    incident_id, title, source, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("incident-1", "SQLite flow", "operator", "open", "{}", "2026-09-04T00:00:00Z", "2026-09-04T00:00:00Z"),
            )
            raise RuntimeError("rollback")

    assert db.query("SELECT incident_id FROM incidents") == []


def test_sqlite_database_enforces_foreign_keys(tmp_path):
    db = SQLiteDatabase(tmp_path / "antisentinel.db")
    db.initialize()

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    session_id, incident_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("session-1", "missing", "active", "{}", "2026-09-04T00:00:00Z", "2026-09-04T00:00:00Z"),
            )
