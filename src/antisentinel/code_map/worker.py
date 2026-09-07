"""Durable single-slot worker for code-map build jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

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
    def __init__(self, store: SQLiteCodeMapStore, *, builder: Callable[[JobLease], MapSnapshot] | None = None, owner: str = "worker") -> None:
        self.store = store
        self.builder = builder
        self.owner = owner

    def run_once(self, owner: str | None = None, now: datetime | None = None) -> WorkerResult:
        current = now or self.store._now()
        lease = self.store.claim_job(owner or self.owner, current)
        if lease is None:
            return WorkerResult("idle")
        try:
            snapshot = self.builder(lease) if self.builder is not None else MapSnapshot(
                snapshot_id=snapshot_id_for(
                    lease.repository_id, lease.commit_sha, lease.parser_revision, lease.rules_digest,
                ),
                repository_id=lease.repository_id, commit_sha=lease.commit_sha,
                parser_revision=lease.parser_revision, rules_digest=lease.rules_digest,
                created_at=current,
            )
            result = self.store.publish(lease, snapshot, self.store.empty_staged_rows())
            if not result.ok:
                return WorkerResult("failed", lease.job_id, result.error.code if result.error else "publish_failed")
            return WorkerResult("succeeded", lease.job_id)
        except DomainError as exc:
            return WorkerResult("failed", lease.job_id, str(exc))
        except Exception as exc:  # normalize builder failures at the worker boundary
            return WorkerResult("failed", lease.job_id, type(exc).__name__)
