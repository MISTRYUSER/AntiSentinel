"""Append-only domain event value object."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from .ids import EventId
from .primitives import (
    decode_datetime,
    encode_datetime,
    new_stable_id,
    require_json,
    require_non_empty,
    require_utc,
)


@dataclass(frozen=True)
class Event:
    event_id: EventId
    type: str
    aggregate_type: str
    aggregate_id: str
    correlation_id: str
    occurred_at: datetime
    payload: Mapping[str, Any]
    causation_id: EventId | None = None
    related_ids: Mapping[str, str] = MappingProxyType({})

    @classmethod
    def create(
        cls,
        *,
        type: str,
        aggregate_type: str,
        aggregate_id: str,
        correlation_id: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        causation_id: EventId | None = None,
        related_ids: Mapping[str, str] | None = None,
    ) -> "Event":
        require_non_empty(type, "type")
        require_non_empty(aggregate_type, "aggregate_type")
        require_non_empty(aggregate_id, "aggregate_id")
        require_non_empty(correlation_id, "correlation_id")
        require_utc(occurred_at, "occurred_at")
        require_json(payload, "payload")
        if causation_id is not None:
            require_non_empty(causation_id, "causation_id")
        safe_related_ids = dict(related_ids or {})
        if any(not isinstance(key, str) or not key.strip() for key in safe_related_ids):
            raise ValueError("related_ids keys must be non-empty strings")
        for key, value in safe_related_ids.items():
            require_non_empty(value, f"related_ids.{key}")
        return cls(
            event_id=EventId(new_stable_id()),
            type=type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            occurred_at=occurred_at,
            payload=MappingProxyType(dict(payload)),
            causation_id=causation_id,
            related_ids=MappingProxyType(safe_related_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "correlation_id": self.correlation_id,
            "occurred_at": encode_datetime(self.occurred_at),
            "payload": dict(self.payload),
            "causation_id": self.causation_id,
            "related_ids": dict(self.related_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Event":
        if not isinstance(value, dict):
            raise ValueError("event payload must be an object")
        try:
            return cls(
                event_id=EventId(require_non_empty(value["event_id"], "event_id")),
                type=require_non_empty(value["type"], "type"),
                aggregate_type=require_non_empty(value["aggregate_type"], "aggregate_type"),
                aggregate_id=require_non_empty(value["aggregate_id"], "aggregate_id"),
                correlation_id=require_non_empty(value["correlation_id"], "correlation_id"),
                occurred_at=decode_datetime(value["occurred_at"], "occurred_at"),
                payload=MappingProxyType(dict(require_json(value["payload"], "payload"))),
                causation_id=(
                    EventId(require_non_empty(value["causation_id"], "causation_id"))
                    if value.get("causation_id")
                    else None
                ),
                related_ids=MappingProxyType(dict(value.get("related_ids", {}))),
            )
        except KeyError as exc:
            raise ValueError(f"missing event field: {exc.args[0]}") from exc
