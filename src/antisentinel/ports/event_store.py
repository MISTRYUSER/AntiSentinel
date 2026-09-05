"""Port for append-only domain event storage."""

from __future__ import annotations

from typing import Protocol

from antisentinel.domain.event import Event


class EventStore(Protocol):
    def append(self, event: Event) -> None:
        """Append an event, treating an identical event ID as an idempotent replay."""

    def list_by_aggregate(self, aggregate_type: str, aggregate_id: str) -> list[Event]:
        """Return events for one aggregate in append order."""

    def list_by_correlation(self, correlation_id: str) -> list[Event]:
        """Return all events with one correlation ID in append order."""
