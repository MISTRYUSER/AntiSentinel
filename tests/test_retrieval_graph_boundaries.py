from dataclasses import replace
import json

import pytest

from antisentinel.code_map.source_context import SourceEvidenceService
from antisentinel.domain.errors import DomainError
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
from antisentinel.retrieval.evidence import CodeEvidenceAssembler
from antisentinel.retrieval.graph import GraphExpander, GraphSeed
from tests.test_code_map_query import build_query


def setup_source(tmp_path):
    query, _, sid, symbols = build_query(tmp_path)
    query.store.bind_incident('incident-a', 'repo-a', sid)
    scope = dict(repository_id='repo-a', snapshot_id=sid, published_generation=1, commit_sha='a' * 40)
    chunk = query.store.read_generation(sid, 1)['chunks'][1]
    source = SourceEvidenceService(query, query.store, SQLiteEvidenceStore(query.store.database))
    return query.store, scope, symbols, chunk, source


def test_graph_reads_archived_generation_not_mutable_nodes(tmp_path):
    store, scope, symbols, _, _ = setup_source(tmp_path)
    with store.database.transaction() as connection:
        connection.execute("UPDATE code_map_nodes SET qualified_name='wrong-current-generation'")
        connection.execute('DELETE FROM code_map_edges')
    result = GraphExpander(store).expand([GraphSeed(symbols[0].node_id, 'doc', 1)], scope=scope)
    assert [item.qualified_name for item in result.items] == ['Service.run']
    assert result.items[0].published_generation == 1
    assert result.items[0].edge_id


@pytest.mark.parametrize('field,value', [('published_generation', 99), ('repository_id', 'foreign'), ('commit_sha', 'b' * 40)])
def test_graph_rejects_wrong_generation_or_scope(tmp_path, field, value):
    store, scope, symbols, _, _ = setup_source(tmp_path)
    with pytest.raises(DomainError):
        GraphExpander(store).expand([GraphSeed(symbols[0].node_id, 'doc', 1)], scope={**scope, field: value})


def test_resolved_calls_do_not_bypass_relation_validation(tmp_path):
    store, scope, symbols, _, _ = setup_source(tmp_path)
    payload = store.read_generation(scope['snapshot_id'], 1)
    payload['edges'] = [dict(payload['edges'][0], relation='calls', resolution='resolved', basis='ast')]
    with store.database.transaction() as connection:
        connection.execute('UPDATE code_map_generations SET payload_json=?', (json.dumps(payload),))
    result = GraphExpander(store).expand([GraphSeed(symbols[0].node_id, 'doc', 1)], scope=scope)
    assert result.items == ()
    assert result.inspected_edges == 0  # The default query only selects contains.
    with pytest.raises(ValueError, match='relation'):
        GraphExpander(store).expand([GraphSeed(symbols[0].node_id, 'doc', 1)], scope=scope, relations=('calls',))


def test_budget_rejection_does_not_persist_evidence(tmp_path):
    store, scope, _, chunk, source = setup_source(tmp_path)
    candidates = [{**scope, 'chunk_id': chunk['chunk_id']}]
    result = CodeEvidenceAssembler(source).select('incident-a', candidates, max_bytes=1)
    assert result.slices == () and result.truncated
    assert store.database.query('SELECT * FROM evidence') == []


@pytest.mark.parametrize('field,value', [('published_generation', 2), ('commit_sha', 'b' * 40), ('source_hash', 'wrong')])
def test_evidence_candidate_mismatch_never_persists(tmp_path, field, value):
    store, scope, _, chunk, source = setup_source(tmp_path)
    candidate = {**scope, 'chunk_id': chunk['chunk_id'], field: value}
    with pytest.raises(DomainError):
        CodeEvidenceAssembler(source).select('incident-a', [candidate])
    assert store.database.query('SELECT * FROM evidence') == []
