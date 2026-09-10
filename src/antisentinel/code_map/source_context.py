"""Hash-verified source Evidence for an incident-bound code snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

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
    byte_start: int | None = None
    byte_end: int | None = None
    published_generation: int | None = None


class SourceEvidenceService:
    """Read-only access to source facts; validated reads append internal Evidence."""
    def __init__(self, query, store, evidence_store) -> None:
        self.query = query
        self.store = store
        self.evidence_store = evidence_store

    def read_source(self, incident_id: str, repository_id: str, snapshot_id: str, chunk_id: str, *, max_bytes: int = 32 * 1024, expected_generation: int | None = None, expected_commit: str | None = None, expected_hash: str | None = None, expected_node_id: str | None = None, expected_path: str | None = None, expected_byte_start: int | None = None, expected_byte_end: int | None = None, expected_parent_hash: str | None = None) -> SourceContextSlice:
        generation, commit = self._bound_identity(incident_id, repository_id, snapshot_id)
        if (expected_generation is not None and expected_generation != generation) or (expected_commit is not None and expected_commit != commit):
            raise DomainError('scope_mismatch')
        reader = self.store.read_generation_utf8_chunk if expected_parent_hash is not None else self.store.read_generation_chunk
        item = reader(repository_id, snapshot_id, generation, chunk_id)
        if expected_parent_hash is not None:
            if expected_hash is None:
                raise DomainError('source_identity_missing')
            item = _source_slice(item, expected_byte_start, expected_byte_end, expected_parent_hash)
        if item['commit_sha'] != commit:
            raise DomainError('scope_mismatch')
        if expected_hash is not None and item['content_hash'] != expected_hash:
            raise DomainError('hash_mismatch')
        for key,expected in [('node_id',expected_node_id),('path',expected_path),('byte_start',expected_byte_start),('byte_end',expected_byte_end)]:
            if expected is not None and item.get(key)!=expected:raise DomainError('source_identity_mismatch')
        if len(item['content'].encode('utf-8')) > max_bytes:
            raise SourceBudgetExceeded('source_budget_exceeded')
        evidence = Evidence.create(
            kind="source_code", content_ref=f"code-map://{repository_id}/{snapshot_id}/{chunk_id}" + (f"#bytes={item['byte_start']}:{item['byte_end']}" if expected_parent_hash is not None else ''),
            content_hash=item["content_hash"], source=repository_id,
            metadata={"incident_id": incident_id, "repository_id": repository_id, "snapshot_id": snapshot_id, "generation": generation, "commit_sha": item['commit_sha'], "path": item["path"], "node_id":item['node_id'], "chunk_id": chunk_id, "byte_start": item['byte_start'], "byte_end": item['byte_end'], **({'parent_source_hash': expected_parent_hash} if expected_parent_hash is not None else {})},
        )
        self.evidence_store.put_once(evidence, incident_id=incident_id)
        return SourceContextSlice(incident_id, str(evidence.evidence_id), repository_id, snapshot_id, item['commit_sha'], item["path"], item["content"], item["content_hash"], int(item["byte_start"]), int(item["byte_end"]), generation)

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
            generation, commit = self._bound_identity(incident_id, repository_id, snapshot_id)
            if metadata.get('generation') is None or metadata.get('commit_sha') is None:
                raise DomainError('source_identity_missing')
            if (metadata['generation'], metadata['commit_sha']) != (generation, commit):
                raise DomainError('scope_mismatch')
            reader = self.store.read_generation_utf8_chunk if metadata.get('parent_source_hash') is not None else self.store.read_generation_chunk
            item = reader(repository_id, snapshot_id, generation, chunk_id)
            if metadata.get('parent_source_hash') is not None:
                item = _source_slice(item, metadata['byte_start'], metadata['byte_end'], metadata['parent_source_hash'])
            if item['content_hash'] != evidence.content_hash:
                raise DomainError('hash_mismatch')
            if any(metadata.get(k, item[k]) != item[k] for k in ('commit_sha', 'byte_start', 'byte_end','path','node_id')):
                raise DomainError('source_identity_mismatch')
            restored.append(SourceContextSlice(incident_id, str(evidence.evidence_id), repository_id, snapshot_id, item['commit_sha'], item['path'], item['content'], item['content_hash'], int(item['byte_start']), int(item['byte_end']), generation))
        return restored

    def _bound_identity(self, incident_id, repository_id, snapshot_id):
        bindings = self.store.database.query(
            'SELECT DISTINCT published_generation,requested_commit FROM code_map_diagnosis_bindings '
            'WHERE incident_id=? AND repository_id=? AND snapshot_id=?',
            (incident_id, repository_id, snapshot_id))
        if not bindings:
            raise DomainError('scope_mismatch')
        if len(bindings) != 1:
            raise DomainError('ambiguous_binding')
        return tuple(bindings[0])


class SourceBudgetExceeded(DomainError):
    """Verified source exceeds remaining budget; no Evidence was persisted."""


def _source_slice(parent, start, end, parent_hash):
    if parent.get('parent_source_hash', parent['content_hash']) != parent_hash:
        raise DomainError('parent_hash_mismatch')
    raw = parent['content'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != parent['content_hash']:
        raise DomainError('unsupported_source_encoding')
    if type(start) is not int or type(end) is not int or not 0 <= parent['byte_start'] <= start < end <= parent['byte_end'] or len(raw) != parent['byte_end']-parent['byte_start']:
        raise DomainError('source_range_mismatch')
    selected = raw[start-parent['byte_start']:end-parent['byte_start']]
    try:
        text = selected.decode('utf-8')
    except UnicodeDecodeError:
        raise DomainError('source_range_encoding_mismatch') from None
    return {**parent, 'content': text, 'content_hash': hashlib.sha256(selected).hexdigest(), 'byte_start': start, 'byte_end': end}
