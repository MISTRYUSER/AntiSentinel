import json

import pytest

from antisentinel.domain.errors import DomainError
from antisentinel.retrieval.graph import GraphExpander, GraphExpansion, GraphExpansionResult, GraphSeed
from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.models import CodeSearchHit, CodeSearchScope
from antisentinel.retrieval.tools import build_code_retrieval_tools
from antisentinel.tools.manifest import ToolExecutionResult
from tests.test_retrieval_graph_boundaries import setup_source


@pytest.mark.parametrize('relation', ['calls', 'imports', 'inherits', 'tested_by'])
def test_disabled_relation_is_rejected_before_reading_generation(relation):
    class UnreadableStore:
        def read_generation(self, *_):
            raise AssertionError('disabled relation reached storage')

    with pytest.raises(ValueError, match='relation'):
        GraphExpander(UnreadableStore()).expand([], scope={'repository_id':'repo', 'snapshot_id':'sid', 'published_generation':1, 'commit_sha':'sha'}, relations=(relation,))


@pytest.mark.parametrize('top_k', [1, 2, 5])
def test_full_keyword_candidates_cannot_hide_graph_expansion(top_k):
    scope = CodeSearchScope('repo', 'snapshot', 1, 'commit')

    class Keywords:
        def is_ready(self, _, **kwargs):
            return True

        def search(self, *_args, **_kwargs):
            return tuple(CodeSearchHit(f'doc-{i}', -1, 'keyword', {**vars(scope), 'node_id': f'seed-{i}'}) for i in range(5))

    class Neighbors:
        def expand(self, *_args, **_kwargs):
            return GraphExpansionResult((GraphExpansion('neighbor', 'doc-0', 'contains', 'outbound',
                       'a.py', 'Child', 'seed=doc-0', 'resolved', 'ast_parent',source_candidates=({**vars(scope),'node_id':'neighbor','chunk_id':'neighbor-chunk','path':'a.py','source_hash':'hash'},)),))

    service = CodeRetrievalService(Keywords(), None, channel_store=None,
                model_revision='none', template_revision='none', projection_revision='v1', graph_expander=Neighbors())
    result = service.search('root', scope, mode='graph', top_k=top_k)
    assert len(result.hits) == top_k
    assert any('graph' in h.channels for h in result.hits)
    assert len({h.source_identity['node_id'] for h in result.hits}) == len(result.hits)
    assert result.truncated is True
    assert result.incomplete is True
    if top_k > 1:
        assert result.hits[0].document_id == 'doc-0'


@pytest.mark.parametrize('field,value', [('published_generation', 2), ('requested_commit', 'b' * 40)])
def test_rehydrate_rejects_evidence_after_incident_binding_changes(tmp_path, field, value):
    store, scope, _, chunk, source = setup_source(tmp_path)
    original = source.read_source('incident-a', 'repo-a', scope['snapshot_id'], chunk['chunk_id'])
    with store.database.transaction() as conn:
        conn.execute(f'UPDATE code_map_diagnosis_bindings SET {field}=? WHERE incident_id=?', (value, 'incident-a'))
    with pytest.raises(DomainError, match='scope_mismatch'):
        source.rehydrate([{'evidence_id': original.evidence_id}])
    assert len(store.database.query('SELECT * FROM evidence')) == 1


def test_budget_truncation_marks_incomplete_and_tool_returns_typed_result(tmp_path):
    store, scope, symbols, _, _ = setup_source(tmp_path)
    payload = store.read_generation(scope['snapshot_id'], 1)
    extra = dict(payload['nodes'][1], node_id='extra', qualified_name='Service.other')
    payload['nodes'].append(extra)
    payload['edges'].append(dict(payload['edges'][0], edge_id='extra-edge', target_node_id='extra'))
    with store.database.transaction() as conn:
        conn.execute('UPDATE code_map_generations SET payload_json=?', (json.dumps(payload),))
    graph = GraphExpander(store)
    result = graph.expand([GraphSeed(symbols[0].node_id, 'root', 1)], scope=scope, node_budget=1)
    assert len(result.items) == 1
    assert result.truncated and result.incomplete
    definitions = build_code_retrieval_tools(None, lambda _: scope, graph_expander=graph)
    tool = next(t for t in definitions if t.name == 'code_retrieval.expand_graph')
    response = tool.handler({'seeds': [{'node_id': symbols[0].node_id, 'seed_document_id':'root', 'rank':1}], 'node_budget':1})
    assert isinstance(response, ToolExecutionResult)
    assert response.status == 'succeeded'
    assert response.result['truncated'] and response.result['incomplete']


def test_legacy_evidence_without_generation_is_not_restored_by_guessing(tmp_path):
    from antisentinel.domain.evidence import Evidence
    store, scope, _, chunk, source = setup_source(tmp_path)
    evidence = Evidence.create(kind='source_code', content_ref='legacy://source', content_hash=chunk['content_hash'],
        metadata={'incident_id':'incident-a', 'repository_id':'repo-a', 'snapshot_id':scope['snapshot_id'],
                  'chunk_id':chunk['chunk_id'], 'commit_sha':scope['commit_sha']})
    source.evidence_store.put_once(evidence, incident_id='incident-a')
    with pytest.raises(DomainError, match='source_identity_missing'):
        source.rehydrate([{'evidence_id': str(evidence.evidence_id)}])
    assert len(store.database.query('SELECT * FROM evidence')) == 1
