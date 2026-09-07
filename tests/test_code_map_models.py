from datetime import datetime, timezone

import pytest


def test_snapshot_identity_is_canonical_and_scope_bound():
    from antisentinel.code_map.identity import node_id_for, snapshot_id_for

    first = snapshot_id_for("repo-a", "a" * 40, "python-3.13/ast-v1", "rules-v1")
    second = snapshot_id_for("repo-a", "a" * 40, "python-3.13/ast-v1", "rules-v1")

    assert first == second and len(first) == 64
    assert first != snapshot_id_for("repo-b", "a" * 40, "python-3.13/ast-v1", "rules-v1")
    assert node_id_for(first, "pkg/a.py", "function", "run", 1, 2) != node_id_for(first, "pkg/a.py", "function", "run", 3, 4)


def test_registration_defaults_and_validation():
    from antisentinel.code_map.models import RepositoryRegistration
    from antisentinel.domain.errors import InvalidInputError

    registration = RepositoryRegistration(
        repository_id="repo-a", remote_url="ssh://git.example/repo", credential_ref="cred-1", tracked_ref="refs/heads/main",
    )
    assert registration.min_interval == 1800
    assert registration.max_interval == 14400
    assert registration.current_interval is None

    with pytest.raises(InvalidInputError):
        RepositoryRegistration(
            repository_id="repo-a", remote_url="ssh://git.example/repo", credential_ref="cred-1", tracked_ref="refs/heads/main",
            min_interval=0,
        )


def test_job_and_snapshot_validate_utc_and_ranges():
    from antisentinel.code_map.models import CodeChunk, MapSnapshot
    from antisentinel.domain.errors import InvalidInputError

    snapshot = MapSnapshot(
        snapshot_id="s" * 64, repository_id="repo-a", commit_sha="a" * 40,
        parser_revision="python-3.13/ast-v1", rules_digest="rules-v1",
    )
    assert snapshot.status == "building"
    with pytest.raises(InvalidInputError):
        CodeChunk(
            chunk_id="chunk-1", node_id="node-1", snapshot_id=snapshot.snapshot_id, path="a.py",
            start_line=3, end_line=2, byte_start=0, byte_end=1, content_hash="h", commit_sha="a" * 40,
        )
    with pytest.raises(InvalidInputError):
        MapSnapshot(
            snapshot_id="s" * 64, repository_id="repo-a", commit_sha="a" * 40,
            parser_revision="v1", rules_digest="r1", created_at=datetime(2026, 1, 1),
        )
