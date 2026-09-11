"""Typed memory contracts exchanged between persistence and runtime recall."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4


MemoryLifecycle = Literal["active", "superseded", "retracted", "conflict", "pending", "failed"]
MemorySourceType = Literal["event", "evidence", "session", "turn", "unknown"]
MemoryVisibility = Literal["session", "incident", "agent", "operator", "tenant"]
_MEMORY_LIFECYCLES = frozenset({"active", "superseded", "retracted", "conflict", "pending", "failed"})
_MEMORY_SOURCE_TYPES = frozenset({"event", "evidence", "session", "turn", "unknown"})
_MEMORY_VISIBILITIES = frozenset({"session", "incident", "agent", "operator", "tenant"})
DEFAULT_TENANT_ID = "default"
DEFAULT_AGENT_ID = "diagnosis-agent"
_FORBIDDEN_WRITE_SCOPE_TOKENS = frozenset({"unknown", "legacy-unscoped"})


class MissingMemoryScopeError(ValueError):
    """Raised when a memory write/recall lacks a fail-closed identity scope."""


def require_scope_id(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MissingMemoryScopeError(f"{name} is required")
    cleaned = value.strip()
    if cleaned.lower() in _FORBIDDEN_WRITE_SCOPE_TOKENS:
        raise MissingMemoryScopeError(f"{name} must not use reserved placeholder {cleaned!r}")
    return cleaned


def new_memory_id() -> str:
    """Globally unique durable memory identity (scope stays in separate fields)."""
    return f"mem_{uuid4().hex}"


@dataclass(frozen=True)
class MemorySourceRef:
    ref_type: MemorySourceType
    ref_id: str
    role: str | None = None
    content_hash: str | None = None
    content_version: int | None = None

    def __post_init__(self) -> None:
        if self.ref_type not in _MEMORY_SOURCE_TYPES:
            raise ValueError("invalid memory source type")
        if not isinstance(self.ref_id, str) or not self.ref_id.strip():
            raise ValueError("source reference id is required")
        if self.ref_type == "evidence" and (not self.content_hash or self.content_version is None):
            raise ValueError("evidence source requires content_hash and content_version")
        if self.content_version is not None and (isinstance(self.content_version, bool) or not isinstance(self.content_version, int) or self.content_version < 1):
            raise ValueError("source content_version must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "type": self.ref_type,
            "id": self.ref_id,
        }
        if self.role is not None:
            value["role"] = self.role
        if self.content_hash is not None:
            value["content_hash"] = self.content_hash
        if self.content_version is not None:
            value["content_version"] = self.content_version
        return value

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "MemorySourceRef":
        return cls(
            ref_type=value["type"],  # type: ignore[arg-type]
            ref_id=value["id"],  # type: ignore[arg-type]
            role=value.get("role"),  # type: ignore[arg-type]
            content_hash=value.get("content_hash"),  # type: ignore[arg-type]
            content_version=value.get("content_version"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class MemoryNamespace:
    """Canonical long-term memory isolation key (namespace-first, not post-filter)."""

    tenant_id: str
    operator_id: str
    agent_id: str
    memory_type: str | None = None
    incident_id: str | None = None
    session_id: str | None = None
    visibility: MemoryVisibility | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", require_scope_id("tenant_id", self.tenant_id))
        object.__setattr__(self, "operator_id", require_scope_id("operator_id", self.operator_id))
        object.__setattr__(self, "agent_id", require_scope_id("agent_id", self.agent_id))
        if self.incident_id is not None:
            object.__setattr__(self, "incident_id", require_scope_id("incident_id", self.incident_id))
        if self.session_id is not None:
            object.__setattr__(self, "session_id", require_scope_id("session_id", self.session_id))
        if self.memory_type is not None and (not isinstance(self.memory_type, str) or not self.memory_type.strip()):
            raise MissingMemoryScopeError("memory_type must be a non-empty string when provided")
        if self.visibility is not None and self.visibility not in _MEMORY_VISIBILITIES:
            raise ValueError("invalid memory visibility")

    def path(self) -> str:
        parts = [self.tenant_id, self.operator_id, self.agent_id]
        if self.memory_type:
            parts.append(self.memory_type)
        if self.incident_id:
            parts.append(self.incident_id)
        if self.session_id:
            parts.append(self.session_id)
        return "/".join(parts)


@dataclass(frozen=True)
class SessionKey:
    """In-process short-term session tree key (never bare session_id alone)."""

    tenant_id: str
    operator_id: str
    agent_id: str
    session_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", require_scope_id("tenant_id", self.tenant_id))
        object.__setattr__(self, "operator_id", require_scope_id("operator_id", self.operator_id))
        object.__setattr__(self, "agent_id", require_scope_id("agent_id", self.agent_id))
        object.__setattr__(self, "session_id", require_scope_id("session_id", self.session_id))


@dataclass(frozen=True)
class AuthorizedMemoryScope:
    operator_id: str
    incident_id: str
    session_id: str
    tenant_id: str = DEFAULT_TENANT_ID
    agent_id: str = DEFAULT_AGENT_ID

    def __post_init__(self) -> None:
        object.__setattr__(self, "operator_id", require_scope_id("operator_id", self.operator_id))
        object.__setattr__(self, "incident_id", require_scope_id("incident_id", self.incident_id))
        object.__setattr__(self, "session_id", require_scope_id("session_id", self.session_id))
        object.__setattr__(self, "tenant_id", require_scope_id("tenant_id", self.tenant_id))
        object.__setattr__(self, "agent_id", require_scope_id("agent_id", self.agent_id))

    def to_namespace(self, *, memory_type: str | None = None, visibility: MemoryVisibility | None = None) -> MemoryNamespace:
        return MemoryNamespace(
            tenant_id=self.tenant_id,
            operator_id=self.operator_id,
            agent_id=self.agent_id,
            memory_type=memory_type,
            incident_id=self.incident_id,
            session_id=self.session_id,
            visibility=visibility,
        )

    def session_key(self) -> SessionKey:
        return SessionKey(self.tenant_id, self.operator_id, self.agent_id, self.session_id)


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    memory_type: str
    operator_id: str
    incident_id: str | None
    session_id: str | None
    content: str
    source_refs: tuple[MemorySourceRef, ...]
    extraction_confidence: float
    valid_from: datetime
    status: MemoryLifecycle = "active"
    valid_to: datetime | None = None
    content_version: int = 1
    schema_version: int = 2
    extractor_revision: str = "rule-v1"
    reason_code: str = ""
    outcome: str | None = None
    tenant_id: str = DEFAULT_TENANT_ID
    agent_id: str = DEFAULT_AGENT_ID
    visibility: MemoryVisibility = "operator"

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.memory_id, self.memory_type, self.operator_id, self.content, self.extractor_revision)):
            raise ValueError("memory_id, memory_type, operator_id, content, and extractor_revision are required")
        # Records may still contain legacy-unscoped for migration reads; "unknown" is never valid.
        if self.operator_id.strip().lower() == "unknown":
            raise MissingMemoryScopeError("operator_id must not use reserved placeholder 'unknown'")
        if self.tenant_id.strip().lower() == "unknown" or self.agent_id.strip().lower() == "unknown":
            raise MissingMemoryScopeError("tenant_id/agent_id must not use reserved placeholder 'unknown'")
        if not self.tenant_id.strip() or not self.agent_id.strip():
            raise MissingMemoryScopeError("tenant_id and agent_id are required")
        if self.visibility not in _MEMORY_VISIBILITIES:
            raise ValueError("invalid memory visibility")
        if self.status not in _MEMORY_LIFECYCLES:
            raise ValueError("invalid memory lifecycle")
        if isinstance(self.extraction_confidence, bool) or not isinstance(self.extraction_confidence, (int, float)) or not 0 <= self.extraction_confidence <= 1:
            raise ValueError("extraction_confidence must be between 0 and 1")
        if isinstance(self.content_version, bool) or not isinstance(self.content_version, int) or self.content_version < 1:
            raise ValueError("content_version must be a positive integer")
        if self.schema_version != 2:
            raise ValueError("unsupported memory schema_version")
        if self.valid_from.tzinfo is None or (self.valid_to is not None and self.valid_to.tzinfo is None):
            raise ValueError("memory validity timestamps must be timezone-aware")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")

    @property
    def confidence(self) -> float:
        """Compatibility value for the existing persistence/ranking column."""
        return float(self.extraction_confidence)

    def namespace(self) -> MemoryNamespace:
        return MemoryNamespace(
            tenant_id=self.tenant_id,
            operator_id=self.operator_id,
            agent_id=self.agent_id,
            memory_type=self.memory_type,
            incident_id=self.incident_id,
            session_id=self.session_id,
            visibility=self.visibility,
        )

    @classmethod
    def create(cls, **kwargs: object) -> "MemoryRecord":
        return cls(**kwargs)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "operator_id": self.operator_id,
            "incident_id": self.incident_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "visibility": self.visibility,
            "content": self.content,
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "extraction_confidence": self.extraction_confidence,
            "confidence": self.confidence,
            "valid_from": self.valid_from.astimezone(timezone.utc).isoformat(),
            "valid_to": self.valid_to.astimezone(timezone.utc).isoformat() if self.valid_to else None,
            "status": self.status,
            "content_version": self.content_version,
            "schema_version": self.schema_version,
            "extractor_revision": self.extractor_revision,
            "reason_code": self.reason_code,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "MemoryRecord":
        return cls(
            memory_id=value["memory_id"], memory_type=value["memory_type"], operator_id=value["operator_id"],
            incident_id=value.get("incident_id"), session_id=value.get("session_id"), content=value["content"],
            source_refs=tuple(MemorySourceRef.from_dict(ref) for ref in value["source_refs"]),  # type: ignore[arg-type]
            extraction_confidence=value.get("extraction_confidence", value.get("confidence")),
            valid_from=datetime.fromisoformat(value["valid_from"].replace("Z", "+00:00")),  # type: ignore[union-attr]
            valid_to=datetime.fromisoformat(value["valid_to"].replace("Z", "+00:00")) if value.get("valid_to") else None,  # type: ignore[union-attr]
            status=value.get("status", "active"), content_version=value.get("content_version", 1),
            schema_version=value.get("schema_version", 2), extractor_revision=value.get("extractor_revision", "rule-v1"),
            reason_code=value.get("reason_code", ""), outcome=value.get("outcome"),
            tenant_id=value.get("tenant_id", DEFAULT_TENANT_ID), agent_id=value.get("agent_id", DEFAULT_AGENT_ID),
            visibility=value.get("visibility", "operator"),
        )  # type: ignore[arg-type]

    @classmethod
    def from_legacy_or_dict(cls, value: dict[str, object]) -> "MemoryRecord":
        if value.get("schema_version") == 2:
            return cls.from_dict(value)
        raw_refs = value.get("source_refs", ())
        source_refs: list[MemorySourceRef] = []
        if isinstance(raw_refs, (list, tuple)):
            for raw_ref in raw_refs:
                if not isinstance(raw_ref, dict) or not raw_ref.get("id"):
                    continue
                ref_type = raw_ref.get("type")
                if ref_type == "evidence" and raw_ref.get("content_hash") and raw_ref.get("content_version") is not None:
                    source_refs.append(MemorySourceRef("evidence", str(raw_ref["id"]), raw_ref.get("role"), raw_ref.get("content_hash"), raw_ref.get("content_version")))
                elif ref_type in {"event", "session", "turn"}:
                    source_refs.append(MemorySourceRef(ref_type, str(raw_ref["id"]), raw_ref.get("role")))
                else:
                    source_refs.append(MemorySourceRef("unknown", str(raw_ref["id"])))
        if not source_refs:
            source_refs = [MemorySourceRef("unknown", str(source_id)) for source_id in value.get("source_ids", ()) if str(source_id).strip()]
        raw_valid_from = value.get("valid_from") or value.get("created_at") or "1970-01-01T00:00:00+00:00"
        valid_from = raw_valid_from if isinstance(raw_valid_from, datetime) else datetime.fromisoformat(str(raw_valid_from).replace("Z", "+00:00"))
        raw_valid_to = value.get("valid_to")
        valid_to = raw_valid_to if isinstance(raw_valid_to, datetime) else (datetime.fromisoformat(str(raw_valid_to).replace("Z", "+00:00")) if raw_valid_to else None)
        text = value.get("content") or value.get("text") or " ".join(str(value.get(key, "")) for key in ("subject", "predicate", "object") if value.get(key)) or "[legacy memory without content]"
        confidence = value.get("extraction_confidence", value.get("confidence", 0.0))
        # Legacy rows without operator stay readable for migration, but cannot be written as "unknown".
        operator_id = str(value.get("operator_id") or value.get("owner_id") or "legacy-unscoped")
        return cls(
            memory_id=str(value["memory_id"]), memory_type=str(value.get("memory_type", "unknown")),
            operator_id=operator_id,
            incident_id=str(value["incident_id"]) if value.get("incident_id") is not None else None,
            session_id=str(value["session_id"]) if value.get("session_id") is not None else None,
            content=str(text), source_refs=tuple(source_refs), extraction_confidence=confidence,
            valid_from=valid_from, valid_to=valid_to,
            status=value.get("status") if value.get("status") in _MEMORY_LIFECYCLES else "pending",
            content_version=int(value.get("content_version", 1)), extractor_revision=str(value.get("extractor_revision", value.get("model_version", "legacy-v1"))),
            reason_code=str(value.get("reason_code", "legacy_record")), outcome=str(value["outcome"]) if value.get("outcome") is not None else None,
            tenant_id=str(value.get("tenant_id", DEFAULT_TENANT_ID)), agent_id=str(value.get("agent_id", DEFAULT_AGENT_ID)),
            visibility=value.get("visibility", "operator") if value.get("visibility") in _MEMORY_VISIBILITIES else "operator",  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class MemoryScope:
    kind: str
    scope_id: str


@dataclass(frozen=True)
class MemoryContextView:
    session_id: str | None = None
    digest: str = ""
    evidence_refs: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()
    estimated_tokens: int = 0

    def with_digest(self, digest: str, evidence_refs: list[str] | tuple[str, ...] = (), memory_ids: list[str] | tuple[str, ...] = ()) -> "MemoryContextView":
        return replace(self, digest=digest, evidence_refs=tuple(evidence_refs), memory_ids=tuple(memory_ids))
