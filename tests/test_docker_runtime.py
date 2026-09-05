from antisentinel.entry.application import DiagnosisApplicationService


def test_demo_probe_uses_configured_redis_service(monkeypatch):
    seen = []

    class Probe:
        def ping(self):
            return True

    def connect(url, **kwargs):
        seen.append(url)
        return Probe()

    monkeypatch.setenv("ANTISENTINEL_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setattr("antisentinel.tools.health.redis.Redis.from_url", connect)
    service = DiagnosisApplicationService.default_fake()
    registry = service.registry_factory()
    result = registry.resolve("read_health").handler({"service": "redis"})
    assert result.status == "succeeded"
    assert seen == ["redis://redis:6379/0"]
