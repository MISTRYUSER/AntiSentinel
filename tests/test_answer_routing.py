import pytest

from antisentinel.evaluation.rag_answers import select_answer_style, verify_index_corpus


def packet(query):
    return {'query':'%s'%query,'query_id':'arbitrary','category':'must_not_be_used','candidates':[{
        'source_identity':{'path':'src/store.go'},
        'text':'func (s *Store) Reload(ctx context.Context) error {\n log.Warn("reload failed, retaining state")\n return nil\n}\n',
    }]}


@pytest.mark.parametrize('query,expected', [
    ('Reload','focused'), ('src/store.go','focused'), ('reload failed','focused'),
    ('为什么 Reload 会失败？','baseline'), ('How does Reload work?','baseline'),
    ('MissingFunction','baseline'), ('return nil','baseline'), ('','baseline'),
])
def test_routes_only_source_supported_lookups(query, expected):
    assert select_answer_style(packet(query)) == expected


def test_route_does_not_use_labels_or_id():
    p=packet('Reload');before=select_answer_style(p)
    p.update(query_id='no_answer',category='semantic',relevant_ids=[])
    assert select_answer_style(p)==before


def test_same_index_can_evaluate_new_queries_but_not_changed_sources():
    old={'corpus_version':'v1','corpus_sha256':'source','commits':{'current':'commit'},'source_count':4,'queries_sha256':'old'}
    new={**old,'queries_sha256':'new'}
    verify_index_corpus({'fingerprints':old,'execution_pass':True},new)
    with pytest.raises(ValueError):
        verify_index_corpus({'fingerprints':old,'execution_pass':True},{**new,'corpus_sha256':'changed'})
