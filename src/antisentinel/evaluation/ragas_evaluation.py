"""Optional, local Ragas ID evaluation over validated retrieval observations."""
import importlib.metadata
import math
import os
from statistics import mean
import warnings


RAGAS_VERSION = '0.4.3'


async def score_id_samples(samples):
    # Dedicated evaluation path: no telemetry or model provider is initialized.
    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    os.environ['LANGSMITH_TRACING'] = 'false'
    if importlib.metadata.version('ragas') != RAGAS_VERSION:
        raise RuntimeError(f'expected ragas=={RAGAS_VERSION}')
    from ragas import SingleTurnSample
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=DeprecationWarning, message='Importing IDBasedContext.*')
        from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall
    precision, recall = IDBasedContextPrecision(), IDBasedContextRecall()
    rows = []
    for sample in samples:
        retrieved, reference = sample['retrieved_context_ids'], sample['reference_context_ids']
        invalid = sample.get('error') or ('duplicate_retrieved_ids' if len(retrieved) != len(set(retrieved)) else None)
        row = {**sample, 'status': 'invalid_input' if invalid else 'scored', 'error': invalid,
               'id_based_context_precision': None, 'id_based_context_recall': None}
        if not invalid:
            data = SingleTurnSample(user_input=sample['user_input'], retrieved_context_ids=retrieved,
                                    reference_context_ids=reference)
            for name, scorer in [('id_based_context_precision', precision), ('id_based_context_recall', recall)]:
                value = await scorer.single_turn_ascore(data)
                row[name] = float(value) if math.isfinite(value) else None
            row['undefined_reason'] = {'precision': 'no_retrieved_ids' if not retrieved else None,
                                       'recall': 'no_reference_ids' if not reference else None}
        rows.append(row)
    modes = {}
    for mode in sorted({r['mode'] for r in rows}):
        selected = [r for r in rows if r['mode'] == mode]
        modes[mode] = {'observations': len(selected), 'invalid_inputs': sum(r['status'] != 'scored' for r in selected)}
        for metric in ['id_based_context_precision', 'id_based_context_recall']:
            values = [r[metric] for r in selected if r[metric] is not None]
            modes[mode][metric] = {'mean_defined': mean(values) if values else None, 'defined_count': len(values),
                                   'undefined_or_invalid_count': len(selected) - len(values)}
    return {'ragas_version': RAGAS_VERSION, 'backend': 'ragas.metrics.IDBasedContextPrecision/IDBasedContextRecall',
            'rows': rows, 'modes': modes, 'external_model_calls': 0, 'telemetry_enabled': False,
            'execution_pass': bool(rows) and all(r['status'] == 'scored' for r in rows),
            'quality_pass': False, 'case_pass': False,
            'definitions': {'id_based_context_precision': 'relevant unique IDs / retrieved unique IDs; not Precision@5',
                            'id_based_context_recall': 'retrieved relevant unique IDs / reference unique IDs'},
            'answer_metrics': {m: {'status': 'not_evaluated', 'reason': 'requires real responses, disclosed contexts and an authorized judge model'}
                               for m in ['faithfulness', 'answer_relevancy', 'llm_context_precision', 'llm_context_recall']}}


def samples_from_report(report, corpus):
    """Recheck frozen labels and source identities; never trust precomputed grades."""
    if report['fingerprints'] != corpus.metadata:
        raise ValueError('evaluation fingerprint mismatch')
    queries = {q['id']: q for q in corpus.queries}
    documents = {d.document_id: d for d in corpus.documents}
    samples = []
    for mode, result in report['modes'].items():
        if result['status'] != 'measured':
            continue
        if len(result['rows']) != len(queries) * report['rounds']:
            raise ValueError('missing observations')
        seen = set()
        for row in result['rows']:
            key = row['query_id'], row['run']
            if key in seen or key[0] not in queries or not 1 <= key[1] <= report['rounds']:
                raise ValueError('duplicate or unknown observation')
            seen.add(key)
            query = queries[key[0]]
            ids, identities = row['document_ids'], row['source_identities']
            error = row.get('error')
            if len(ids) != len(identities) or len(ids) > 5:
                error = 'invalid_result_shape'
            for doc_id, identity in zip(ids, identities):
                doc = documents.get(doc_id)
                if doc is None or any(identity.get(k) != v for k, v in query['scope'].items()):
                    error = 'scope_mismatch'
                elif any(identity.get(k) != v for k, v in doc.source_identity.items()):
                    error = 'source_identity_mismatch'
            samples.append({'query_id': key[0], 'run': key[1], 'mode': mode, 'user_input': query['query'],
                            'retrieved_context_ids': ids, 'reference_context_ids': query['relevant_ids'], 'error': error})
    return samples
