from datetime import datetime, timezone

from antisentinel.persistence.sqlite_database import SQLiteDatabase


def make_registration():
    from antisentinel.code_map.models import RepositoryRegistration

    return RepositoryRegistration(
        repository_id="repo-a", remote_url="ssh://git.example/repo", credential_ref="credential-a", tracked_ref="refs/heads/main",
    )


def test_store_round_trips_registration_and_job(tmp_path):
    from antisentinel.code_map.store import SQLiteCodeMapStore
    from antisentinel.persistence.sqlite_database import SQLiteDatabase

    database = SQLiteDatabase(tmp_path / "code-map.db")
    database.initialize()
    store = SQLiteCodeMapStore(database, clock=lambda: datetime(2026, 9, 7, tzinfo=timezone.utc))
    registration = store.register(make_registration())
    job = store.enqueue(registration.repository_id, "a" * 40, "scheduled", trace={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"})

    assert store.get_registration("repo-a").remote_url == registration.remote_url
    assert store.get_job(job.job_id).commit_sha == "a" * 40
    assert store.get_job(job.job_id).trace_context["traceparent"].startswith("00-")


def test_store_rejects_invalid_registration_interval(tmp_path):
    import pytest
    from antisentinel.code_map.models import RepositoryRegistration
    from antisentinel.domain.errors import InvalidInputError

    database = SQLiteDatabase(tmp_path / "code-map.db")
    database.initialize()
    with pytest.raises(InvalidInputError):
        RepositoryRegistration(
            repository_id="repo-a", remote_url="ssh://git.example/repo", credential_ref="credential-a", tracked_ref="refs/heads/main",
            min_interval=3600, max_interval=1800,
        )
