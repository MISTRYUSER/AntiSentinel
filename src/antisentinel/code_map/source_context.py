"""Hash-verified source Evidence for an incident-bound code snapshot."""

from __future__ import annotations

from dataclasses import dataclass

from antisentinel.domain.errors import DomainError
from antisentinel.domain.evidence import Evidence


@dataclass(frozen=True)
class SourceContextSlice:
    incident_id: str
    evidence_id: str
    repository_id: str
    snapshot_id: str
    commit_sha: str
    path: str
    content: str
    content_hash: str


class SourceEvidenceService:
    def __init__(self, query, store, evidence_store) -> None:
        self.query = query
        self.store = store
        self.evidence_store = evidence_store

    def read_source(self, incident_id: str, repository_id: str, snapshot_id: str, chunk_id: str) -> SourceContextSlice:
        if not self.store.incident_binding_matches(incident_id, repository_id, snapshot_id):
            raise DomainError("scope_mismatch")
        scope = self.store.scope_for_incident(incident_id)
        result = self.query.read_source(scope, repository_id, snapshot_id, chunk_id)
        if result.error is not None or not result.items:
            raise DomainError((result.error.code if result.error else "source_not_found"))
        item = result.items[0]
        evidence = Evidence.create(
            kind="source_code", content_ref=f"code-map://{repository_id}/{snapshot_id}/{chunk_id}",
            content_hash=item["content_hash"], source=repository_id,
            metadata={"incident_id": incident_id, "repository_id": repository_id, "snapshot_id": snapshot_id, "commit_sha": result.indexed_commit, "path": item["path"], "chunk_id": chunk_id},
        )
        self.evidence_store.put_once(evidence, incident_id=incident_id)
        return SourceContextSlice(incident_id, str(evidence.evidence_id), repository_id, snapshot_id, result.indexed_commit or "", item["path"], item["content"], item["content_hash"])

    def rehydrate(self, references: list[dict]) -> list[SourceContextSlice]:
        restored: list[SourceContextSlice] = []
        for reference in references:
            evidence = self.evidence_store.get(reference["evidence_id"])
            if evidence is None or evidence.kind != "source_code":
                continue
            metadata = evidence.metadata
            incident_id = str(metadata["incident_id"])
            repository_id = str(metadata["repository_id"])
            snapshot_id = str(metadata["snapshot_id"])
            chunk_id = str(metadata["chunk_id"])
            scope = self.store.scope_for_incident(incident_id)
            result = self.query.read_source(scope, repository_id, snapshot_id, chunk_id)
            if result.error is not None or not result.items or result.items[0]["content_hash"] != evidence.content_hash:
                continue
            item = result.items[0]
            restored.append(SourceContextSlice(incident_id, str(evidence.evidence_id), repository_id, snapshot_id, result.indexed_commit or "", item["path"], item["content"], item["content_hash"]))
        return restored
