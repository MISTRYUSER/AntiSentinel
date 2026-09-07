"""Adaptive repository version checks; source building belongs to the Worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .store import CheckLease, SQLiteCodeMapStore


@dataclass(frozen=True)
class SyncResult:
    success: bool
    commit_sha: str | None = None
    error_code: str | None = None
    retryable: bool = False
    trace_context: Mapping[str, str] = field(default_factory=dict)
    trigger: str = "scheduled"


@dataclass(frozen=True)
class SchedulerResult:
    status: str
    repository_id: str | None = None
    job_id: str | None = None
    error_code: str | None = None


class CodeMapScheduler:
    def __init__(self, store: SQLiteCodeMapStore, git_reader: Any, *, owner: str = "scheduler") -> None:
        self.store = store
        self.git_reader = git_reader
        self.owner = owner

    def tick(self, now: datetime) -> SchedulerResult:
        lease = self.store.claim_check(now, self.owner)
        if lease is None:
            return SchedulerResult(status="idle")
        registration = self.store.get_registration(lease.repository_id)
        try:
            result = self.git_reader.sync_ref(registration.tracked_ref, timeout_s=60.0)
            if not isinstance(result, SyncResult):
                result = SyncResult(success=True, commit_sha=str(result))
        except TimeoutError:
            result = SyncResult(success=False, error_code="sync_timeout", retryable=True)
        except PermissionError:
            result = SyncResult(success=False, error_code="permission_denied", retryable=False)
        except Exception:
            result = SyncResult(success=False, error_code="network_unavailable", retryable=True)
        job = self.store.complete_check(lease, result)
        return SchedulerResult(
            status="succeeded" if result.success else "failed",
            repository_id=lease.repository_id,
            job_id=job.job_id if job else None,
            error_code=result.error_code,
        )
