import asyncio
import importlib.util
import socket

import pytest

from antisentinel.evaluation.ragas_evaluation import score_id_samples, samples_from_report


@pytest.mark.skipif(importlib.util.find_spec('ragas') is None, reason='install the ragas-evaluation extra')
def test_ragas_ids_use_unique_retrieved_denominator_and_no_network(monkeypatch):
    def reject_network(*_args, **_kwargs):
        raise AssertionError('offline Ragas attempted network access')

    monkeypatch.setattr(socket.socket, 'connect', reject_network)
    samples = [{'query_id': 'q1', 'mode': 'keyword', 'run': 1, 'user_input': 'find code',
                'retrieved_context_ids': ['a', 'x'], 'reference_context_ids': ['a', 'b'], 'error': None}]
    result = asyncio.run(score_id_samples(samples))
    assert result['rows'][0]['id_based_context_precision'] == 0.5
    assert result['rows'][0]['id_based_context_recall'] == 0.5
    assert result['external_model_calls'] == 0
    assert result['answer_metrics']['faithfulness']['status'] == 'not_evaluated'


@pytest.mark.skipif(importlib.util.find_spec('ragas') is None, reason='install the ragas-evaluation extra')
def test_empty_and_failed_rows_do_not_become_perfect_scores():
    base = {'query_id': 'q', 'mode': 'vector', 'run': 1, 'user_input': 'query', 'error': None}
    samples = [
        {**base, 'retrieved_context_ids': [], 'reference_context_ids': ['a']},
        {**base, 'retrieved_context_ids': ['x'], 'reference_context_ids': []},
        {**base, 'retrieved_context_ids': ['a'], 'reference_context_ids': ['a'], 'error': 'scope_mismatch'},
        {**base, 'retrieved_context_ids': ['a', 'a'], 'reference_context_ids': ['a']},
    ]
    result = asyncio.run(score_id_samples(samples))
    assert result['rows'][0]['id_based_context_precision'] is None
    assert result['rows'][0]['id_based_context_recall'] == 0
    assert result['rows'][1]['id_based_context_precision'] == 0
    assert result['rows'][1]['id_based_context_recall'] is None
    assert result['rows'][2]['status'] == 'invalid_input'
    assert result['rows'][2]['id_based_context_precision'] is None
    assert result['rows'][3]['status'] == 'invalid_input'
    assert result['execution_pass'] is False


def test_ragas_export_rejects_tampered_fingerprint_and_repeated_observation():
    from types import SimpleNamespace
    corpus = SimpleNamespace(metadata={'sha':'frozen'}, documents=(), queries=(
        {'id':'q','query':'query','scope':{},'relevant_ids':[]},))
    report = {'fingerprints':{'sha':'changed'}, 'rounds':2, 'modes':{}}
    with pytest.raises(ValueError, match='fingerprint'):
        samples_from_report(report, corpus)
    row = {'query_id':'q','run':1,'document_ids':[],'source_identities':[],'error':None}
    report = {'fingerprints':corpus.metadata, 'rounds':2, 'modes':{'keyword':{'status':'measured','rows':[row,row]}}}
    with pytest.raises(ValueError, match='duplicate'):
        samples_from_report(report, corpus)


def test_ragas_export_does_not_trust_success_when_source_scope_is_wrong():
    from types import SimpleNamespace
    corpus = SimpleNamespace(metadata={'sha':'frozen'}, documents=(SimpleNamespace(document_id='d',source_identity={'repository_id':'repo'}),),
        queries=({'id':'q','query':'query','scope':{'repository_id':'repo'},'relevant_ids':['d']},))
    row = {'query_id':'q','run':1,'document_ids':['d'],'source_identities':[{'repository_id':'foreign'}],'error':None}
    report = {'fingerprints':corpus.metadata,'rounds':1,'modes':{'keyword':{'status':'measured','rows':[row]}}}
    sample = samples_from_report(report,corpus)[0]
    assert sample['error']=='scope_mismatch'
