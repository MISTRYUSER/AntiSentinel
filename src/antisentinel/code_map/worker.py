"""Durable single-slot worker for code-map build jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
import threading
import multiprocessing as mp

from antisentinel.domain.errors import DomainError

from .identity import snapshot_id_for
from .models import MapSnapshot
from .store import JobLease, SQLiteCodeMapStore


@dataclass(frozen=True)
class WorkerResult:
    status: str
    job_id: str | None = None
    error_code: str | None = None


class CodeMapWorker:
    def __init__(self, store: SQLiteCodeMapStore, *, builder: Callable[[JobLease], MapSnapshot] | None = None, owner: str = "worker", timeout_seconds: float = 120.0) -> None:
        self.store = store
        self.builder = builder
        self.owner = owner
        self.timeout_seconds = timeout_seconds

    def run_once(self, owner: str | None = None, now: datetime | None = None) -> WorkerResult:
        current = now or self.store._now()
        lease = self.store.claim_job(owner or self.owner, current)
        if lease is None:
            return WorkerResult("idle")
        stop = threading.Event()
        def watch():
            interval = max(0.5, min(self.store.lease_seconds / 3, 10.0))
            while not stop.wait(interval):
                if not self.store.heartbeat(lease, self.store._now()): return
        heartbeat = threading.Thread(target=watch, daemon=True); heartbeat.start()
        try:
            ctx = mp.get_context("fork")
            queue = ctx.Queue()
            def child():
                try:
                    value = self.builder(lease) if self.builder is not None else MapSnapshot(snapshot_id=snapshot_id_for(lease.repository_id, lease.commit_sha, lease.parser_revision, lease.rules_digest), repository_id=lease.repository_id, commit_sha=lease.commit_sha, parser_revision=lease.parser_revision, rules_digest=lease.rules_digest, created_at=current)
                    queue.put(("ok", value))
                except Exception as exc:
                    queue.put(("error", type(exc).__name__, str(exc)))
            process = ctx.Process(target=child, daemon=True); process.start()
            message_holder = []
            def drain():
                try: message_holder.append(queue.get())
                except (EOFError, OSError): pass
            reader = threading.Thread(target=drain, daemon=True); reader.start()
            process.join(self.timeout_seconds)
            if process.is_alive():
                process.terminate(); process.join(1)
                if process.is_alive(): process.kill(); process.join(5)
                if process.is_alive():
                    self.store.fail_job(lease, "build_process_stuck", "child did not exit")
                    return WorkerResult("failed", lease.job_id, "build_process_stuck")
                self.store.fail_job(lease, "build_timeout", f"timeout>{self.timeout_seconds}s")
                return WorkerResult("failed", lease.job_id, "build_timeout")
            reader.join(2)
            if not message_holder:
                self.store.fail_job(lease, "builder_exit", "builder exited without result")
                return WorkerResult("failed", lease.job_id, "builder_exit")
            message = message_holder[0]
            if message[0] == "error":
                self.store.fail_job(lease, message[1], message[2]); return WorkerResult("failed", lease.job_id, message[1])
            built = message[1]
            snapshot = built.snapshot if hasattr(built, "snapshot") else built
            rows = built.rows if hasattr(built, "rows") else self.store.empty_staged_rows()
            result = self.store.publish(lease, snapshot, rows)
            if not result.ok:
                code = result.error.code if result.error else "publish_failed"; self.store.fail_job(lease, code, code); return WorkerResult("failed", lease.job_id, code)
            return WorkerResult("succeeded", lease.job_id)
        except DomainError as exc:
            self.store.fail_job(lease, str(exc), str(exc)); return WorkerResult("failed", lease.job_id, str(exc))
        except Exception as exc:
            code = type(exc).__name__; self.store.fail_job(lease, code, str(exc)); return WorkerResult("failed", lease.job_id, code)
        finally:
            stop.set()
            heartbeat.join(timeout=1)
