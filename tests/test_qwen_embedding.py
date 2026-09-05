import json

import httpx
import pytest

from antisentinel.persistence import local_vector_memory as vectors


MODEL = "qwen3.7-text-embedding-flash"


def make_embedder(handler, **kwargs):
    adapter = getattr(vectors, "QwenFlashEmbedder", None)
    assert adapter is not None, "Qwen Flash must replace the local E5/BGE adapter"
    return adapter(
        base_url="https://embedding.test/compatible-mode/v1",
        api_key="test-secret",
        dimension=256,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def response_for(request):
    texts = json.loads(request.content)["input"]
    return httpx.Response(200, json={
        "model": MODEL,
        "data": [{"index": i, "embedding": [float(i + 1)] * 256} for i in reversed(range(len(texts)))],
    })


def test_flash_batches_at_twenty_and_restores_input_order():
    requests = []

    def handler(request):
        requests.append(request)
        return response_for(request)

    embedder = make_embedder(handler)
    result = embedder.embed_documents([f"document-{i}" for i in range(21)])

    assert len(result) == 21
    assert [row[0] for row in result] == list(range(1, 21)) + [1]
    assert [len(json.loads(r.content)["input"]) for r in requests] == [20, 1]
    body = json.loads(requests[0].content)
    assert body == {"model": MODEL, "input": [f"document-{i}" for i in range(20)], "dimensions": 256, "encoding_format": "float"}
    assert requests[0].url.path == "/compatible-mode/v1/embeddings"
    assert requests[0].headers["Authorization"] == "Bearer test-secret"


def test_query_uses_flash_without_e5_prefix_and_empty_input_makes_no_request():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return response_for(request)

    embedder = make_embedder(handler)
    assert embedder.embed([]) == []
    assert embedder.embed_query(["先看日志"])[0] == [1.0] * 256
    assert requests == [{"model": MODEL, "input": ["先看日志"], "dimensions": 256, "encoding_format": "float"}]


@pytest.mark.parametrize("data", [
    [],
    [{"index": 0, "embedding": [1.0] * 255}],
    [{"index": 1, "embedding": [1.0] * 256}],
    [{"index": 0, "embedding": [True] * 256}],
    [{"index": 0, "embedding": ["1"] * 256}],
    [{"index": 0, "embedding": [0.0] * 256}],
    [{"index": 0, "embedding": [1.0] * 256}] * 2,
])
def test_invalid_vectors_are_rejected_before_indexing(data):
    embedder = make_embedder(lambda _: httpx.Response(200, json={"model": MODEL, "data": data}))
    with pytest.raises(ValueError, match="invalid_embedding_response"):
        embedder.embed(["doc"])


def test_nonfinite_vector_is_rejected():
    raw = '{"model":"' + MODEL + '","data":[{"index":0,"embedding":[' + ','.join(["NaN"] * 256) + ']}]}'
    embedder = make_embedder(lambda _: httpx.Response(200, text=raw))
    with pytest.raises(ValueError, match="invalid_embedding_response"):
        embedder.embed(["doc"])


def test_wrong_response_model_is_rejected():
    def handler(request):
        body = response_for(request).json()
        body["model"] = "old-model"
        return httpx.Response(200, json=body)

    with pytest.raises(ValueError, match="invalid_embedding_response"):
        make_embedder(handler).embed(["doc"])


def test_auth_failure_does_not_retry_or_expose_provider_body():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="test-secret private document")

    with pytest.raises(RuntimeError, match="embedding_http_401") as error:
        make_embedder(handler).embed(["doc"])
    assert len(calls) == 1
    assert "test-secret" not in str(error.value)
    assert "private document" not in str(error.value)


def test_transient_failure_has_bounded_retries(monkeypatch):
    calls = []
    monkeypatch.setattr("time.sleep", lambda _: None)

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    with pytest.raises(RuntimeError, match="embedding_http_429"):
        make_embedder(handler, max_retries=2).embed(["doc"])
    assert len(calls) == 3


def test_environment_config_selects_flash_without_loading_local_models(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-secret")
    monkeypatch.setenv("ANTISENTINEL_EMBEDDING_BASE_URL", "https://embedding.test/compatible-mode/v1")
    adapter = getattr(vectors, "QwenFlashEmbedder", None)
    assert adapter is not None
    with adapter.from_env() as embedder:
        assert embedder.model_name == MODEL
        assert embedder.dimension == 1024


def test_missing_environment_config_fails_before_network(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ANTISENTINEL_EMBEDDING_BASE_URL", raising=False)
    adapter = getattr(vectors, "QwenFlashEmbedder", None)
    assert adapter is not None
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        adapter.from_env()


def test_flash_vectors_persist_with_model_identity_and_reopen(tmp_path):
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    path = tmp_path / "memory.db"
    database = SQLiteDatabase(path)
    database.initialize()
    embedder = make_embedder(response_for)
    memory = vectors.LocalVectorMemory(database, embedder)
    memory.upsert({"memory_id": "qwen-1", "operator_id": "op-1", "content": "先看日志", "content_version": 2})

    reopened = SQLiteDatabase(path)
    record = reopened.query("SELECT embedding_model,dimension,content_version FROM memory_vectors")[0]
    assert tuple(record) == (MODEL, 256, 2)
    results = vectors.LocalVectorMemory(reopened, embedder).search("日志", operator_id="op-1")
    assert [item["memory_id"] for item in results] == ["qwen-1"]
    assert results[0]["vector_score"] == pytest.approx(1.0)


def test_timeout_retries_are_bounded_and_do_not_leak_input(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private document test-secret", request=request)

    with pytest.raises(RuntimeError, match="embedding_transport_error") as error:
        make_embedder(handler, max_retries=1).embed(["private document"])
    assert len(calls) == 2
    assert "private" not in str(error.value)


def test_second_batch_failure_does_not_return_partial_vectors():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(503)
        return response_for(request)

    with pytest.raises(RuntimeError, match="embedding_http_503"):
        make_embedder(handler, max_retries=0).embed(["doc"] * 21)
    assert len(calls) == 2
