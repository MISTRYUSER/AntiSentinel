from pathlib import Path
from types import SimpleNamespace

import pytest

from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.graph import GraphExpansionResult
from antisentinel.retrieval.models import CodeSearchScope, CodeSearchDocument
from antisentinel.retrieval.tools import build_code_retrieval_tools
from tests.test_code_retrieval_hybrid import FakeKeyword, FakeVector, vector_hit, identity


SCOPE = CodeSearchScope('repo', 'snapshot-a', 1, 'commit-a')


def service(graph, vector=None):
    return CodeRetrievalService(FakeKeyword(()), vector or FakeVector((vector_hit('vector-only', 1),)),
        channel_store=None, model_revision='m', template_revision='t', projection_revision='p', graph_expander=graph)


def test_vector_only_seed_expands_with_canonical_document_identity():
    seen = []
    def expand(seeds, **kwargs):
        seen.extend(seeds)
        return GraphExpansionResult((SimpleNamespace(source_candidates=(identity('child'),),
            edge_id='edge', seed_document_id=seeds[0].seed_document_id, relation='contains'),))
    result = service(SimpleNamespace(expand=expand)).search('semantic', SCOPE, mode='hybrid_graph', query_vector=[1], top_k=1)
    assert seen[0].node_id == 'vector-only'
    expected = CodeSearchDocument(*SCOPE.key, 'child', 'chunk-child', 'src/child.py', 'child', 'py', 'hash-child', 'input', 'p', 'text')
    assert result.hits[0].document_id == expected.document_id
    assert result.hits[0].channels == ('graph',)
    assert result.channel_statuses == {'keyword': 'ready', 'vector': 'ready', 'graph': 'ready'}


def test_graph_timeout_preserves_hybrid_hits_and_exposes_failure():
    def expand(*args, **kwargs):
        raise TimeoutError()
    result = service(SimpleNamespace(expand=expand)).search('q', SCOPE, mode='hybrid_graph', query_vector=[1])
    assert result.hits[0].source_identity['node_id'] == 'vector-only'
    assert result.error_code == 'graph_unavailable' and result.degraded


def test_schema_failure_is_not_hidden_by_hybrid_graph():
    with pytest.raises(ValueError, match='schema'):
        service(None, FakeVector(error=ValueError('schema'))).search('q', SCOPE, mode='hybrid_graph', query_vector=[1])


def test_production_tool_encodes_hybrid_graph_query():
    encoded = []
    graph = SimpleNamespace(expand=lambda *args, **kwargs: GraphExpansionResult(()))
    def encode(query):
        encoded.append(query)
        return [1]
    tool = build_code_retrieval_tools(service(graph), lambda _: SCOPE, query_encoder=encode)[0]
    assert tool.handler({'query': 'q', 'mode': 'hybrid_graph'}).result['mode'] == 'hybrid_graph'
    assert encoded == ['q']


def test_frozen_graph_publication_preserves_all_document_sources(tmp_path):
    from antisentinel.evaluation.code_corpus import load_corpus, publish_graph_corpus
    from antisentinel.persistence.sqlite_database import SQLiteDatabase
    root = Path(__file__).resolve().parents[1]
    corpus = load_corpus(root, root / 'tests/fixtures/code_retrieval/v1/manifest.json')
    store = publish_graph_corpus(corpus, SQLiteDatabase(tmp_path / 'facts.sqlite'))
    for doc in corpus.documents:
        chunk = store.read_generation_chunk(doc.repository_id, doc.snapshot_id, doc.published_generation, doc.chunk_id)
        assert chunk['content_hash'] == doc.source_hash and chunk['content'] == doc.text
    assert len(store.database.query('SELECT * FROM code_map_snapshots')) == 2


def test_vector_seed_graph_candidate_reads_verified_evidence(tmp_path):
    from antisentinel.retrieval.graph import GraphExpander
    from antisentinel.retrieval.vector import VectorSearchHit
    from antisentinel.retrieval.evidence import CodeEvidenceAssembler
    from tests.test_retrieval_graph_boundaries import setup_source
    store, scope, symbols, _, source = setup_source(tmp_path)
    node = next(n for n in symbols if n.qualified_name == 'Service')
    chunk = next(c for c in store.read_generation(scope['snapshot_id'], 1)['chunks'] if c['node_id'] == node.node_id)
    seed_identity = {**scope, 'node_id': node.node_id, 'chunk_id': chunk['chunk_id'], 'path': chunk['path'], 'source_hash': chunk['content_hash']}
    retrieval = CodeRetrievalService(FakeKeyword(()), FakeVector((VectorSearchHit('seed', 1, seed_identity),)),
        channel_store=None, model_revision='m', template_revision='t', projection_revision='p', graph_expander=GraphExpander(store))
    result = retrieval.search('semantic', CodeSearchScope(**scope), mode='hybrid_graph', query_vector=[1], top_k=1)
    assert result.hits[0].channels == ('graph',)
    context = CodeEvidenceAssembler(source).select('incident-a', [result.hits[0].source_identity])
    assert len(context.slices) == 1 and 'def run' in context.slices[0].content
    assert len(store.database.query('SELECT * FROM evidence')) == 1
