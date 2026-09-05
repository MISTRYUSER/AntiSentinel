def test_environment_wires_redis_cache_and_worker(monkeypatch, tmp_path):
    from antisentinel.adapters.cache.redis import RedisMemoryCache
    from antisentinel.entry.application import DiagnosisApplicationService

    class FakeClient:
        def zcard(self, key):
            return 0

    sentinel = RedisMemoryCache(FakeClient())
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTISENTINEL_REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setattr(RedisMemoryCache, "from_url", classmethod(lambda cls, url, namespace="": sentinel))

    service = DiagnosisApplicationService.from_environment()

    assert service.memory_recorder is not None
    assert service.memory_recorder.rollout_memory.cache is sentinel
    assert service.memory_recorder.memory_jobs.client is sentinel.client
    assert service.memory_recorder.worker._thread is not None
    service.memory_recorder.worker.stop()
