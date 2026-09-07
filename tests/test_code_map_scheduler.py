from datetime import datetime, timedelta, timezone


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def make_registration():
    from antisentinel.code_map.models import RepositoryRegistration

    return RepositoryRegistration(
        repository_id="repo-a", remote_url="ssh://git.example/repo", credential_ref="credential-a", tracked_ref="refs/heads/main",
    )


def make_scheduler(tmp_path):
    from antisentinel.code_map.scheduler import CodeMapScheduler, SyncResult
    from antisentinel.code_map.store import SQLiteCodeMapStore
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    clock = FakeClock()
    database = SQLiteDatabase(tmp_path / "code-map.db")
    database.initialize()
    store = SQLiteCodeMapStore(database, clock=clock)

    class Git:
        def __init__(self):
            self.commit_sha = "a" * 40
            self.calls = 0

        def sync_ref(self, tracked_ref, timeout_s=60.0):
            self.calls += 1
            return SyncResult(success=True, commit_sha=self.commit_sha)

    git = Git()
    return clock, store, CodeMapScheduler(store, git, owner="scheduler-1"), git


def test_interval_sequence_is_based_on_check_completion_time(tmp_path):
    clock, store, scheduler, _ = make_scheduler(tmp_path)
    registration = store.register(make_registration())

    scheduler.tick(clock.now())
    assert store.get_registration(registration.repository_id).current_interval == 1800
    assert store.get_registration(registration.repository_id).next_check_at == clock.now() + timedelta(seconds=1800)

    for expected in (3600, 7200, 14400, 14400):
        clock.advance(store.get_registration(registration.repository_id).current_interval)
        scheduler.tick(clock.now())
        assert store.get_registration(registration.repository_id).current_interval == expected


def test_sha_change_resets_interval_and_same_job_is_deduplicated(tmp_path):
    clock, store, scheduler, git = make_scheduler(tmp_path)
    registration = store.register(make_registration())
    scheduler.tick(clock.now())
    clock.advance(1800)
    scheduler.tick(clock.now())
    assert store.get_registration(registration.repository_id).current_interval == 3600

    git.commit_sha = "b" * 40
    clock.advance(3600)
    scheduler.tick(clock.now())
    assert store.get_registration(registration.repository_id).current_interval == 1800
    first = store.enqueue(registration.repository_id, git.commit_sha, "explicit")
    second = store.enqueue(registration.repository_id, git.commit_sha, "explicit")
    assert first.job_id == second.job_id


def test_two_schedulers_claim_one_check_and_pause_cancels_queued_jobs(tmp_path):
    clock, store, scheduler, _ = make_scheduler(tmp_path)
    registration = store.register(make_registration())
    first = store.claim_check(clock.now(), "scheduler-1")
    second = store.claim_check(clock.now(), "scheduler-2")
    assert first is not None and second is None

    job = store.enqueue(registration.repository_id, "c" * 40, "explicit")
    store.pause(registration.repository_id)
    assert store.get_job(job.job_id).status == "cancelled"


def test_authentication_failure_blocks_without_changing_interval(tmp_path):
    from antisentinel.code_map.scheduler import CodeMapScheduler, SyncResult

    clock, store, _, _ = make_scheduler(tmp_path)

    class AuthFailure:
        def sync_ref(self, tracked_ref, timeout_s=60.0):
            return SyncResult(success=False, error_code="auth_failed", retryable=False)

    registration = store.register(make_registration())
    scheduler = CodeMapScheduler(store, AuthFailure(), owner="scheduler-auth")
    scheduler.tick(clock.now())
    current = store.get_registration(registration.repository_id)
    assert current.blocked is True
    assert current.current_interval is None
    assert current.observed_commit is None
