"""Primary persistence with observable JSONL audit degradation."""

from __future__ import annotations

from typing import Any, Callable


class AuditedEventStore:
    def __init__(self, primary: Any, audit: Any, on_degraded: Callable[[str], None]) -> None:
        self.primary, self.audit, self.on_degraded = primary, audit, on_degraded

    def append(self, event) -> None:
        self.primary.append(event)
        try:
            self.audit.append(event)
        except Exception as exc:  # noqa: BLE001 - primary commit remains authoritative
            self.on_degraded(f"event audit failed for {event.event_id}: {exc}")

    def list_by_aggregate(self, aggregate_type: str, aggregate_id: str):
        return self.primary.list_by_aggregate(aggregate_type, aggregate_id)

    def list_by_correlation(self, correlation_id: str):
        return self.primary.list_by_correlation(correlation_id)


class AuditedEvidenceStore:
    def __init__(self, primary: Any, audit: Any, on_degraded: Callable[[str], None]) -> None:
        self.primary, self.audit, self.on_degraded = primary, audit, on_degraded

    def put_once(self, evidence, *, incident_id: str | None = None) -> None:
        self.primary.put_once(evidence, incident_id=incident_id)
        try:
            self.audit.put_once(evidence, incident_id=incident_id)
        except Exception as exc:  # noqa: BLE001
            self.on_degraded(f"evidence audit failed for {evidence.evidence_id}: {exc}")

    def get(self, evidence_id: str):
        return self.primary.get(evidence_id)


class AuditedMemoryStore:
    def __init__(self, primary: Any, audit: Any, on_degraded: Callable[[str], None]) -> None:
        self.primary, self.audit, self.on_degraded = primary, audit, on_degraded

    def append(self, record: dict[str, Any]) -> None:
        self.primary.append(record)
        try:
            self.audit.append(record)
        except Exception as exc:  # noqa: BLE001
            self.on_degraded(f"memory audit failed for {record.get('memory_id')}: {exc}")

    def replace(self, record: dict[str, Any]) -> None:
        self.primary.replace(record)
        try:
            self.audit.replace(record)
        except Exception as exc:  # noqa: BLE001
            self.on_degraded(f"memory audit failed for {record.get('memory_id')}: {exc}")

    def get(self, memory_id: str):
        return self.primary.get(memory_id)

    def list_by_operator(self, operator_id: str):
        return self.primary.list_by_operator(operator_id)

    def list_by_type(self, memory_type: str):
        return self.primary.list_by_type(memory_type)
