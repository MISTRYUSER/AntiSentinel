from datetime import datetime, timezone

from antisentinel.domain.event import Event
from antisentinel.entry.application import DiagnosisApplicationService
from antisentinel.persistence.application_store import FileApplicationStore
from antisentinel.persistence.audited_stores import AuditedEventStore
from antisentinel.persistence.sqlite_stores import SQLiteApplicationStore


def test_environment_uses_sqlite_and_recovers_incident_after_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTISENTINEL_PERSISTENCE_MODE", "sqlite")
    monkeypatch.delenv("ANTISENTINEL_REDIS_URL", raising=False)

    first = DiagnosisApplicationService.from_environment()
    incident = first.create_incident(title="SQLite flow", summary=None, source="operator")
    second = DiagnosisApplicationService.from_environment()

    assert isinstance(first.application_store, SQLiteApplicationStore)
    assert (tmp_path / "antisentinel.db").exists()
    assert str(incident.incident_id) in second.incidents


def test_environment_can_explicitly_use_legacy_file_adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTISENTINEL_PERSISTENCE_MODE", "legacy")
    monkeypatch.delenv("ANTISENTINEL_REDIS_URL", raising=False)

    service = DiagnosisApplicationService.from_environment()

    assert isinstance(service.application_store, FileApplicationStore)
    assert not (tmp_path / "antisentinel.db").exists()


def test_audited_event_store_keeps_primary_commit_and_reports_audit_degraded():
    class Primary:
        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)

        def list_by_aggregate(self, aggregate_type, aggregate_id):
            return list(self.events)

        def list_by_correlation(self, correlation_id):
            return list(self.events)

    class BrokenAudit:
        def append(self, event):
            raise OSError("disk full")

    degraded = []
    primary = Primary()
    store = AuditedEventStore(primary, BrokenAudit(), degraded.append)
    event = Event.create(
        type="incident.created", aggregate_type="Incident", aggregate_id="incident-1",
        correlation_id="incident-1", occurred_at=datetime.now(timezone.utc), payload={},
    )

    store.append(event)

    assert primary.events == [event]
    assert degraded == ["event audit failed for " + str(event.event_id) + ": disk full"]
    assert store.list_by_correlation("incident-1") == [event]


def test_environment_uses_redis_memory_job_queue_when_redis_is_configured(monkeypatch, tmp_path):
    from antisentinel.adapters.cache.redis import RedisMemoryCache
    from antisentinel.memory.jobs import RedisMemoryJobQueue
    from antisentinel.memory.worker import MemoryWorker

    class FakeRedis:
        pass

    fake_cache = RedisMemoryCache(FakeRedis())
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTISENTINEL_REDIS_URL", "redis://local/0")
    monkeypatch.setenv("ANTISENTINEL_REDIS_PREFIX", "case:memory")
    monkeypatch.setattr(RedisMemoryCache, "from_url", classmethod(lambda cls, url, namespace="": fake_cache))
    monkeypatch.setattr(MemoryWorker, "start", lambda self: None)

    service = DiagnosisApplicationService.from_environment()

    assert isinstance(service.memory_recorder.memory_jobs, RedisMemoryJobQueue)
    assert service.memory_recorder.memory_jobs.prefix == "case:memory"
