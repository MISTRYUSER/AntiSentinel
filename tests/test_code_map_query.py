from datetime import datetime, timezone


def build_query(tmp_path):
    from antisentinel.code_map.identity import snapshot_id_for
    from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration
    from antisentinel.code_map.python_parser import PythonAstParser
    from antisentinel.code_map.query import CodeMapQuery, QueryScope
    from antisentinel.code_map.store import SQLiteCodeMapStore, StagedMapRows
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    database = SQLiteDatabase(tmp_path / "query.db")
    database.initialize()
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    store = SQLiteCodeMapStore(database, clock=lambda: now)
    store.register(RepositoryRegistration(repository_id="repo-a", remote_url="file:///repo-a", credential_ref="local", tracked_ref="refs/heads/main"))
    job = store.enqueue("repo-a", "a" * 40, "scheduled")
    lease = store.claim_job("worker", now)
    snapshot_id = snapshot_id_for("repo-a", "a" * 40, "python-ast-v1", job.rules_digest)
    parser = PythonAstParser("python-ast-v1")
    parsed = parser.parse_file("app.py", b"class Service:\n    def run(self):\n        return 1\n", snapshot_id)
    snapshot = MapSnapshot(snapshot_id=snapshot_id, repository_id="repo-a", commit_sha="a" * 40, parser_revision="python-ast-v1", rules_digest=job.rules_digest, file_count=1)
    store.publish(lease, snapshot, StagedMapRows(nodes=parsed.symbols, edges=parser.contains_edges((parsed,), snapshot_id), chunks=parsed.chunks))
    return CodeMapQuery(store), QueryScope("incident-a", frozenset({"repo-a"})), snapshot_id, parsed.symbols


def test_exact_symbol_and_contains_neighbors_are_scoped_to_published_snapshot(tmp_path):
    query, scope, snapshot_id, symbols = build_query(tmp_path)

    found = query.find_symbols(scope, "repo-a", snapshot_id, "Service.run")
    neighbors = query.get_neighbors(scope, "repo-a", snapshot_id, symbols[0].node_id, direction="outbound", relations=("contains",))

    assert found.error is None
    assert [node["qualified_name"] for node in found.items] == ["Service.run"]
    assert neighbors.error is None
    assert [node["qualified_name"] for node in neighbors.items] == ["Service.run"]


def test_missing_commit_never_falls_back_and_scope_mismatch_is_rejected(tmp_path):
    from antisentinel.code_map.query import QueryScope

    query, scope, snapshot_id, _ = build_query(tmp_path)

    missing = query.get_snapshot(scope, "repo-a", "b" * 40)
    rejected = query.find_symbols(QueryScope("incident-b", frozenset()), "repo-a", snapshot_id, "Service")

    assert missing.error.code == "snapshot_missing"
    assert rejected.error.code == "scope_mismatch"
