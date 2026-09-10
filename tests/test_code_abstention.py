import pytest

from antisentinel.evaluation.abstention import assess, calibrate, compare


def row(score, *, answer=True, exact=False):
    return {'max_cosine': score, 'baseline_ids': ['a'], 'exact_ids': ['a'] if exact else [], 'relevant_ids': ['a'] if answer else []}


def test_threshold_is_selected_on_development_and_exact_hits_survive():
    rows = [row(.8), row(.1, exact=True), row(.2, answer=False)]
    selected = calibrate(rows)
    assert .2 < selected['threshold'] <= .8
    result = assess(rows, selected['threshold'])
    assert result['false_positive_rate'] == 0
    assert result['macro']['recall_at_5'] == 1


def test_heldout_regression_rejects_policy_even_when_false_positives_drop():
    rows = [row(.2), row(.8), row(.1, answer=False)]
    result = assess(rows, .5)
    assert result['false_positive_rate'] == 0
    assert result['answerable_rejection_rate'] == .5
    comparison = compare([row(.8), row(.1, answer=False)], rows)
    assert comparison['quality_tradeoff_pass'] is False


@pytest.mark.parametrize('score', [float('nan'), float('inf'), 2.0])
def test_non_cosine_scores_fail_closed(score):
    with pytest.raises(ValueError, match='cosine'):
        calibrate([row(score), row(.1, answer=False)])
