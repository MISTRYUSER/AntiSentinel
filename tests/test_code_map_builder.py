import subprocess
from datetime import datetime, timezone


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_builder_reads_fixed_git_blob_and_publishes_minimal_python_map(tmp_path):
    from antisentinel.code_map.git_reader import SubprocessGitReader
    from antisentinel.code_map.models import RepositoryRegistration
    from antisentinel.code_map.snapshot_builder import SnapshotBuilder
    from antisentinel.code_map.store import SQLiteCodeMapStore
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    remote = tmp_path / "remote.git"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", str(worktree)], check=True, capture_output=True, text=True)
    git(worktree, "config", "user.email", "case@example.test")
    git(worktree, "config", "user.name", "Code Map Case")
    (worktree / "app.py").write_text("class Service:\n    def run(self):\n        return 1\n", encoding="utf-8")
    git(worktree, "add", "app.py")
    git(worktree, "commit", "-m", "fixture")
    git(worktree, "remote", "add", "origin", str(remote))
    git(worktree, "push", "-u", "origin", "main")
    commit = git(worktree, "rev-parse", "HEAD")

    database = SQLiteDatabase(tmp_path / "map.db")
    database.initialize()
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    store = SQLiteCodeMapStore(database, clock=lambda: now)
    store.register(RepositoryRegistration(
        repository_id="repo-a", remote_url=str(remote), credential_ref="local", tracked_ref="refs/heads/main",
    ))
    job = store.enqueue("repo-a", commit, "scheduled")
    lease = store.claim_job("worker", now)
    reader = SubprocessGitReader(str(remote), tmp_path / "cache", "local", frozenset())
    reader.sync_ref("refs/heads/main")

    output = SnapshotBuilder(reader).build(lease)
    result = store.publish(lease, output.snapshot, output.rows)

    assert result.ok is True
    ready = store.get_published_snapshot("repo-a", commit)
    assert (ready.file_count, ready.node_count, ready.edge_count, ready.chunk_count) == (1, 2, 1, 2)


def test_worker_publishes_builder_rows_not_an_empty_snapshot(tmp_path):
    from antisentinel.code_map.git_reader import SubprocessGitReader
    from antisentinel.code_map.models import RepositoryRegistration
    from antisentinel.code_map.snapshot_builder import SnapshotBuilder
    from antisentinel.code_map.store import SQLiteCodeMapStore
    from antisentinel.code_map.worker import CodeMapWorker
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    remote = tmp_path / "remote.git"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", str(worktree)], check=True, capture_output=True, text=True)
    git(worktree, "config", "user.email", "case@example.test")
    git(worktree, "config", "user.name", "Code Map Case")
    (worktree / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    git(worktree, "add", "app.py")
    git(worktree, "commit", "-m", "fixture")
    git(worktree, "remote", "add", "origin", str(remote))
    git(worktree, "push", "-u", "origin", "main")
    commit = git(worktree, "rev-parse", "HEAD")
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    database = SQLiteDatabase(tmp_path / "map.db")
    database.initialize()
    store = SQLiteCodeMapStore(database, clock=lambda: now)
    store.register(RepositoryRegistration(
        repository_id="repo-a", remote_url=str(remote), credential_ref="local", tracked_ref="refs/heads/main",
    ))
    store.enqueue("repo-a", commit, "scheduled")
    reader = SubprocessGitReader(str(remote), tmp_path / "cache", "local", frozenset())
    reader.sync_ref("refs/heads/main")

    result = CodeMapWorker(store, builder=SnapshotBuilder(reader).build).run_once(now=now)

    assert result.status == "succeeded"
    ready = store.get_published_snapshot("repo-a", commit)
    assert ready is not None and ready.node_count == 1 and ready.chunk_count == 1


def test_builder_persists_file_blob_and_reads_chunk_source_by_snapshot(tmp_path):
    from antisentinel.code_map.git_reader import SubprocessGitReader
    from antisentinel.code_map.models import RepositoryRegistration
    from antisentinel.code_map.snapshot_builder import SnapshotBuilder
    from antisentinel.code_map.store import SQLiteCodeMapStore
    from antisentinel.code_map.worker import CodeMapWorker
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    remote = tmp_path / "remote.git"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", str(worktree)], check=True, capture_output=True, text=True)
    git(worktree, "config", "user.email", "case@example.test")
    git(worktree, "config", "user.name", "Code Map Case")
    source = "def run():\n    return 'stable'\n"
    (worktree / "app.py").write_text(source, encoding="utf-8")
    git(worktree, "add", "app.py")
    git(worktree, "commit", "-m", "fixture")
    git(worktree, "remote", "add", "origin", str(remote))
    git(worktree, "push", "-u", "origin", "main")
    commit = git(worktree, "rev-parse", "HEAD")
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    database = SQLiteDatabase(tmp_path / "map.db")
    database.initialize()
    store = SQLiteCodeMapStore(database, clock=lambda: now)
    store.register(RepositoryRegistration(repository_id="repo-a", remote_url=str(remote), credential_ref="local", tracked_ref="refs/heads/main"))
    store.enqueue("repo-a", commit, "scheduled")
    reader = SubprocessGitReader(str(remote), tmp_path / "cache", "local", frozenset())
    reader.sync_ref("refs/heads/main")
    CodeMapWorker(store, builder=SnapshotBuilder(reader).build).run_once(now=now)

    chunk = store.database.query("SELECT chunk_id FROM code_map_chunks LIMIT 1")[0]["chunk_id"]
    assert store.read_chunk_source("repo-a", commit, chunk) == source.encode()
