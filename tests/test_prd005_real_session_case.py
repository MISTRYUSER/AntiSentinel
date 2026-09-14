from pathlib import Path
import httpx
import pytest

from scripts.run_prd005_real_session_case import local_case_factories, real_case_factories, run
from antisentinel.runtime.deadline import query_budget
from types import SimpleNamespace
import time
import json
import subprocess
import sys


def test_r1_uses_normal_application_entry_and_reopens_without_document_embedding(tmp_path):
    root = Path(__file__).resolve().parents[1]
    factories = local_case_factories()

    report = run(
        root,
        root / 'tests/fixtures/code_retrieval/v1/manifest.json',
        tmp_path / 'r1',
        str(tmp_path / 'vectors.db'),
        'q01',
        'q21',
        120,
        factories=factories,
    )

    assert report['case_pass']
    assert report['counts']['sessions'] == 2
    assert report['counts']['persisted_results'] == 2
    assert report['counts']['rehydrated_evidence'] >= 1
    assert report['embedding']['document_inputs_per_start'][1] == 0
    assert report['checks']['normal_http_entry']
    assert report['checks']['projection_revision_matches']
    assert report['unexpected_background_exceptions'] == 0


def test_real_embedding_audit_covers_async_queries_and_counts_documents(monkeypatch):
    from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
    monkeypatch.setenv('ANTISENTINEL_EMBEDDING_BASE_URL', 'https://embedding.example/v1')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-secret')
    monkeypatch.setenv('ANTISENTINEL_EMBEDDING_DIMENSION', '256')
    def response(request):
        return httpx.Response(200, json={
            'model': QwenFlashEmbedder.model_name,
            'data': [{'index': 0, 'embedding': [1.] + [0.] * 255}],
            'usage': {'total_tokens': 3},
        })
    original_client, original_async = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(response), **kwargs))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_async(transport=httpx.MockTransport(response), **kwargs))
    factories = real_case_factories()
    encoder = factories.embedder_factory()
    try:
        assert len(encoder.embed_documents(['document'])[0]) == 256
        with query_budget(1):
            assert len(encoder.embed_query(['question'])[0]) == 256
        assert factories.document_inputs_per_start() == [1]
        assert factories.embedding_audit.snapshot()['requests'] == 2
        assert factories.embedding_audit.snapshot()['usage_responses'] == 2
        assert factories.embedding_audit.snapshot()['total_tokens'] == 6
    finally:
        encoder.close()
        factories.close()


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), True, 0])
def test_invalid_budget_fails_before_creating_output(tmp_path, timeout):
    with pytest.raises(ValueError, match='timeout'):
        run(tmp_path, tmp_path / 'absent.json', tmp_path / 'out', 'none', 'q01', 'q21', timeout,
            factories=local_case_factories())
    assert not (tmp_path / 'out').exists()


def test_wait_ready_stops_on_blocked_run():
    from scripts.run_prd005_real_session_case import _wait_ready
    client = SimpleNamespace(get=lambda _: SimpleNamespace(json=lambda: {
        'state': 'running', 'projection_errors': 0, 'runs': {'blocked': 1},
    }))
    with pytest.raises(RuntimeError, match='blocked'):
        _wait_ready(client, 1, time.monotonic() + .05)


def test_failed_background_session_stops_polling_before_budget():
    from scripts.run_prd005_real_session_case import _run_session
    class Client:
        def post(self, path, **kwargs):
            if path == '/api/incidents':
                return httpx.Response(201, json={'incident_id': 'incident'})
            return httpx.Response(202, json={'session_id': 'session'})
        def get(self, path):
            return httpx.Response(200, json={'status': 'running', 'session_status': 'failed'})
    service = SimpleNamespace(code_map_store=SimpleNamespace(bind_incident=lambda *args: None))
    with pytest.raises(RuntimeError, match='background'):
        _run_session(Client(), service, {'query': 'query', 'scope': {'repository_id': 'repo', 'snapshot_id': 'snap'}}, time.monotonic() + .05)


def test_cli_preflight_from_other_directory_makes_no_output(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        sys.executable, str(root / 'scripts/run_prd005_real_session_case.py'),
        '--preflight', '--repository', str(root),
        '--manifest', str(root / 'tests/fixtures/code_retrieval/v1/manifest.json'),
        '--output', str(tmp_path / 'out'), '--milvus-uri', 'http://127.0.0.1:29530',
        '--answerable-query-id', 'q01', '--no-answer-query-id', 'q21',
    ], cwd=tmp_path, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['external_calls'] == 0
    assert report['input_documents'] == 82
    assert report['minimum_scheduler_wait_seconds'] == 200
    assert not (tmp_path / 'out').exists()


def test_false_citation_prevents_case_pass(tmp_path):
    from scripts.run_prd005_real_session_case import AnswerableModel, NoAnswerModel
    class BadCitationModel(AnswerableModel):
        def complete(self, request):
            response = super().complete(request)
            if 'final' in response:
                response['final']['evidence_refs'] = [{'evidence_id': 'made-up-evidence'}]
            return response
    root = Path(__file__).resolve().parents[1]
    factories = local_case_factories()
    models = []
    factories.configure_queries = lambda a, n: models.extend([BadCitationModel(a), NoAnswerModel(n)])
    factories.model_factory = lambda: models.pop(0)
    report = run(root, root / 'tests/fixtures/code_retrieval/v1/manifest.json',
                 tmp_path / 'bad', str(tmp_path / 'bad-vectors.db'), 'q01', 'q21', factories=factories)
    assert not report['checks']['citations_disclosed']
    assert not report['case_pass']


@pytest.mark.parametrize('mode,degraded,scope,accepted', [
    ('hybrid', False, 'repo', True), ('keyword', False, 'repo', False),
    ('hybrid', True, 'repo', False), ('hybrid', False, 'other', False),
])
def test_hybrid_acceptance_rejects_downgrade_and_wrong_scope(mode, degraded, scope, accepted):
    from scripts.run_prd005_real_session_case import verified_hybrid_search
    result = {'tool_calls': [{'tool_call_id': 'call', 'tool_name': 'code_retrieval.search',
                              'arguments': {'mode': mode}}],
              'attempts': [{'tool_call_id': 'call', 'status': 'succeeded', 'result_summary': json.dumps({
                  'degraded': degraded, 'incomplete': False, 'error_code': None,
                  'channel_statuses': {'keyword': 'ready', 'vector': 'ready'},
                  'hits': [{'source_identity': {'repository_id': scope}}],
              })}]}
    assert verified_hybrid_search(result, {'repository_id': 'repo'}) is accepted


@pytest.mark.parametrize('changed', ['final', 'evidence_refs', 'tool_calls', 'attempts', None])
def test_reopened_result_must_preserve_answer_citations_and_execution(changed):
    from scripts.run_prd005_real_session_case import restored_result_matches
    original = {'status': 'completed', 'final': {'summary': 'answer'},
                'evidence_refs': [{'evidence_id': 'evidence'}],
                'tool_calls': [{'tool_name': 'code_retrieval.search'}],
                'attempts': [{'status': 'succeeded'}]}
    reopened = {**original, 'messages': []}
    if changed:
        reopened.pop(changed)
    assert restored_result_matches(original, reopened) is (changed is None)


def test_audit_accounts_for_transport_failure_without_a_response(monkeypatch):
    from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
    monkeypatch.setenv('ANTISENTINEL_EMBEDDING_BASE_URL', 'https://embedding.example/v1')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-secret')
    monkeypatch.setenv('ANTISENTINEL_EMBEDDING_DIMENSION', '256')
    calls = []
    def response(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError('injected transport failure')
        return httpx.Response(200, json={'model': QwenFlashEmbedder.model_name,
            'data': [{'index': 0, 'embedding': [1.] + [0.] * 255}], 'usage': {'total_tokens': 3}})
    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(response), **kwargs))
    factories = real_case_factories()
    encoder = factories.embedder_factory()
    try:
        with pytest.raises(RuntimeError, match='embedding_transport_error'):
            encoder.embed_documents(['document'])
        encoder.embed_documents(['document'])
        assert factories.embedding_audit.snapshot()['requests'] == 2
        assert factories.embedding_audit.snapshot()['responses'] == 1
        assert factories.unanswered_embedding_failures() == 1
    finally:
        encoder.close()
        factories.close()
