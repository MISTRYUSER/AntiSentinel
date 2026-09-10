"""Retrieval metrics with explicit denominators, sampling and comparability."""
from collections import Counter
import math
from statistics import mean, median
import unicodedata
import time


QUALITY_THRESHOLDS = {'precision_at_5': 0.8, 'recall_at_5': 0.6, 'mrr': 0.8}


def score_query(document_ids, relevant_ids):
    """Duplicate positions consume top-k slots and never add relevance credit."""
    top = list(document_ids[:5])
    relevant = set(relevant_ids)
    matched = len(set(top) & relevant)
    rank = next((i for i, value in enumerate(top, 1) if value in relevant), None)
    return {
        'precision_at_5': matched / 5 if relevant else None,
        'recall_at_5': matched / len(relevant) if relevant else None,
        'mrr': (1 / rank if rank else 0.0) if relevant else None,
        'ceiling_precision_at_5': min(len(relevant), 5) / 5 if relevant else None,
        'false_positive': bool(top) if not relevant else None,
        'duplicates': len(top) - len(set(top)), 'result_count': len(document_ids),
    }


def summarize(rows):
    answered = [r for r in rows if r['precision_at_5'] is not None]
    empty = [r for r in rows if r['false_positive'] is not None]
    return {
        'answerable_count': len(answered), 'no_answer_count': len(empty),
        'macro': {m: mean(r[m] for r in answered) if answered else None for m in QUALITY_THRESHOLDS},
        'min': {m: min(r[m] for r in answered) if answered else None for m in QUALITY_THRESHOLDS},
        'false_positive_rate': mean(r['false_positive'] for r in empty) if empty else None,
        'duplicate_count': sum(r['duplicates'] for r in rows),
    }


def preflight(queries):
    texts = [' '.join(unicodedata.normalize('NFKC', q['query']).casefold().split()) for q in queries]
    errors = []
    if len(set(texts)) != len(texts):
        errors.append('duplicate_query_text')
    if len({q['id'] for q in queries}) != len(queries):
        errors.append('duplicate_query_id')
    if any(not text for text in texts):
        errors.append('empty_query_text')
    answered = [q for q in queries if q['relevant_ids']]
    ceiling = mean(min(len(set(q['relevant_ids'])), 5) / 5 for q in answered) if answered else None
    return {
        'query_count': len(queries), 'unique_queries': len(set(texts)),
        'sample_sufficient': len(set(texts)) >= 20,
        'sample_minimum': 20, 'answerable_count': len(answered),
        'no_answer_count': len(queries) - len(answered),
        'precision_ceiling': ceiling,
        'precision_goal_feasible': ceiling is not None and ceiling >= QUALITY_THRESHOLDS['precision_at_5'],
        'categories': dict(Counter(q.get('category', 'unspecified') for q in queries)),
        'errors': errors,
    }


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(values) * fraction) - 1)]


def compare_performance(current_runs, baseline_runs, *, same_workload):
    comparable = same_workload and len(current_runs) == len(baseline_runs) == 5 and median(baseline_runs) > 0
    if not comparable:
        return {'comparable': False, 'pass': None, 'reason': 'requires_same_workload_and_five_runs'}
    ratio = median(current_runs) / median(baseline_runs)
    return {'comparable': True, 'current_median_ms': median(current_runs),
            'baseline_median_ms': median(baseline_runs), 'median_ratio': ratio,
            'threshold': 1.05, 'pass': ratio <= 1.05}


def evaluate(corpus, search, *, modes, rounds=5, timeout_seconds=120, baseline=None, execution_key=None):
    """Keep every attempted query, including failures, in the denominator/audit."""
    if rounds != 5 or timeout_seconds <= 0:
        raise ValueError('evaluation requires five rounds and a positive deadline')
    if not modes or not set(modes) <= {'keyword', 'vector', 'hybrid', 'hybrid_graph'}:
        raise ValueError('unsupported mode')
    if len(set(modes)) != len(modes):
        raise ValueError('duplicate mode')
    started = time.perf_counter()
    documents = {d.document_id: d for d in corpus.documents}
    report = {'schema_version': 1, 'fingerprints': corpus.metadata, 'preflight': preflight(corpus.queries),
              'rounds': rounds, 'top_k': 5, 'execution_key': execution_key,
              'modes': {m: {'status': 'not_available', 'reason': 'not_selected_or_not_implemented', 'metrics': None}
                        for m in ('keyword', 'vector', 'hybrid', 'hybrid_graph')}}
    for mode in modes:
        rows, run_times = [], []
        for run in range(rounds):
            batch_time = 0.0
            for query in corpus.queries:
                begin = time.perf_counter()
                ids, identities, contributions = [], [], []
                error = None
                scope_error = False
                try:
                    if begin - started > timeout_seconds:
                        raise TimeoutError('evaluation deadline')
                    result = search(mode, query)
                    ids = [h.document_id for h in result.hits]
                    identities = [h.source_identity for h in result.hits]
                    contributions = [{'channels': list(h.channels), 'ranks': h.ranks, 'score': h.score} for h in result.hits]
                    if result.error_code or result.degraded:
                        error = result.error_code or 'degraded'
                    if len(ids) > 5:
                        error = 'top_k_exceeded'
                    for hit in result.hits:
                        doc = documents.get(hit.document_id)
                        if doc is None or any(hit.source_identity.get(k) != v for k, v in query['scope'].items()):
                            scope_error = True
                        elif any(hit.source_identity.get(k) != v for k, v in doc.source_identity.items()):
                            scope_error = True
                    if scope_error:
                        error = 'source_identity_mismatch'
                except Exception as exc:
                    # This is the measurement boundary: preserve a failed row, never hide it as success.
                    error = type(exc).__name__
                elapsed = (time.perf_counter() - begin) * 1000
                if time.perf_counter() - started > timeout_seconds:
                    error = 'deadline_exceeded'
                batch_time += elapsed
                metrics = score_query(ids if error is None else [], query['relevant_ids'])
                # Preserve real duplicate counts even if an earlier validation rejected the response.
                metrics['duplicates'] = len(ids[:5]) - len(set(ids[:5]))
                rows.append({**metrics, 'query_id': query['id'], 'category': query['category'], 'run': run + 1,
                             'latency_ms': elapsed, 'document_ids': ids, 'source_identities': identities,
                             'contributions': contributions, 'error': error, 'scope_error': scope_error})
            run_times.append(batch_time)
        summary = summarize(rows)
        per_query = {q['id']: summarize([r for r in rows if r['query_id'] == q['id']]) for q in corpus.queries}
        errors = sum(r['error'] is not None for r in rows)
        exact = [r for r in rows if r['category'] == 'exact']
        latency = {'p50_ms': percentile([r['latency_ms'] for r in rows], .5),
                   'p95_ms': percentile([r['latency_ms'] for r in rows], .95),
                   'minimum_ms': min((r['latency_ms'] for r in rows), default=None),
                   'five_run_ms': run_times, 'median_run_ms': median(run_times)}
        old = baseline.get('modes', {}).get(mode, {}) if baseline else {}
        performance = compare_performance(run_times, old.get('latency', {}).get('five_run_ms', []),
                    same_workload=bool(baseline and execution_key and baseline.get('execution_key') == execution_key))
        if performance['comparable']:
            baseline_p95 = old['latency']['p95_ms']
            performance['p95_ratio'] = latency['p95_ms'] / baseline_p95 if baseline_p95 else None
            performance['p95_threshold'] = 1.20
            performance['pass'] = performance['pass'] and performance['p95_ratio'] is not None and performance['p95_ratio'] <= 1.20
        report['modes'][mode] = {'status': 'measured', 'queries': len(corpus.queries), 'observations': len(rows),
            'metrics': summary, 'per_query': per_query, 'errors': errors,
            'scope_errors': sum(r['scope_error'] for r in rows), 'hit_at_1_exact': mean(r['mrr'] == 1 for r in exact) if exact else None,
            'latency': latency, 'performance_comparison': performance, 'rows': rows,
            'quality_thresholds_pass': bool(not errors and summary['duplicate_count'] == 0
                and all(summary['macro'][m] is not None and summary['macro'][m] >= t for m, t in QUALITY_THRESHOLDS.items())
                and summary['false_positive_rate'] in (0, None))}
    measured = [report['modes'][m] for m in modes]
    report['execution_pass'] = bool(measured) and all(m['errors'] == 0 and m['metrics']['duplicate_count'] == 0 for m in measured)
    report['thresholds'] = QUALITY_THRESHOLDS
    report['quality_pass'] = all(report['modes'][m].get('quality_thresholds_pass', False) for m in report['modes'])
    report['acceptance_blockers'] = ['real_embedding_not_verified', 'standalone_not_verified']
    if 'hybrid_graph' not in modes:
        report['acceptance_blockers'].append('hybrid_graph_not_measured')
    if corpus.metadata.get('label_status') != 'reviewed':
        report['acceptance_blockers'].append('labels_not_reviewed')
    if not report['preflight']['precision_goal_feasible']:
        report['acceptance_blockers'].append('precision_goal_unattainable')
    if not report['preflight']['sample_sufficient']:
        report['acceptance_blockers'].append('insufficient_distinct_queries')
    if any(m['performance_comparison']['pass'] is not True for m in measured):
        report['acceptance_blockers'].append('performance_comparison_not_passed')
    report['case_pass'] = report['execution_pass'] and report['quality_pass'] and not report['acceptance_blockers']
    report['elapsed_ms'] = (time.perf_counter() - started) * 1000
    return report
