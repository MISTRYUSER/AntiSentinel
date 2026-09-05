"""A run must commit all skill disclosure state or none of it."""

import pytest

from tests.test_skill_catalog import package, write_manifest


def api():
    from antisentinel.capabilities.loader import SkillLoader
    from antisentinel.capabilities.package import build_local_package
    from antisentinel.capabilities.runtime_state import SkillRuntimeState, SkillStateError
    return SkillLoader, build_local_package, SkillRuntimeState, SkillStateError


class RecordingStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.values = []

    def save(self, value):
        if self.fail:
            raise OSError("disk unavailable")
        self.values.append(value)


def fixture(tmp_path):
    SkillLoader, build, SkillRuntimeState, _ = api()
    source = tmp_path / "source"
    manifest = package(source)
    (source / "references").mkdir()
    (source / "references" / "guide.md").write_text("reference")
    manifest["skills"][0]["references"] = [{"reference_id": "guide", "description": "guide", "path": "references/guide.md"}]
    write_manifest(source, manifest)
    published = build(source, tmp_path / "published")
    return SkillLoader(published), SkillRuntimeState(run_id="run-1", release_id=published.release_id)


def test_activation_persists_before_state_becomes_active(tmp_path):
    loader, state = fixture(tmp_path)
    prepared = state.prepare_activation(loader.load("local/diagnosis"))
    store = RecordingStore()

    result = state.commit_activation(prepared, store)

    assert result.error is None
    assert state.selected_skill_id == "local/diagnosis"
    assert state.revision == 1
    assert store.values[0]["selected_skill_id"] == "local/diagnosis"


def test_checkpoint_failure_leaves_no_half_activated_skill(tmp_path):
    loader, state = fixture(tmp_path)
    prepared = state.prepare_activation(loader.load("local/diagnosis"))

    result = state.commit_activation(prepared, RecordingStore(fail=True))

    assert result.error == {"code": "checkpoint_write_failed", "message": "disk unavailable"}
    assert state.selected_skill_id is None
    assert state.revision == 0


def test_same_skill_is_idempotent_and_second_skill_can_activate(tmp_path):
    loader, state = fixture(tmp_path)
    store = RecordingStore()
    first = state.prepare_activation(loader.load("local/diagnosis"))
    state.commit_activation(first, store)
    same = state.prepare_activation(loader.load("local/diagnosis"))

    assert state.commit_activation(same, store).idempotent is True
    second = state.prepare_activation(loader.load("local/runbook"))
    assert state.commit_activation(second, store).error is None
    assert state.active_skill_ids == ("local/diagnosis", "local/runbook")


def test_reference_disclosure_is_atomic_and_run_scoped(tmp_path):
    loader, first = fixture(tmp_path)
    _, second = fixture(tmp_path / "second")
    store = RecordingStore()
    first.commit_activation(first.prepare_activation(loader.load("local/diagnosis")), store)
    reference = loader.read_reference("local/diagnosis", "guide")

    prepared = first.prepare_reference(reference)
    failed = first.commit_reference(prepared, RecordingStore(fail=True))

    assert failed.error["code"] == "checkpoint_write_failed"
    assert first.disclosed_reference_ids == ()
    assert second.disclosed_reference_ids == ()
    assert first.commit_reference(first.prepare_reference(reference), store).error is None
    assert first.disclosed_reference_ids == ("local/diagnosis:guide",)
    assert second.disclosed_reference_ids == ()
