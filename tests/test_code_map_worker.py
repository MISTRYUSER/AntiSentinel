from datetime import datetime, timedelta, timezone


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 9, 7, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def make_store(tmp_path):
    from antisentinel.code_map.store import SQLiteCodeMapStore
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    clock = FakeClock()
    database = SQLiteDatabase(tmp_path / "worker.db")
    database.initialize()
    store = SQLiteCodeMapStore(database, clock=clock, lease_seconds=60)
    from antisentinel.code_map.models import RepositoryRegistration
    store.register(RepositoryRegistration(
        repository_id="repo-a", remote_url="file:///repo-a", credential_ref="credential-a", tracked_ref="refs/heads/main",
    ))
    job = store.enqueue("repo-a", "a" * 40, "scheduled")
    return clock, store, job


def test_claim_heartbeat_and_expired_lease_recovery(tmp_path):
    clock, store, job = make_store(tmp_path)

    first = store.claim_job("worker-1", clock.now())
    assert first is not None and first.attempt == 1
    assert store.claim_job("worker-2", clock.now()) is None
    assert store.heartbeat(first, clock.now()) is True
    clock.advance(61)
    recovered = store.claim_job("worker-2", clock.now())
    assert recovered is not None and recovered.attempt == 2
    assert recovered.token != first.token
    assert store.heartbeat(first, clock.now()) is False
    assert store.get_job(job.job_id).status == "running"


def test_stale_lease_cannot_publish_ready_snapshot(tmp_path):
    from antisentinel.code_map.models import MapSnapshot

    clock, store, job = make_store(tmp_path)
    lease = store.claim_job("worker-1", clock.now())
    assert lease is not None
    clock.advance(61)
    stale = MapSnapshot(
        snapshot_id="s" * 64, repository_id="repo-a", commit_sha="a" * 40,
        parser_revision="python-ast-v1", rules_digest=job.rules_digest,
    )
    result = store.publish(lease, stale, store.empty_staged_rows())
    assert result.ok is False and result.error.code == "lease_lost"
    assert store.get_snapshot(stale.snapshot_id) is None


def test_publish_is_fenced_and_persists_one_successful_generation(tmp_path):
    from antisentinel.code_map.models import MapSnapshot

    clock, store, job = make_store(tmp_path)
    lease = store.claim_job("worker-1", clock.now())
    assert lease is not None
    snapshot = MapSnapshot(
        snapshot_id="s" * 64, repository_id="repo-a", commit_sha="a" * 40,
        parser_revision="python-ast-v1", rules_digest=job.rules_digest,
    )
    result = store.publish(lease, snapshot, store.empty_staged_rows())
    assert result.ok is True
    assert store.get_snapshot(snapshot.snapshot_id).status == "ready"
    assert store.get_job(job.job_id).status == "succeeded"
    assert store.list_jobs("repo-a")[0].attempt == 1


def test_worker_run_once_builds_and_publishes_a_job(tmp_path):
    from antisentinel.code_map.worker import CodeMapWorker

    clock, store, job = make_store(tmp_path)
    result = CodeMapWorker(store, owner="worker-1").run_once(now=clock.now())

    assert result.status == "succeeded"
    assert result.job_id == job.job_id
    assert store.get_job(job.job_id).status == "succeeded"
