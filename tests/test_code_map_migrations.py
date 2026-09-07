import sqlite3


def test_initialize_upgrades_v1_database_without_changing_existing_rows(tmp_path):
    from antisentinel.persistence.sqlite_database import SQLiteDatabase, SCHEMA_VERSION

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE incidents(incident_id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT, source TEXT NOT NULL,
            status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO schema_migrations(version) VALUES (1);
        INSERT INTO incidents VALUES ('inc-1','title',NULL,'operator','open','{}','2026-01-01','2026-01-01');
        """
    )
    connection.commit()
    connection.close()

    database = SQLiteDatabase(path)
    database.initialize()

    assert SCHEMA_VERSION == 4
    assert database.query("SELECT title FROM incidents WHERE incident_id='inc-1'")[0]["title"] == "title"
    assert database.query("SELECT MAX(version) AS version FROM schema_migrations")[0]["version"] == 4
    for table in (
        "code_map_repositories", "code_map_scan_jobs", "code_map_job_attempts", "code_map_worker_slots",
        "code_map_snapshots", "code_map_blobs", "code_map_files", "code_map_nodes", "code_map_edges", "code_map_chunks",
        "code_map_deployments", "code_map_diagnosis_bindings",
    ):
        assert database.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))


def test_initialize_is_idempotent_and_rejects_newer_schema(tmp_path):
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    database = SQLiteDatabase(tmp_path / "database.db")
    database.initialize()
    database.initialize()
    assert database.query("SELECT COUNT(*) AS count FROM schema_migrations")[0]["count"] == 4

    with database.transaction() as connection:
        connection.execute("INSERT INTO schema_migrations(version) VALUES (99)")

    try:
        database.initialize()
    except RuntimeError as exc:
        assert "newer" in str(exc)
    else:
        raise AssertionError("newer schema version must be rejected")
