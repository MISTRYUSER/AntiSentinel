"""Long-lived scheduler and worker process for code-map registrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Any

from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.tracing.telemetry import Telemetry

from .config import CodeMapConfig
from .scheduler import CodeMapScheduler
from .store import SQLiteCodeMapStore
from .worker import CodeMapWorker


@dataclass(frozen=True)
class DaemonHealth:
    status: str
    last_tick_at: datetime | None
    ticks: int
    worker_runs: int


class RegisteredGitReader:
    """Construct a fixed-Commit reader from each server-side registration."""

    def __init__(self, store: SQLiteCodeMapStore, cache_root):
        self.store = store
        self.cache_root = cache_root
        self.builders = {}

    def sync_registration(self, registration, *, timeout_s: float = 60.0):
        from .git_reader import SubprocessGitReader
        reader = SubprocessGitReader(
            remote_url=registration.remote_url,
            cache_root=self.cache_root / registration.repository_id,
            credential_ref=registration.credential_ref,
            allowed_hosts=frozenset(),
        )
        return reader.sync_ref(registration.tracked_ref, timeout_s=timeout_s)

    def build(self, lease):
        from .git_reader import SubprocessGitReader
        from .incremental import AstFactCache
        from .snapshot_builder import SnapshotBuilder
        registration = self.store.get_registration(lease.repository_id)
        key = (lease.repository_id, registration.remote_url)
        if key not in self.builders:
            reader = SubprocessGitReader(registration.remote_url, self.cache_root / lease.repository_id,
                                         registration.credential_ref, frozenset())
            self.builders[key] = SnapshotBuilder(reader, fact_cache=AstFactCache())
        builder = self.builders[key]
        previous = self.store.database.query(
            "SELECT commit_sha FROM code_map_snapshots WHERE repository_id=? AND status='ready' ORDER BY published_at DESC LIMIT 1",
            (lease.repository_id,),
        )
        return builder.build(lease, previous_commit=previous[0][0] if previous else None)


class CodeMapDaemon:
    def __init__(self, config: CodeMapConfig, scheduler: CodeMapScheduler, worker: CodeMapWorker, *, telemetry: Telemetry | None = None) -> None:
        self.config = config
        self.scheduler = scheduler
        self.worker = worker
        self.telemetry = telemetry
        self._stop = Event()
        self._last_tick_at: datetime | None = None
        self._ticks = 0
        self._worker_runs = 0

    def run_forever(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            if self.telemetry is None:
                self.scheduler.tick(now)
                self.worker.run_once(now=now)
            else:
                with self.telemetry.span("code_map.tick"):
                    with self.telemetry.span("code_map.scheduler.tick"):
                        self.scheduler.tick(now)
                    with self.telemetry.span("code_map.worker.run_once"):
                        self.worker.run_once(now=now)
            self._last_tick_at = now
            self._ticks += 1
            self._worker_runs += 1
            self._stop.wait(self.config.poll_seconds)

    def stop(self) -> None:
        self._stop.set()

    def health(self) -> dict[str, Any]:
        return {
            "status": "stopping" if self._stop.is_set() else "running",
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
            "ticks": self._ticks,
            "worker_runs": self._worker_runs,
        }


def build_daemon_from_environment(config: CodeMapConfig | None = None) -> CodeMapDaemon:
    config = config or CodeMapConfig.from_environment()
    database = SQLiteDatabase(config.database_path)
    database.initialize()
    store = SQLiteCodeMapStore(database, lease_seconds=config.lease_seconds)
    git_reader = RegisteredGitReader(store, config.cache_root)
    scheduler = CodeMapScheduler(store, git_reader, owner="code-map-scheduler")
    worker = CodeMapWorker(store, builder=git_reader.build, owner="code-map-worker")
    telemetry = Telemetry(service_name="antisentinel.code_map", database=database, trace_path=config.storage_root / "observability" / "code-map-traces.jsonl")
    return CodeMapDaemon(config, scheduler, worker, telemetry=telemetry)
