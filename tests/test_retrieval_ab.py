import pytest
from scripts.run_retrieval_ab import paired_schedule, paired_statistics


def test_order_is_balanced_and_alternates_per_query():
    ids = [str(i) for i in range(24)]
    schedule = paired_schedule(ids)
    assert schedule == paired_schedule(ids)
    assert len(schedule) == 120
    assert sum(p['order'][0] == 'A' for p in schedule) == 60
    for query in ids:
        orders = [p['order'][0] for p in schedule if p['query_id'] == query]
        assert all(a != b for a, b in zip(orders, orders[1:]))


def rows(rounds):
    return [{'query_id':str(q), 'run':run, 'arm':arm,
             'recall_at_5':float(arm=='B' and q==0),
             'precision_at_5':float(arm=='B' and q==0)/5,
             'mrr':float(arm=='B' and q==0), 'false_positive':None}
            for q in range(20) for run in range(1,rounds+1) for arm in ('A','B')]


def test_repeated_rounds_do_not_increase_statistical_sample_size():
    once = paired_statistics(rows(1), replicates=1000)
    repeated = paired_statistics(rows(5), replicates=1000)
    assert once == repeated
    assert repeated['recall_at_5']['independent_queries'] == 20
    assert repeated['recall_at_5']['ci95'][0] == 0


def test_missing_or_duplicate_arm_is_rejected():
    with pytest.raises(ValueError, match='missing'):
        paired_statistics(rows(1)[:-1])
    with pytest.raises(ValueError, match='duplicate'):
        paired_statistics(rows(1) + rows(1)[:1])


def test_mismatched_denominators_are_rejected():
    data = rows(1)
    data[0]['recall_at_5'] = None
    with pytest.raises(ValueError, match='denominator'):
        paired_statistics(data)


def test_two_snapshot_ab_reconciles_both_scopes(tmp_path):
    from scripts.run_retrieval_ab import run
    result = run(tmp_path/'ab')
    assert result['execution_pass']
    assert result['measured_calls'] == 240 and result['warmup_calls'] == 48
    assert result['checks']['index_reconciled']
    assert result['persisted_documents'] == result['persisted_vectors'] == 82
    assert result['promote_default'] is False
