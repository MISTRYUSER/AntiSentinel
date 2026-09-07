"""SQLite persistence for repository registrations and scan scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from antisentinel.domain.errors import DomainError, InvalidInputError
from antisentinel.domain.primitives import decode_datetime, encode_datetime
from antisentinel.persistence.sqlite_database import SQLiteDatabase

from .identity import snapshot_id_for
from .models import RepositoryRegistration, ScanJob


@dataclass(frozen=True)
class CheckLease:
    repository_id: str
    owner: str
    token: str
    expires_at: datetime


class SQLiteCodeMapStore:
    def __init__(self, database: SQLiteDatabase, *, clock: Any | None = None, lease_seconds: int = 60) -> None:
        self.database = database
        self.clock = clock
        self.lease_seconds = lease_seconds

    def _now(self) -> datetime:
        if self.clock is None:
            return datetime.now(timezone.utc)
        return self.clock.now() if hasattr(self.clock, "now") else self.clock()

    def register(self, registration: RepositoryRegistration) -> RepositoryRegistration:
        now = self._now()
        next_check = registration.next_check_at or now
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO code_map_repositories(
                    repository_id, remote_url, credential_ref, tracked_ref, min_interval, max_interval,
                    current_interval, last_check_completed_at, last_change_observed_at, next_check_at,
                    last_check_at, last_sync_at, observed_commit, rules_json, budget_json, enabled,
                    blocked, blocked_reason, check_token, check_expires_at, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(repository_id) DO UPDATE SET
                    remote_url=excluded.remote_url, credential_ref=excluded.credential_ref,
                    tracked_ref=excluded.tracked_ref, min_interval=excluded.min_interval,
                    max_interval=excluded.max_interval, rules_json=excluded.rules_json,
                    updated_at=excluded.updated_at
                """,
                (
                    registration.repository_id, registration.remote_url, registration.credential_ref,
                    registration.tracked_ref, registration.min_interval, registration.max_interval,
                    registration.current_interval, _dt(registration.last_check_completed_at),
                    _dt(registration.last_change_observed_at), _dt(next_check), _dt(registration.last_check_at),
                    _dt(registration.last_sync_at), registration.observed_commit,
                    json.dumps(dict(registration.rules), ensure_ascii=False, sort_keys=True), "{}",
                    int(registration.enabled), int(registration.blocked), registration.blocked_reason,
                    None, None, _dt(now), _dt(now),
                ),
            )
        return self.get_registration(registration.repository_id)

    def get_registration(self, repository_id: str) -> RepositoryRegistration:
        rows = self.database.query("SELECT * FROM code_map_repositories WHERE repository_id=?", (repository_id,))
        if not rows:
            raise DomainError(f"unknown repository: {repository_id}")
        row = rows[0]
        return RepositoryRegistration(
            repository_id=row["repository_id"], remote_url=row["remote_url"],
            credential_ref=row["credential_ref"], tracked_ref=row["tracked_ref"],
            min_interval=int(row["min_interval"]), max_interval=int(row["max_interval"]),
            current_interval=row["current_interval"],
            last_check_completed_at=_decode_optional(row["last_check_completed_at"], "last_check_completed_at"),
            last_change_observed_at=_decode_optional(row["last_change_observed_at"], "last_change_observed_at"),
            next_check_at=_decode_optional(row["next_check_at"], "next_check_at"),
            last_check_at=_decode_optional(row["last_check_at"], "last_check_at"),
            last_sync_at=_decode_optional(row["last_sync_at"], "last_sync_at"),
            observed_commit=row["observed_commit"],
            rules=json.loads(row["rules_json"]), enabled=bool(row["enabled"]),
            blocked=bool(row["blocked"]), blocked_reason=row["blocked_reason"],
        )

    def next_check(self, repository_id: str) -> datetime | None:
        return self.get_registration(repository_id).next_check_at

    def claim_check(self, now: datetime, owner: str) -> CheckLease | None:
        now = _require_utc(now)
        token = str(uuid4())
        expires_at = now + timedelta(seconds=self.lease_seconds)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT repository_id FROM code_map_repositories
                WHERE enabled=1 AND blocked=0 AND next_check_at IS NOT NULL AND next_check_at<=?
                  AND (check_token IS NULL OR check_expires_at<=?)
                ORDER BY next_check_at, repository_id LIMIT 1
                """,
                (_dt(now), _dt(now)),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE code_map_repositories SET check_token=?, check_expires_at=?, last_check_at=?, updated_at=?
                WHERE repository_id=? AND enabled=1 AND blocked=0
                  AND next_check_at<=? AND (check_token IS NULL OR check_expires_at<=?)
                """,
                (token, _dt(expires_at), _dt(now), _dt(now), row["repository_id"], _dt(now), _dt(now)),
            ).rowcount
            if updated != 1:
                return None
        return CheckLease(row["repository_id"], owner, token, expires_at)

    def complete_check(self, lease: CheckLease, result: Any) -> ScanJob | None:
        now = self._now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM code_map_repositories WHERE repository_id=? AND check_token=? AND check_expires_at>?",
                (lease.repository_id, lease.token, _dt(now)),
            ).fetchone()
            if row is None:
                raise DomainError("lease_lost")
            if not result.success:
                blocked = result.error_code in {"auth_failed", "permission_denied", "repository_missing", "ref_missing"}
                interval = int(row["current_interval"] or row["min_interval"])
                connection.execute(
                    """
                    UPDATE code_map_repositories SET next_check_at=?, last_check_completed_at=?,
                        check_token=NULL, check_expires_at=NULL, blocked=?, blocked_reason=?, updated_at=?
                    WHERE repository_id=? AND check_token=?
                    """,
                    (_dt(now + timedelta(seconds=interval)), _dt(now), int(blocked), result.error_code, _dt(now), lease.repository_id, lease.token),
                )
                return None

            commit_sha = result.commit_sha
            if not commit_sha:
                raise InvalidInputError("successful sync must include commit_sha")
            previous = row["observed_commit"]
            changed = previous != commit_sha
            current = int(row["min_interval"] if changed or row["current_interval"] is None else min(int(row["current_interval"]) * 2, int(row["max_interval"])))
            last_change = now if changed else _decode_optional(row["last_change_observed_at"], "last_change_observed_at")
            connection.execute(
                """
                UPDATE code_map_repositories SET observed_commit=?, current_interval=?,
                    last_check_completed_at=?, last_change_observed_at=?, next_check_at=?, last_sync_at=?,
                    check_token=NULL, check_expires_at=NULL, blocked=0, blocked_reason=NULL, updated_at=?
                WHERE repository_id=? AND check_token=?
                """,
                (commit_sha, current, _dt(now), _dt(last_change), _dt(now + timedelta(seconds=current)), _dt(now), _dt(now), lease.repository_id, lease.token),
            )
            if not changed:
                return None
            parser_revision = "python-ast-v1"
            rules_digest = _rules_digest(json.loads(row["rules_json"]))
            return _insert_or_get_job(
                connection, repository_id=lease.repository_id, commit_sha=commit_sha,
                parser_revision=parser_revision, rules_digest=rules_digest,
                trigger=getattr(result, "trigger", "scheduled"), trace_context=getattr(result, "trace_context", None), now=now,
            )

    def enqueue(
        self, registration_id: str, commit_sha: str, trigger: str, trace: Any | None = None,
        *, parser_revision: str = "python-ast-v1", rules_digest: str | None = None,
    ) -> ScanJob:
        registration = self.get_registration(registration_id)
        now = self._now()
        digest = rules_digest or _rules_digest(dict(registration.rules))
        with self.database.transaction() as connection:
            return _insert_or_get_job(
                connection, repository_id=registration_id, commit_sha=commit_sha,
                parser_revision=parser_revision, rules_digest=digest,
                trigger=trigger, trace_context=trace, now=now,
            )

    def get_job(self, job_id: str) -> ScanJob:
        rows = self.database.query("SELECT * FROM code_map_scan_jobs WHERE job_id=?", (job_id,))
        if not rows:
            raise DomainError(f"unknown scan job: {job_id}")
        return _job_from_row(rows[0])

    def list_jobs(self, repository_id: str | None = None) -> list[ScanJob]:
        if repository_id is None:
            rows = self.database.query("SELECT * FROM code_map_scan_jobs ORDER BY created_at, job_id")
        else:
            rows = self.database.query("SELECT * FROM code_map_scan_jobs WHERE repository_id=? ORDER BY created_at, job_id", (repository_id,))
        return [_job_from_row(row) for row in rows]

    def pause(self, repository_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE code_map_repositories SET enabled=0, updated_at=? WHERE repository_id=?",
                (_dt(self._now()), repository_id),
            )
            connection.execute(
                "UPDATE code_map_scan_jobs SET status='cancelled', completed_at=? WHERE repository_id=? AND status IN ('queued','retry_wait')",
                (_dt(self._now()), repository_id),
            )

    def resume(self, repository_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE code_map_repositories SET enabled=1, blocked=0, blocked_reason=NULL, next_check_at=?, updated_at=? WHERE repository_id=?",
                (_dt(self._now()), _dt(self._now()), repository_id),
            )


def _insert_or_get_job(connection, *, repository_id: str, commit_sha: str, parser_revision: str, rules_digest: str, trigger: str, trace_context: Any, now: datetime) -> ScanJob:
    existing = connection.execute(
        "SELECT * FROM code_map_scan_jobs WHERE repository_id=? AND commit_sha=? AND parser_revision=? AND rules_digest=?",
        (repository_id, commit_sha, parser_revision, rules_digest),
    ).fetchone()
    if existing is not None:
        return _job_from_row(existing)
    job_id = f"scan:{snapshot_id_for(repository_id, commit_sha, parser_revision, rules_digest)}"
    connection.execute(
        """
        INSERT INTO code_map_scan_jobs(job_id,repository_id,commit_sha,parser_revision,rules_digest,trigger,status,attempt,trace_context_json,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (job_id, repository_id, commit_sha, parser_revision, rules_digest, trigger, "queued", 0, json.dumps(dict(trace_context or {}), sort_keys=True), _dt(now)),
    )
    return ScanJob(job_id=job_id, repository_id=repository_id, commit_sha=commit_sha, parser_revision=parser_revision, rules_digest=rules_digest, trigger=trigger, trace_context=dict(trace_context or {}), created_at=now)


def _job_from_row(row) -> ScanJob:
    return ScanJob(
        job_id=row["job_id"], repository_id=row["repository_id"], commit_sha=row["commit_sha"],
        parser_revision=row["parser_revision"], rules_digest=row["rules_digest"], trigger=row["trigger"],
        status=row["status"], attempt=int(row["attempt"]),
        next_attempt_at=_decode_optional(row["next_attempt_at"], "next_attempt_at"),
        lease_owner=row["lease_owner"], lease_token=row["lease_token"],
        lease_expires_at=_decode_optional(row["lease_expires_at"], "lease_expires_at"),
        trace_context=json.loads(row["trace_context_json"]), error_code=row["error_code"],
        error_message=row["error_message"], created_at=decode_datetime(row["created_at"], "created_at"),
        started_at=_decode_optional(row["started_at"], "started_at"), completed_at=_decode_optional(row["completed_at"], "completed_at"),
    )


def _rules_digest(rules: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise InvalidInputError("datetime must be timezone-aware UTC")
    return value


def _dt(value: datetime | None) -> str | None:
    return encode_datetime(_require_utc(value)) if value is not None else None


def _decode_optional(value: str | None, name: str) -> datetime | None:
    return decode_datetime(value, name) if value else None
