import pytest


def test_source_evidence_requires_incident_binding_and_persists_hash_verified_slice(tmp_path):
    from antisentinel.code_map.source_context import SourceEvidenceService
    from antisentinel.domain.errors import DomainError
    from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
    from tests.test_code_map_query import build_query

    query, scope, snapshot_id, symbols = build_query(tmp_path)
    chunk_id = query.store.database.query("SELECT chunk_id FROM code_map_chunks WHERE node_id=?", (symbols[1].node_id,))[0]["chunk_id"]
    evidence_store = SQLiteEvidenceStore(query.store.database)
    service = SourceEvidenceService(query, query.store, evidence_store)

    with pytest.raises(DomainError, match="scope_mismatch"):
        service.read_source("incident-a", "repo-a", snapshot_id, chunk_id)

    query.store.bind_incident("incident-a", "repo-a", snapshot_id)
    source = service.read_source("incident-a", "repo-a", snapshot_id, chunk_id)

    assert source.content == "    def run(self):\n        return 1\n"
    assert evidence_store.get(source.evidence_id).content_hash == source.content_hash
