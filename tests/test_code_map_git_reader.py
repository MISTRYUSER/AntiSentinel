import subprocess
from pathlib import Path

import pytest


def run_git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_remote(tmp_path: Path):
    remote = tmp_path / "remote.git"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", str(worktree)], check=True, capture_output=True, text=True)
    run_git(worktree, "config", "user.email", "case@example.test")
    run_git(worktree, "config", "user.name", "Code Map Case")
    (worktree / "module.py").write_text("def stable():\n    return 'a'\n", encoding="utf-8")
    run_git(worktree, "add", "module.py")
    run_git(worktree, "commit", "-m", "commit-a")
    commit_a = run_git(worktree, "rev-parse", "HEAD")
    run_git(worktree, "remote", "add", "origin", str(remote))
    run_git(worktree, "push", "-u", "origin", "main")
    return remote, worktree, commit_a


def test_reader_pins_fixed_commit_across_worktree_edit_and_force_push(tmp_path):
    from antisentinel.code_map.git_reader import SubprocessGitReader
    from antisentinel.code_map.models import RepositoryBudget

    remote, worktree, commit_a = make_remote(tmp_path)
    reader = SubprocessGitReader(str(remote), tmp_path / "cache", "local-test", frozenset())
    result = reader.sync_ref("refs/heads/main")
    assert result.success is True and result.commit_sha == commit_a
    entry = reader.list_tree(commit_a, RepositoryBudget())[0]
    original = reader.read_blob(commit_a, entry.object_id, 1_048_576)

    (worktree / "module.py").write_text("def stable():\n    return 'working-tree-only'\n", encoding="utf-8")
    assert reader.read_blob(commit_a, entry.object_id, 1_048_576) == original
    run_git(worktree, "add", "module.py")
    run_git(worktree, "commit", "-m", "commit-b")
    run_git(worktree, "push", "--force", "origin", "main")
    assert reader.sync_ref("refs/heads/main").commit_sha != commit_a
    assert reader.read_blob(commit_a, entry.object_id, 1_048_576) == original


def test_reader_classifies_symlink_and_rejects_file_budget_before_blob_read(tmp_path):
    from antisentinel.code_map.git_reader import GitReadError, SubprocessGitReader
    from antisentinel.code_map.models import RepositoryBudget

    remote, worktree, _ = make_remote(tmp_path)
    (worktree / "large.py").write_text("x" * 32, encoding="utf-8")
    (worktree / "link.py").symlink_to("module.py")
    run_git(worktree, "add", "large.py", "link.py")
    run_git(worktree, "commit", "-m", "budget-and-link")
    run_git(worktree, "push", "origin", "main")
    reader = SubprocessGitReader(str(remote), tmp_path / "cache", "local-test", frozenset())
    commit = reader.sync_ref("refs/heads/main").commit_sha

    with pytest.raises(GitReadError, match="budget_exceeded"):
        reader.list_tree(commit, RepositoryBudget(max_files=10, max_file_bytes=8, max_text_bytes=64))

    entries = reader.list_tree(commit, RepositoryBudget())
    symlink = next(entry for entry in entries if entry.path == "link.py")
    assert symlink.included is False
    assert symlink.reason == "symlink"


def test_registered_reader_uses_server_side_registration(tmp_path):
    from antisentinel.code_map.daemon import RegisteredGitReader
    from antisentinel.code_map.models import RepositoryRegistration
    from antisentinel.code_map.store import SQLiteCodeMapStore
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    remote, _, commit = make_remote(tmp_path)
    database = SQLiteDatabase(tmp_path / "daemon.db")
    database.initialize()
    store = SQLiteCodeMapStore(database)
    registration = store.register(RepositoryRegistration(
        repository_id="repo-a", remote_url=str(remote), credential_ref="local-test", tracked_ref="refs/heads/main",
    ))

    result = RegisteredGitReader(store, tmp_path / "cache").sync_registration(registration)
    assert result.success is True
    assert result.commit_sha == commit


def test_reader_rejects_scp_style_ssh_host_outside_allowlist(tmp_path):
    from antisentinel.code_map.git_reader import GitReadError, SubprocessGitReader

    with pytest.raises(GitReadError, match="remote_not_allowed"):
        SubprocessGitReader("git@untrusted.example:team/repo.git", tmp_path / "cache", "credential", frozenset())


def test_reader_reports_added_modified_deleted_and_renamed_paths(tmp_path):
    from antisentinel.code_map.git_reader import SubprocessGitReader

    remote, worktree, commit_a = make_remote(tmp_path)
    reader = SubprocessGitReader(str(remote), tmp_path / "cache", "local-test", frozenset())
    reader.sync_ref("refs/heads/main")
    git = lambda *args: run_git(worktree, *args)
    git("mv", "module.py", "renamed.py")
    (worktree / "added.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "renamed.py", "added.py")
    git("commit", "-m", "commit-b")
    git("push", "origin", "main")
    commit_b = reader.sync_ref("refs/heads/main").commit_sha

    changes = reader.diff_paths(commit_a, commit_b)

    assert ("A", None, "added.py") in changes
    assert any(status.startswith("R") and old == "module.py" and new == "renamed.py" for status, old, new in changes)
