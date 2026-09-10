import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time

import httpx
import pytest

from antisentinel.runtime.deadline import query_budget, current_deadline, QueryDeadlineExceeded
from antisentinel.retrieval.coordinator import RetrievalCoordinator
from antisentinel.retrieval.tools import build_code_retrieval_tools
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
from tests.test_code_retrieval_hybrid import FakeKeyword, keyword_hit
from tests.test_hybrid_graph import SCOPE
from antisentinel.retrieval.engine import CodeRetrievalService


def coordinator(handler, timeout=.04):
    instance = RetrievalCoordinator(None, None, allowed_repositories=['repo'],
        index_factory=None, embedder_factory=None, query_timeout=timeout)
    instance.state = 'running'
    definition = ToolDefinition('code_retrieval.search', 'test', {'type':'object'}, handler)
    instance.bindings = SimpleNamespace(for_incident=lambda _: (definition,))
    return instance


def test_lock_wait_is_in_budget_and_does_not_run_handler():
    calls = []
    instance = coordinator(lambda _: calls.append(1))
    tool = instance.for_incident('incident')[0]
    pool = ThreadPoolExecutor(1)
    instance.lock.acquire()
    try:
        started = time.monotonic()
        result = pool.submit(tool.handler, {}).result(timeout=1)
        assert time.monotonic() - started < .5
        assert result.error['code'] == 'query_deadline_exceeded'
        assert calls == []
    finally:
        instance.lock.release()
        pool.shutdown()


def test_late_synchronous_result_is_never_accepted():
    def late(_):
        time.sleep(.04)
        return ToolExecutionResult(status='succeeded', result={'hits':['late']})
    result = coordinator(late, .01).for_incident('i')[0].handler({})
    assert result.status == 'failed' and result.result is None
    assert current_deadline() is None


def test_nested_budgets_never_extend_deadline_and_restore_context():
    with query_budget(1) as outer:
        with query_budget(10) as inner:
            assert inner.expires_at == outer.expires_at
        assert current_deadline() is outer
    assert current_deadline() is None


def test_rpc_timeout_shrinks_without_mutating_worker_default(monkeypatch):
    import antisentinel.runtime.deadline as module
    now = [100.]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    index = MilvusAdapter('unused', 'test', dimension=3, model_revision='m',
        template_revision='t', projection_revision='p', rpc_timeout=10, client=object())
    with query_budget(4):
        assert index.rpc_options == {'timeout':4}
        now[0] += 1
        assert index.rpc_options == {'timeout':3}
        now[0] += 4
        with pytest.raises(QueryDeadlineExceeded):
            _ = index.rpc_options
    assert index.rpc_options == {'timeout':10}


def test_sqlite_long_query_is_interrupted_and_connection_is_reusable(tmp_path):
    db = SQLiteDatabase(tmp_path/'facts.sqlite')
    db.initialize()
    with query_budget(.01), pytest.raises(QueryDeadlineExceeded):
        db.query('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT SUM(x) FROM n')
    assert db.query('SELECT 1')[0][0] == 1


def encoder(transport):
    clients = []
    def factory(**kwargs):
        client = httpx.AsyncClient(transport=transport, **kwargs)
        clients.append(client)
        return client
    return QwenFlashEmbedder(base_url='https://fixture.invalid', api_key='fixture',
        dimension=256, async_client_factory=factory), clients


def test_embedding_timeout_cancels_request_and_hybrid_falls_back():
    completed = []
    async def delayed(request):
        try:
            await asyncio.sleep(10)
        finally:
            completed.append('cancelled')
    embedder, clients = encoder(httpx.MockTransport(delayed))
    service = CodeRetrievalService(FakeKeyword((keyword_hit('a',1),)), None,
        channel_store=None, model_revision='m', template_revision='t', projection_revision='p')
    definition = build_code_retrieval_tools(service, lambda _: SCOPE,
        query_encoder=lambda q: embedder.embed_query([q])[0])[0]
    try:
        with query_budget(.3):
            result = definition.handler({'query':'a'})
        assert result.result['degraded'] and result.result['hits']
        assert result.result['channel_statuses']['vector'] == 'unavailable'
        assert completed == ['cancelled'] and all(c.is_closed for c in clients)
        assert not embedder.client.is_closed  # The worker's client is untouched.
    finally:
        embedder.close()


@pytest.mark.parametrize('status', [401, 403])
def test_auth_errors_are_not_hidden_as_keyword_fallback(status):
    async def denied(request):
        return httpx.Response(status)
    embedder, _ = encoder(httpx.MockTransport(denied))
    definition = build_code_retrieval_tools(None, lambda _: SCOPE,
        query_encoder=lambda q: embedder.embed_query([q])[0])[0]
    try:
        with query_budget(1), pytest.raises(RuntimeError, match=f'embedding_http_{status}'):
            definition.handler({'query':'a'})
    finally:
        embedder.close()


def test_custom_sync_transport_is_not_silently_replaced_with_network():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
        embedder = QwenFlashEmbedder(base_url='https://fixture.invalid', api_key='fixture', client=client)
        with query_budget(1), pytest.raises(ValueError, match='custom client'):
            embedder.embed_query(['a'])


def test_local_tls_deadline_case(tmp_path):
    from scripts.run_query_deadline_case import run
    result = run(tmp_path/'deadline-case')
    assert result['case_pass']
    assert result['local_https_requests']==2 and result['external_model_calls']==0
    assert result['normal_output_count']==2 and result['persisted_tasks']==2
