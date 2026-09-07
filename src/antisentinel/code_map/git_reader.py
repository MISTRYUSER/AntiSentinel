"""Read immutable Git objects from a service-owned bare cache."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from pathlib import Path
import subprocess
from typing import Iterator
from urllib.parse import urlparse

from .models import RepositoryBudget
from .scheduler import SyncResult


class GitReadError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class TreeEntry:
    path: str
    mode: str
    object_type: str
    object_id: str
    byte_count: int = 0
    included: bool = True
    reason: str | None = None


class SubprocessGitReader:
    def __init__(self, remote_url: str, cache_root: str | Path, credential_ref: str, allowed_hosts: frozenset[str]) -> None:
        self.remote_url = remote_url
        self.cache_root = Path(cache_root)
        self.credential_ref = credential_ref
        self.allowed_hosts = allowed_hosts
        self._validate_remote()

    def sync_ref(self, tracked_ref: str, timeout_s: float = 60.0) -> SyncResult:
        try:
            with self._cache_lock():
                self._ensure_cache(timeout_s)
                self._run(("fetch", "--no-tags", "origin", tracked_ref), timeout_s)
                commit_sha = self._run(("rev-parse", "FETCH_HEAD^{commit}"), timeout_s).stdout.decode().strip()
                self._pin_locked(commit_sha, timeout_s)
            return SyncResult(success=True, commit_sha=commit_sha)
        except GitReadError as exc:
            return SyncResult(success=False, error_code=exc.code, retryable=exc.code in {"sync_timeout", "network_unavailable"})

    def pin(self, commit_sha: str) -> None:
        with self._cache_lock():
            self._pin_locked(commit_sha, 60.0)

    def list_tree(self, commit_sha: str, budget: RepositoryBudget) -> tuple[TreeEntry, ...]:
        with self._cache_lock():
            self._require_commit(commit_sha)
            raw = self._run(("ls-tree", "-r", "-z", commit_sha), 60.0).stdout
            entries: list[TreeEntry] = []
            included_files = 0
            total_bytes = 0
            for record in raw.split(b"\0"):
                if not record:
                    continue
                header, raw_path = record.split(b"\t", 1)
                mode, object_type, object_id = header.decode("ascii").split(" ", 2)
                path = raw_path.decode("utf-8", "surrogateescape")
                if any(0xDC80 <= ord(character) <= 0xDCFF for character in path):
                    entries.append(TreeEntry(path, mode, object_type, object_id, included=False, reason="unsupported_path"))
                    continue
                if mode == "120000":
                    entries.append(TreeEntry(path, mode, object_type, object_id, included=False, reason="symlink"))
                    continue
                if mode == "160000" or object_type == "commit":
                    entries.append(TreeEntry(path, mode, object_type, object_id, included=False, reason="submodule"))
                    continue
                if object_type != "blob":
                    entries.append(TreeEntry(path, mode, object_type, object_id, included=False, reason="unsupported_object"))
                    continue
                byte_count = int(self._run(("cat-file", "-s", object_id), 60.0).stdout.decode().strip())
                if byte_count > budget.max_file_bytes:
                    raise GitReadError("budget_exceeded", f"budget_exceeded: {path}")
                included_files += 1
                total_bytes += byte_count
                if included_files > budget.max_files or total_bytes > budget.max_text_bytes:
                    raise GitReadError("budget_exceeded")
                entries.append(TreeEntry(path, mode, object_type, object_id, byte_count=byte_count))
            return tuple(entries)

    def read_blob(self, commit_sha: str, object_id: str, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise GitReadError("budget_exceeded")
        with self._cache_lock():
            self._require_commit(commit_sha)
            byte_count = int(self._run(("cat-file", "-s", object_id), 60.0).stdout.decode().strip())
            if byte_count > max_bytes:
                raise GitReadError("budget_exceeded")
            return self._run(("cat-file", "blob", object_id), 60.0).stdout

    def diff_paths(self, old_commit: str, new_commit: str) -> tuple[tuple[str, str | None, str], ...]:
        with self._cache_lock():
            self._require_commit(old_commit)
            self._require_commit(new_commit)
            fields = self._run(("diff", "--name-status", "-z", "-M", old_commit, new_commit), 60.0).stdout.split(b"\0")
        changes = []
        index = 0
        while index < len(fields) and fields[index]:
            status = fields[index].decode("ascii")
            index += 1
            first = fields[index].decode("utf-8", "surrogateescape")
            index += 1
            if status.startswith(("R", "C")):
                second = fields[index].decode("utf-8", "surrogateescape")
                index += 1
                changes.append((status, first, second))
            else:
                changes.append((status, None, first))
        return tuple(changes)

    def _validate_remote(self) -> None:
        parsed = urlparse(self.remote_url)
        if parsed.scheme == "file":
            return
        if parsed.scheme == "":
            user_host, separator, _ = self.remote_url.partition(":")
            if separator and "@" in user_host:
                host = user_host.rsplit("@", 1)[1]
                if host not in self.allowed_hosts:
                    raise GitReadError("remote_not_allowed")
                return
            return
        host = parsed.hostname
        if not host or host not in self.allowed_hosts:
            raise GitReadError("remote_not_allowed")

    @contextmanager
    def _cache_lock(self) -> Iterator[None]:
        self.cache_root.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.cache_root.with_suffix(self.cache_root.suffix + ".lock")
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _ensure_cache(self, timeout_s: float) -> None:
        if not self.cache_root.exists():
            self.cache_root.parent.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                ("git", "init", "--bare", str(self.cache_root)), capture_output=True, timeout=timeout_s,
            )
            if completed.returncode != 0:
                raise GitReadError("git_init_failed")
        remote = self._run(("remote",), timeout_s).stdout.decode().split()
        if "origin" in remote:
            self._run(("remote", "set-url", "origin", self.remote_url), timeout_s)
        else:
            self._run(("remote", "add", "origin", self.remote_url), timeout_s)

    def _pin_locked(self, commit_sha: str, timeout_s: float) -> None:
        self._require_commit(commit_sha, timeout_s)
        self._run(("update-ref", f"refs/antisentinel/pinned/{commit_sha}", commit_sha), timeout_s)

    def _require_commit(self, commit_sha: str, timeout_s: float = 60.0) -> None:
        self._run(("cat-file", "-e", f"{commit_sha}^{{commit}}"), timeout_s)

    def _run(self, args: tuple[str, ...], timeout_s: float) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ("git", "--git-dir", str(self.cache_root), *args),
                check=True, capture_output=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitReadError("sync_timeout") from exc
        except subprocess.CalledProcessError as exc:
            raise GitReadError("network_unavailable") from exc
