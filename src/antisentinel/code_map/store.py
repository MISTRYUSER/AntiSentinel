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
from .models import CodeMapError, MapSnapshot, RepositoryRegistration, ScanJob


@dataclass(frozen=True)
class CheckLease:
    repository_id: str
    owner: str
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class JobLease:
    job_id: str
    repository_id: str
    commit_sha: str
    parser_revision: str
    rules_digest: str
    owner: str
    token: str
    expires_at: datetime
    attempt: int


@dataclass(frozen=True)
class StagedMapRows:
    nodes: tuple[Any, ...] = ()
    edges: tuple[Any, ...] = ()
    chunks: tuple[Any, ...] = ()


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    error: CodeMapError | None = None
    snapshot: MapSnapshot | None = None


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

    def claim_job(self, owner: str, now: datetime) -> JobLease | None:
        now = _require_utc(now)
        token = str(uuid4())
        expires_at = now + timedelta(seconds=self.lease_seconds)
        with self.database.transaction() as connection:
            slot = connection.execute(
                "SELECT owner, lease_expires_at FROM code_map_worker_slots WHERE slot_name='global'"
            ).fetchone()
            if slot is not None and slot["lease_expires_at"] and slot["lease_expires_at"] > _dt(now):
                return None
            row = connection.execute(
                """
                SELECT * FROM code_map_scan_jobs
                WHERE status IN ('queued','retry_wait')
                   OR (status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                ORDER BY created_at, job_id LIMIT 1
                """,
                (_dt(now),),
            ).fetchone()
            if row is None:
                return None
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                INSERT INTO code_map_worker_slots(slot_name, owner, lease_token, lease_expires_at)
                VALUES('global',?,?,?)
                ON CONFLICT(slot_name) DO UPDATE SET owner=excluded.owner, lease_token=excluded.lease_token, lease_expires_at=excluded.lease_expires_at
                """,
                (owner, token, _dt(expires_at)),
            )
            connection.execute(
                """
                UPDATE code_map_scan_jobs SET status='running', attempt=?, lease_owner=?, lease_token=?, lease_expires_at=?,
                    started_at=COALESCE(started_at,?), error_code=NULL, error_message=NULL
                WHERE job_id=? AND (status IN ('queued','retry_wait') OR lease_expires_at<=?)
                """,
                (attempt, owner, token, _dt(expires_at), _dt(now), row["job_id"], _dt(now)),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO code_map_job_attempts(
                    attempt_id, job_id, attempt, status, trace_context_json, started_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (f"{row['job_id']}:{attempt}", row["job_id"], attempt, "running", row["trace_context_json"], _dt(now)),
            )
        return JobLease(
            job_id=row["job_id"], repository_id=row["repository_id"], commit_sha=row["commit_sha"],
            parser_revision=row["parser_revision"], rules_digest=row["rules_digest"], owner=owner,
            token=token, expires_at=expires_at, attempt=attempt,
        )

    def heartbeat(self, lease: JobLease, now: datetime) -> bool:
        now = _require_utc(now)
        expires_at = now + timedelta(seconds=self.lease_seconds)
        with self.database.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE code_map_scan_jobs SET lease_expires_at=?
                WHERE job_id=? AND lease_owner=? AND lease_token=? AND status='running' AND lease_expires_at>?
                """,
                (_dt(expires_at), lease.job_id, lease.owner, lease.token, _dt(now)),
            ).rowcount
            slot_updated = connection.execute(
                """
                UPDATE code_map_worker_slots SET lease_expires_at=?
                WHERE slot_name='global' AND owner=? AND lease_token=? AND lease_expires_at>?
                """,
                (_dt(expires_at), lease.owner, lease.token, _dt(now)),
            ).rowcount
        return updated == 1 and slot_updated == 1

    def empty_staged_rows(self) -> StagedMapRows:
        return StagedMapRows()

    def publish(self, lease: JobLease, snapshot: MapSnapshot, staged_rows: StagedMapRows) -> PublishResult:
        now = self._now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT job_id FROM code_map_scan_jobs
                WHERE job_id=? AND lease_owner=? AND lease_token=? AND status='running' AND lease_expires_at>?
                """,
                (lease.job_id, lease.owner, lease.token, _dt(now)),
            ).fetchone()
            slot = connection.execute(
                "SELECT slot_name FROM code_map_worker_slots WHERE slot_name='global' AND owner=? AND lease_token=? AND lease_expires_at>?",
                (lease.owner, lease.token, _dt(now)),
            ).fetchone()
            if row is None or slot is None:
                return PublishResult(False, CodeMapError("lease_lost", False, {"job_id": lease.job_id}))
            connection.execute(
                """
                INSERT INTO code_map_snapshots(
                    snapshot_id,repository_id,commit_sha,parser_revision,rules_digest,status,generation,published_generation,
                    file_count,node_count,edge_count,chunk_count,failed_files_json,excluded_files_json,logical_bytes,created_at,published_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_id) DO UPDATE SET status=excluded.status, published_generation=excluded.published_generation, published_at=excluded.published_at
                """,
                (
                    snapshot.snapshot_id, snapshot.repository_id, snapshot.commit_sha, snapshot.parser_revision,
                    snapshot.rules_digest, "ready", snapshot.generation, snapshot.generation,
                    snapshot.file_count, len(staged_rows.nodes), len(staged_rows.edges), len(staged_rows.chunks),
                    json.dumps(list(snapshot.failed_files)), json.dumps(list(snapshot.excluded_files)), snapshot.logical_bytes,
                    _dt(snapshot.created_at), _dt(now),
                ),
            )
            for node in staged_rows.nodes:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO code_map_nodes(
                        node_id,snapshot_id,repository_id,commit_sha,kind,qualified_name,path,start_line,end_line,start_col,end_col,content_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        node.node_id, node.snapshot_id, snapshot.repository_id, snapshot.commit_sha, node.kind,
                        node.qualified_name, node.path, node.start_line, node.end_line, node.start_col, node.end_col, node.content_hash,
                    ),
                )
            for edge in staged_rows.edges:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO code_map_edges(
                        edge_id,snapshot_id,source_node_id,relation,target_node_id,unresolved_expression,call_start_line,call_end_line,resolution,basis
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        edge.edge_id, edge.snapshot_id, edge.source_node_id, edge.relation, edge.target_node_id,
                        edge.unresolved_expression, edge.call_start_line, edge.call_end_line, edge.resolution, edge.basis,
                    ),
                )
            for chunk in staged_rows.chunks:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO code_map_chunks(
                        chunk_id,node_id,snapshot_id,path,start_line,end_line,byte_start,byte_end,content_hash,commit_sha,encoding,truncated,partial_line
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        chunk.chunk_id, chunk.node_id, chunk.snapshot_id, chunk.path, chunk.start_line, chunk.end_line,
                        chunk.byte_start, chunk.byte_end, chunk.content_hash, snapshot.commit_sha, chunk.encoding,
                        int(chunk.truncated), int(chunk.partial_line),
                    ),
                )
            connection.execute(
                """
                UPDATE code_map_scan_jobs SET status='succeeded', completed_at=?, lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL
                WHERE job_id=? AND lease_owner=? AND lease_token=?
                """,
                (_dt(now), lease.job_id, lease.owner, lease.token),
            )
            connection.execute(
                "UPDATE code_map_job_attempts SET status='succeeded', completed_at=? WHERE job_id=? AND attempt=?",
                (_dt(now), lease.job_id, lease.attempt),
            )
            connection.execute(
                "UPDATE code_map_worker_slots SET owner=NULL, lease_token=NULL, lease_expires_at=NULL WHERE slot_name='global' AND owner=? AND lease_token=?",
                (lease.owner, lease.token),
            )
        return PublishResult(True, snapshot=snapshot)

    def get_snapshot(self, snapshot_id: str) -> MapSnapshot | None:
        rows = self.database.query("SELECT * FROM code_map_snapshots WHERE snapshot_id=?", (snapshot_id,))
        if not rows:
            return None
        row = rows[0]
        return MapSnapshot(
            snapshot_id=row["snapshot_id"], repository_id=row["repository_id"], commit_sha=row["commit_sha"],
            parser_revision=row["parser_revision"], rules_digest=row["rules_digest"], status=row["status"],
            generation=int(row["generation"]), published_generation=row["published_generation"],
            file_count=int(row["file_count"]), node_count=int(row["node_count"]), edge_count=int(row["edge_count"]),
            chunk_count=int(row["chunk_count"]), failed_files=tuple(json.loads(row["failed_files_json"])),
            excluded_files=tuple(json.loads(row["excluded_files_json"])), logical_bytes=int(row["logical_bytes"]),
            created_at=decode_datetime(row["created_at"], "created_at"),
            published_at=_decode_optional(row["published_at"], "published_at"),
        )

    def get_published_snapshot(self, repository_id: str, commit_sha: str, allow_partial: bool = False) -> MapSnapshot | None:
        states = ("ready", "partial") if allow_partial else ("ready",)
        placeholders = ",".join("?" for _ in states)
        rows = self.database.query(
            f"SELECT snapshot_id FROM code_map_snapshots WHERE repository_id=? AND commit_sha=? AND status IN ({placeholders}) ORDER BY published_at DESC LIMIT 1",
            (repository_id, commit_sha, *states),
        )
        return self.get_snapshot(rows[0]["snapshot_id"]) if rows else None

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
