from antisentinel.retrieval.engine import _replace_graph_seed_hits
from antisentinel.retrieval.fusion import FusedCodeSearchHit


def hit(name, parent=None):
    return FusedCodeSearchHit(name, .01, ('graph',) if parent else ('vector',), {},
        {'seed_document_id': parent, 'relation': 'contains'})


def test_graph_does_not_evict_unrelated_fifth_result():
    seeds = tuple(hit(str(i)) for i in range(5))
    result, truncated = _replace_graph_seed_hits(seeds, [hit('child', '0')], 5)
    assert [h.document_id for h in result] == ['child', '1', '2', '3', '4']
    assert truncated


def test_unknown_parent_cannot_claim_a_slot():
    seeds = [hit('a'), hit('b')]
    result, _ = _replace_graph_seed_hits(seeds, [hit('child', 'foreign')], 2)
    assert result == tuple(seeds)


def test_existing_seed_cannot_be_duplicated_as_child():
    seeds = [hit('a'), hit('b')]
    result, _ = _replace_graph_seed_hits(seeds, [hit('b', 'a')], 2)
    assert result == tuple(seeds)


def test_duplicate_child_falls_back_to_second_seed():
    result, _ = _replace_graph_seed_hits([hit('a'), hit('b')], [hit('child', 'a'), hit('child', 'b')], 2)
    assert [h.document_id for h in result] == ['child', 'b']


def test_multiple_children_share_only_their_parent_slot():
    result, truncated = _replace_graph_seed_hits([hit('a'), hit('b')], [hit('first', 'a'), hit('second', 'a')], 2)
    assert [h.document_id for h in result] == ['first', 'b']
    assert truncated


def test_no_expansion_preserves_base_order_and_top_k():
    result, truncated = _replace_graph_seed_hits([hit('a'), hit('b')], [], 1)
    assert [h.document_id for h in result] == ['a'] and truncated


def test_child_of_lower_ranked_seed_cannot_jump_to_first_slot():
    result, _ = _replace_graph_seed_hits([hit('a'), hit('b')], [hit('child', 'b')], 1)
    assert [h.document_id for h in result] == ['a']


def test_service_routes_only_experimental_mode_to_selected_policy():
    from types import SimpleNamespace
    from antisentinel.retrieval.engine import CodeRetrievalService
    from antisentinel.retrieval.graph import GraphExpansionResult
    from tests.test_code_retrieval_hybrid import FakeKeyword, FakeVector, vector_hit, identity
    from tests.test_hybrid_graph import SCOPE
    graph = SimpleNamespace(expand=lambda seeds, **kwargs: GraphExpansionResult((
        SimpleNamespace(source_candidates=(identity('child'),), edge_id='edge',
            seed_document_id=seeds[0].seed_document_id, relation='contains'),)))
    service = CodeRetrievalService(FakeKeyword(()), FakeVector(tuple(vector_hit(str(i), 1) for i in range(5))),
        channel_store=None, model_revision='m', template_revision='t', projection_revision='p',
        graph_expander=graph, graph_selection='replace_seed')
    result = service.search('q', SCOPE, mode='hybrid_graph', query_vector=[1])
    assert [h.source_identity['node_id'] for h in result.hits] == ['child', '1', '2', '3', '4']
    baseline = service.search('q', SCOPE, mode='hybrid', query_vector=[1])
    assert [h.source_identity['node_id'] for h in baseline.hits] == ['0', '1', '2', '3', '4']


def test_unknown_policy_is_rejected_before_use():
    import pytest
    from antisentinel.retrieval.engine import CodeRetrievalService
    with pytest.raises(ValueError, match='policy'):
        CodeRetrievalService(None, None, channel_store=None, model_revision='m',
            template_revision='t', projection_revision='p', graph_selection='unknown')


def test_container_policy_preserves_function_and_unknown_seed_kinds():
    for kind in ('function', 'method', None):
        child = hit('child', 'a')
        child.source_identity['seed_kind'] = kind
        result, _ = _replace_graph_seed_hits([hit('a')], [child], 1, containers_only=True)
        assert result[0].document_id == 'a'


def test_container_policy_can_replace_class_in_its_slot():
    child = hit('child', 'a')
    child.source_identity['seed_kind'] = 'class'
    result, _ = _replace_graph_seed_hits([hit('a'), hit('b')], [child], 2, containers_only=True)
    assert [h.document_id for h in result] == ['child', 'b']


def test_graph_seed_kind_comes_from_archived_node(tmp_path):
    from tests.test_retrieval_graph_boundaries import setup_source
    from antisentinel.retrieval.graph import GraphExpander, GraphSeed
    store, scope, symbols, _, _ = setup_source(tmp_path)
    parent = next(n for n in symbols if n.qualified_name == 'Service')
    with store.database.transaction() as connection:
        connection.execute("UPDATE code_map_nodes SET kind='function'")
    result = GraphExpander(store).expand([GraphSeed(parent.node_id, 'seed', 1)], scope=scope)
    assert result.items[0].seed_kind == 'class'
