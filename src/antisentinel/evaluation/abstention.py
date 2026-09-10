"""Offline query-level abstention experiment; never changes retrieval defaults."""
import math

from .code_retrieval import score_query, summarize


def assess(rows, threshold):
    if not math.isfinite(threshold):
        raise ValueError('threshold must be finite')
    scored, rejected, answered = [], 0, 0
    for row in rows:
        score = row['max_cosine']
        if not math.isfinite(score) or not -1.000001 <= score <= 1.000001:
            raise ValueError('expected a finite cosine similarity')
        ids = row['baseline_ids'] if score >= threshold else row['exact_ids'][:5]
        scored.append(score_query(ids, row['relevant_ids']))
        if row['relevant_ids']:
            answered += 1
            rejected += not ids
    return {**summarize(scored), 'answerable_rejection_rate': rejected / answered if answered else None}


def calibrate(development_rows):
    baseline = assess(development_rows, -1.000001)
    if not baseline['answerable_count'] or not baseline['no_answer_count']:
        raise ValueError('development requires answerable and no-answer queries')
    scores = sorted({row['max_cosine'] for row in development_rows})
    thresholds = [-1.000001] + [(a+b)/2 for a, b in zip(scores, scores[1:])] + [math.nextafter(scores[-1], math.inf)]
    candidates = [{'threshold': t, 'metrics': assess(development_rows, t)} for t in thresholds]
    # Fixed before inspecting holdout: at most 5 percentage points of quality loss.
    feasible = [c for c in candidates if all(
        c['metrics']['macro'][key] >= baseline['macro'][key] - .05 - 1e-9
        for key in ('recall_at_5', 'mrr')) and c['metrics']['answerable_rejection_rate'] <= .05]
    return min(feasible, key=lambda c: (c['metrics']['false_positive_rate'], c['threshold']))


def compare(development_rows, heldout_rows):
    selected = calibrate(development_rows)
    baseline = assess(heldout_rows, -1.000001)
    candidate = assess(heldout_rows, selected['threshold'])
    checks = {
        'false_positive_reduced': candidate['false_positive_rate'] is not None and baseline['false_positive_rate'] is not None and candidate['false_positive_rate'] < baseline['false_positive_rate'],
        'recall_relative_regression_within_5_percent': baseline['macro']['recall_at_5'] is not None and candidate['macro']['recall_at_5'] >= baseline['macro']['recall_at_5'] * .95,
        'mrr_relative_regression_within_5_percent': baseline['macro']['mrr'] is not None and candidate['macro']['mrr'] >= baseline['macro']['mrr'] * .95,
        'answerable_rejection_at_most_5_percent': candidate['answerable_rejection_rate'] is not None and candidate['answerable_rejection_rate'] <= .05,
    }
    return {'selected_on_development': selected, 'heldout_baseline': baseline, 'heldout_candidate': candidate,
            'checks': checks, 'quality_tradeoff_pass': all(checks.values()), 'production_ready': False,
            'performance_measured': False, 'note': '5pp calibration constraint differs from stricter 5% relative acceptance; labels remain proposed'}
