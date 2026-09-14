import json
import os
from pathlib import Path

import httpx
import pytest

from scripts.prd005_real_case_support import (
    HttpUsageAudit,
    assert_report_safe,
    case_environment,
    freeze_case_input,
)


def test_freeze_case_input_requires_one_answerable_and_one_no_answer_query():
    root = Path(__file__).resolve().parents[1]

    frozen = freeze_case_input(
        root,
        root / 'tests/fixtures/code_retrieval/v1/manifest.json',
        'q01',
        'q21',
    )

    assert frozen.answerable_query['relevant_ids']
    assert frozen.no_answer_query['relevant_ids'] == []
    assert frozen.answerable_query['scope'] == frozen.no_answer_query['scope']
    assert len(frozen.manifest_sha256) == 64

    with pytest.raises(ValueError, match='answerable query'):
        freeze_case_input(root, root / 'tests/fixtures/code_retrieval/v1/manifest.json', 'q21', 'q22')


def test_http_usage_audit_counts_usage_without_secrets_or_bodies():
    audit = HttpUsageAudit('model')
    request = httpx.Request(
        'POST', 'https://model.example/v1/chat/completions',
        headers={'Authorization': 'Bearer secret-value'},
        json={'private_source': 'do not persist'},
    )
    response = httpx.Response(
        200,
        request=request,
        json={'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}},
    )

    audit.request(request)
    audit.response(response)

    snapshot = audit.snapshot()
    assert snapshot == {
        'kind': 'model',
        'requests': 1,
        'responses': 1,
        'input_tokens': 11,
        'output_tokens': 7,
        'total_tokens': 18,
        'usage_responses': 1,
    }
    assert 'secret-value' not in json.dumps(snapshot)
    assert 'private_source' not in json.dumps(snapshot)


def test_case_environment_restores_values_and_report_check_rejects_sensitive_material(monkeypatch):
    monkeypatch.setenv('PRD005_CASE_EXISTING', 'before')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'embedding-secret')

    with case_environment({'PRD005_CASE_EXISTING': 'during', 'PRD005_CASE_NEW': 'new'}):
        assert_report_safe({'requests': 1, 'tokens': 2})
        with pytest.raises(ValueError, match='sensitive material'):
            assert_report_safe({'body': 'embedding-secret'})

    assert os.environ['PRD005_CASE_EXISTING'] == 'before'
    assert 'PRD005_CASE_NEW' not in os.environ


@pytest.mark.parametrize('usage', [
    {'total_tokens': -1},
    {'total_tokens': 10, 'prompt_tokens': None},
    {'total_tokens': 10, 'completion_tokens': 'unknown'},
    {'total_tokens': True},
])
def test_invalid_usage_does_not_break_response_or_count_as_measured_tokens(usage):
    audit = HttpUsageAudit('embedding')
    audit.response(httpx.Response(200, json={'usage': usage}))
    assert audit.snapshot()['responses'] == 1
    assert audit.snapshot()['usage_responses'] == 0
    assert audit.snapshot()['total_tokens'] == 0


def test_report_rejects_milvus_token(monkeypatch):
    monkeypatch.setenv('ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN', 'private-milvus-token')
    with pytest.raises(ValueError, match='sensitive material'):
        assert_report_safe({'error': 'private-milvus-token'})
