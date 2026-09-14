"""The report must fail on a wrong independent expectation, not just serialize results."""
from copy import deepcopy
from importlib.util import find_spec
from importlib import import_module
import json
from pathlib import Path


def evaluator():
    assert find_spec('antisentinel.evaluation.intent_contract'), 'case evaluator is not implemented'
    return import_module('antisentinel.evaluation.intent_contract')


def suite():
    return json.loads((Path(__file__).parent / 'fixtures/intents/v1.json').read_text())


def test_report_detects_wrong_route_expectation():
    module = evaluator()
    cases = suite()['cases'][:1]
    cases[0]['expected']['route'] = 'plan'
    records, report = module.evaluate_cases(cases)
    assert report['contract_pass'] is False
    assert report['mismatches'] == 1
    assert report['inputs'] == report['outputs'] == 1
    assert records[0]['matched'] is False


def test_report_rejects_corrupted_fixture_hash():
    module = evaluator()
    cases = deepcopy(suite()['cases'][:1])
    cases[0]['context']['actor_id'] = 'other'
    _, report = module.evaluate_cases(cases)
    assert report['contract_pass'] is False
    assert report['fixture_errors'] == 1


def test_report_counts_all_routes_and_roundtrip_associations():
    module = evaluator()
    records, report = module.evaluate_cases(suite()['cases'])
    assert report['contract_pass'] is True
    assert report['inputs'] == report['outputs'] == 32
    assert report['associations_checked'] == 32
    assert report['mismatches'] == report['fixture_errors'] == 0
    assert set(report['routes']) == {'plan', 'direct_answer', 'task_control', 'clarify', 'unsupported'}
    assert len(records) == 32
