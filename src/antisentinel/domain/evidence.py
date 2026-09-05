"""Immutable references to externally stored evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from .errors import InvalidInputError
from .event import Event
from .ids import EvidenceId
from .primitives import (
    decode_datetime,
    encode_datetime,
    new_stable_id,
    require_json,
    require_non_empty,
    require_utc,
    utc_now,
)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: EvidenceId
    role: str | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.evidence_id, "evidence_id")
        if self.role is not None and (not isinstance(self.role, str) or not self.role.strip()):
            raise InvalidInputError("role must be non-empty when provided")


@dataclass(frozen=True)
class Evidence:
    evidence_id: EvidenceId
    kind: str
    content_ref: str
    content_hash: str | None
    source: str | None
    observed_at: datetime | None
    recorded_at: datetime
    metadata: Mapping[str, Any]
    pending_events: tuple[Event, ...] = field(default_factory=tuple, repr=False)

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        content_ref: str,
        content_hash: str | None = None,
        source: str | None = None,
        observed_at: datetime | None = None,
        recorded_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
        evidence_id: EvidenceId | str | None = None,
    ) -> "Evidence":
        require_non_empty(kind, "kind")
        require_non_empty(content_ref, "content_ref")
        if observed_at is not None:
            require_utc(observed_at, "observed_at")
        timestamp = require_utc(recorded_at or utc_now(), "recorded_at")
        raw_metadata = dict(metadata or {})
        require_json(raw_metadata, "metadata")
        evidence = cls(
            evidence_id=EvidenceId(evidence_id or new_stable_id()),
            kind=kind,
            content_ref=content_ref,
            content_hash=content_hash,
            source=source,
            observed_at=observed_at,
            recorded_at=timestamp,
            metadata=MappingProxyType(raw_metadata),
        )
        event = Event.create(
            type="evidence.recorded",
            aggregate_type="Evidence",
            aggregate_id=evidence.evidence_id,
            correlation_id=evidence.evidence_id,
            occurred_at=timestamp,
            payload={"kind": kind, "content_ref": content_ref},
        )
        object.__setattr__(evidence, "pending_events", (event,))
        return evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "content_ref": self.content_ref,
            "content_hash": self.content_hash,
            "source": self.source,
            "observed_at": encode_datetime(self.observed_at) if self.observed_at else None,
            "recorded_at": encode_datetime(self.recorded_at),
            "metadata": dict(self.metadata),
            "pending_events": [event.to_dict() for event in self.pending_events],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Evidence":
        if not isinstance(value, dict):
            raise InvalidInputError("evidence payload must be an object")
        try:
            evidence = cls(
                evidence_id=EvidenceId(require_non_empty(value["evidence_id"], "evidence_id")),
                kind=require_non_empty(value["kind"], "kind"),
                content_ref=require_non_empty(value["content_ref"], "content_ref"),
                content_hash=value.get("content_hash"),
                source=value.get("source"),
                observed_at=(
                    decode_datetime(value["observed_at"], "observed_at")
                    if value.get("observed_at")
                    else None
                ),
                recorded_at=decode_datetime(value["recorded_at"], "recorded_at"),
                metadata=MappingProxyType(dict(require_json(value.get("metadata", {}), "metadata"))),
            )
        except KeyError as exc:
            raise InvalidInputError(f"missing evidence field: {exc.args[0]}") from exc
        events = tuple(
            Event(
                event_id=item["event_id"],
                type=item["type"],
                aggregate_type=item["aggregate_type"],
                aggregate_id=item["aggregate_id"],
                correlation_id=item["correlation_id"],
                occurred_at=decode_datetime(item["occurred_at"], "occurred_at"),
                payload=item["payload"],
            )
            for item in value.get("pending_events", [])
        )
        object.__setattr__(evidence, "pending_events", events)
        return evidence
