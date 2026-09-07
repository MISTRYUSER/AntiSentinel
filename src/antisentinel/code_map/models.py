"""Immutable domain values for code-map registration and snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.primitives import require_json, require_non_empty, require_utc, utc_now


def _utc_or_none(value: datetime | None, field_name: str) -> datetime | None:
    return require_utc(value, field_name) if value is not None else None


def _mapping(value: Mapping[str, Any] | None, field_name: str) -> Mapping[str, Any]:
    raw = dict(value or {})
    require_json(raw, field_name)
    return MappingProxyType(raw)


@dataclass(frozen=True)
class RepositoryBudget:
    max_files: int = 10_000
    max_file_bytes: int = 1_048_576
    max_text_bytes: int = 67_108_864

    def __post_init__(self) -> None:
        for name in ("max_files", "max_file_bytes", "max_text_bytes"):
            if not isinstance(getattr(self, name), int) or getattr(self, name) <= 0:
                raise InvalidInputError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RepositoryRegistration:
    repository_id: str
    remote_url: str
    credential_ref: str
    tracked_ref: str
    min_interval: int = 1_800
    max_interval: int = 14_400
    current_interval: int | None = None
    last_check_completed_at: datetime | None = None
    last_change_observed_at: datetime | None = None
    next_check_at: datetime | None = None
    last_check_at: datetime | None = None
    last_sync_at: datetime | None = None
    observed_commit: str | None = None
    rules: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    blocked: bool = False
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("repository_id", "remote_url", "credential_ref", "tracked_ref"):
            require_non_empty(getattr(self, name), name)
        if self.min_interval <= 0 or self.max_interval <= 0 or self.min_interval > self.max_interval:
            raise InvalidInputError("intervals must satisfy 0 < min_interval <= max_interval")
        if self.current_interval is not None and not self.min_interval <= self.current_interval <= self.max_interval:
            raise InvalidInputError("current_interval must be within configured interval bounds")
        object.__setattr__(self, "rules", _mapping(self.rules, "rules"))
        for name in ("last_check_completed_at", "last_change_observed_at", "next_check_at", "last_check_at", "last_sync_at"):
            _utc_or_none(getattr(self, name), name)


@dataclass(frozen=True)
class ScanJob:
    job_id: str
    repository_id: str
    commit_sha: str
    parser_revision: str
    rules_digest: str
    trigger: str
    status: str = "queued"
    attempt: int = 0
    next_attempt_at: datetime | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    trace_context: Mapping[str, str] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("job_id", "repository_id", "commit_sha", "parser_revision", "rules_digest", "trigger", "status"):
            require_non_empty(getattr(self, name), name)
        if self.attempt < 0:
            raise InvalidInputError("attempt must be non-negative")
        object.__setattr__(self, "trace_context", _mapping(self.trace_context, "trace_context"))
        for name in ("created_at", "next_attempt_at", "lease_expires_at", "started_at", "completed_at"):
            _utc_or_none(getattr(self, name), name)


@dataclass(frozen=True)
class MapSnapshot:
    snapshot_id: str
    repository_id: str
    commit_sha: str
    parser_revision: str
    rules_digest: str
    status: str = "building"
    generation: int = 1
    published_generation: int | None = None
    file_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    chunk_count: int = 0
    failed_files: tuple[str, ...] = ()
    excluded_files: tuple[str, ...] = ()
    logical_bytes: int = 0
    created_at: datetime = field(default_factory=utc_now)
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "repository_id", "commit_sha", "parser_revision", "rules_digest", "status"):
            require_non_empty(getattr(self, name), name)
        for name in ("generation", "file_count", "node_count", "edge_count", "chunk_count", "logical_bytes"):
            if getattr(self, name) < 0:
                raise InvalidInputError(f"{name} must be non-negative")
        if self.generation == 0:
            raise InvalidInputError("generation must be positive")
        _utc_or_none(self.created_at, "created_at")
        _utc_or_none(self.published_at, "published_at")


@dataclass(frozen=True)
class CodeNode:
    node_id: str
    snapshot_id: str
    repository_id: str
    commit_sha: str
    kind: str
    qualified_name: str
    path: str
    start_line: int
    end_line: int
    content_hash: str | None = None
    start_col: int | None = None
    end_col: int | None = None

    def __post_init__(self) -> None:
        for name in ("node_id", "snapshot_id", "repository_id", "commit_sha", "kind", "qualified_name", "path"):
            require_non_empty(getattr(self, name), name)
        if self.start_line < 1 or self.end_line < self.start_line:
            raise InvalidInputError("node line range must be 1-based and inclusive")
        for name in ("start_col", "end_col"):
            if getattr(self, name) is not None and getattr(self, name) < 0:
                raise InvalidInputError(f"{name} must be non-negative")


@dataclass(frozen=True)
class CodeEdge:
    edge_id: str
    snapshot_id: str
    source_node_id: str
    relation: str
    target_node_id: str | None = None
    unresolved_expression: str | None = None
    call_start_line: int | None = None
    call_end_line: int | None = None
    resolution: str = "resolved"
    basis: str = "ast"


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    node_id: str
    snapshot_id: str
    path: str
    start_line: int
    end_line: int
    byte_start: int
    byte_end: int
    content_hash: str
    commit_sha: str
    encoding: str = "utf-8"
    truncated: bool = False
    partial_line: bool = False

    def __post_init__(self) -> None:
        for name in ("chunk_id", "node_id", "snapshot_id", "path", "content_hash", "commit_sha"):
            require_non_empty(getattr(self, name), name)
        if self.start_line < 1 or self.end_line < self.start_line:
            raise InvalidInputError("chunk line range must be 1-based and inclusive")
        if self.byte_start < 0 or self.byte_end < self.byte_start:
            raise InvalidInputError("chunk byte range must be non-negative and ordered")


@dataclass(frozen=True)
class CodeMapError:
    code: str
    retryable: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_non_empty(self.code, "code")
        object.__setattr__(self, "details", _mapping(self.details, "details"))
