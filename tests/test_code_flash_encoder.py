import httpx
import pytest

from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
from antisentinel.evaluation.code_embedding import FlashCodeEncoder


def test_flash_encoder_tracks_real_http_attempts_and_usage_without_caching():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            'model': 'qwen3.7-text-embedding-flash',
            'data': [{'index': 0, 'embedding': [1.0] * 256}],
            'usage': {'total_tokens': 7},
        })
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        base = QwenFlashEmbedder(base_url='https://example.test/v1', api_key='test', dimension=256, client=client)
        encoder = FlashCodeEncoder(base)
        assert len(encoder.encode('query')) == 256
        encoder.encode('query')
        assert encoder.revision == base.model_name
        assert encoder.metadata()['external_calls'] == 2
        assert encoder.metadata()['tokens'] == 14
        assert encoder.metadata()['cost'] is None
        encoder.close()
        assert not client.event_hooks['request']


def test_flash_encoder_auth_failure_is_not_reported_as_zero_calls():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
        encoder = FlashCodeEncoder(QwenFlashEmbedder(base_url='https://example.test/v1', api_key='test', client=client))
        with pytest.raises(RuntimeError, match='embedding_http_401'):
            encoder.encode('query')
        assert encoder.metadata()['external_calls'] == 1
        assert encoder.metadata()['tokens'] is None
        encoder.close()


def test_benchmark_flash_path_persists_model_and_reopens_vectors(tmp_path, monkeypatch):
    import json
    import runpy
    from pathlib import Path
    pytest.importorskip('milvus_lite')
    root = Path(__file__).resolve().parents[1]
    def handler(request):
        texts = json.loads(request.content)['input']
        return httpx.Response(200, json={
            'model': 'qwen3.7-text-embedding-flash',
            'data': [{'index': i, 'embedding': [1.0] * 256} for i in range(len(texts))],
            'usage': {'total_tokens': len(texts)},
        })
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        encoder = FlashCodeEncoder(QwenFlashEmbedder(base_url='https://example.test/v1', api_key='test', dimension=256, client=client))
        monkeypatch.setattr(FlashCodeEncoder, 'from_env', classmethod(lambda cls: encoder))
        runner = runpy.run_path(str(root/'scripts/evaluate_code_retrieval.py'))
        report = runner['run_benchmark'](root, root/'tests/fixtures/code_retrieval/v1/manifest.json',
            tmp_path/'case', diagnostic=True, qwen_flash=True)
        assert report['execution_pass']
        assert report['documents_verified'] and report['vectors_verified']
        assert report['persisted_vectors'] == report['input_documents']
        assert report['embedding']['revision'] == 'qwen3.7-text-embedding-flash'
        assert report['embedding']['dimension'] == 256
        assert report['embedding']['external_calls'] == (report['input_documents'] + 19)//20 + 240
        assert report['embedding']['tokens'] == report['input_documents'] + 240
        assert report['case_pass'] is False  # Mock vectors never establish acceptance.
