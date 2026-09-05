"""Port for immutable evidence storage."""

from __future__ import annotations

from typing import Protocol

from antisentinel.domain.evidence import Evidence


class EvidenceStore(Protocol):
    def put_once(self, evidence: Evidence, *, incident_id: str | None = None) -> None:
        """Store evidence once; matching duplicate writes are idempotent."""

    def get(self, evidence_id: str) -> Evidence | None:
        """Load evidence by ID."""
