from datetime import datetime, timezone


def test_publish_persists_nodes_contains_edges_chunks_and_reopens_ready_snapshot(tmp_path):
    from antisentinel.code_map.identity import snapshot_id_for
    from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration
    from antisentinel.code_map.python_parser import PythonAstParser
    from antisentinel.code_map.store import SQLiteCodeMapStore, StagedMapRows
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    database_path = tmp_path / "snapshot.db"
    database = SQLiteDatabase(database_path)
    database.initialize()
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    store = SQLiteCodeMapStore(database, clock=lambda: now)
    store.register(RepositoryRegistration(
        repository_id="repo-a", remote_url="file:///repo-a", credential_ref="local", tracked_ref="refs/heads/main",
    ))
    job = store.enqueue("repo-a", "a" * 40, "scheduled", parser_revision="python-3.13/ast-v1")
    lease = store.claim_job("worker", now)
    snapshot_id = snapshot_id_for("repo-a", "a" * 40, "python-3.13/ast-v1", job.rules_digest)
    parsed = PythonAstParser("python-3.13/ast-v1").parse_file(
        "pkg/a.py", b"class A:\n    def run(self):\n        return 1\n", snapshot_id,
    )
    snapshot = MapSnapshot(
        snapshot_id=snapshot_id, repository_id="repo-a", commit_sha="a" * 40,
        parser_revision="python-3.13/ast-v1", rules_digest=job.rules_digest, file_count=1,
    )

    result = store.publish(lease, snapshot, StagedMapRows(
        nodes=parsed.symbols, edges=PythonAstParser("python-3.13/ast-v1").contains_edges((parsed,), snapshot_id), chunks=parsed.chunks,
    ))

    assert result.ok is True
    reopened = SQLiteCodeMapStore(SQLiteDatabase(database_path), clock=lambda: now)
    ready = reopened.get_published_snapshot("repo-a", "a" * 40)
    assert ready is not None and ready.status == "ready"
    assert (ready.node_count, ready.edge_count, ready.chunk_count) == (2, 1, 2)
