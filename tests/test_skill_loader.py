"""Loading reads only the requested published resource and verifies its hash."""

import pytest
import errno
from pathlib import Path

from tests.test_skill_catalog import package, write_manifest


def api():
    from antisentinel.capabilities.loader import ResourceCache, SkillLoadError, SkillLoader
    from antisentinel.capabilities.package import build_local_package
    return ResourceCache, SkillLoadError, SkillLoader, build_local_package


def published_with_reference(tmp_path, instruction="diagnose safely", reference="reference facts"):
    _, _, _, build = api()
    source = tmp_path / "source"
    manifest = package(source)
    (source / "diagnosis.md").write_text(instruction)
    (source / "references").mkdir()
    (source / "references" / "guide.md").write_text(reference)
    manifest["skills"][0]["references"] = [{
        "reference_id": "guide", "description": "diagnosis guide", "path": "references/guide.md",
    }]
    write_manifest(source, manifest)
    return build(source, tmp_path / "published")


def test_load_reads_instruction_but_not_reference_until_requested(tmp_path):
    ResourceCache, _, SkillLoader, _ = api()
    published = published_with_reference(tmp_path)
    cache = ResourceCache()
    loader = SkillLoader(published, cache=cache)

    loaded = loader.load("local/diagnosis")

    assert loaded.instruction == "diagnose safely"
    assert loaded.references[0].reference_id == "guide"
    assert cache.load_count == 1
    assert loader.read_reference("local/diagnosis", "guide").content == "reference facts"
    assert cache.load_count == 2


def test_unknown_skill_and_unknown_reference_return_structured_codes(tmp_path):
    _, SkillLoadError, SkillLoader, _ = api()
    loader = SkillLoader(published_with_reference(tmp_path))
    with pytest.raises(SkillLoadError) as unknown_skill:
        loader.load("local/unknown")
    with pytest.raises(SkillLoadError) as unknown_reference:
        loader.read_reference("local/diagnosis", "unknown")
    assert unknown_skill.value.code == "unknown_skill"
    assert unknown_reference.value.code == "unknown_reference"


def test_tampered_published_resource_is_rejected_before_disclosure(tmp_path):
    _, SkillLoadError, SkillLoader, _ = api()
    published = published_with_reference(tmp_path)
    (published.root / "diagnosis.md").write_text("tampered")

    with pytest.raises(SkillLoadError) as error:
        SkillLoader(published).load("local/diagnosis")

    assert error.value.code == "snapshot_mismatch"


def test_context_budget_rejects_full_instruction_without_truncating(tmp_path):
    _, SkillLoadError, SkillLoader, _ = api()
    published = published_with_reference(tmp_path, instruction="x" * 8193)

    with pytest.raises(SkillLoadError) as error:
        SkillLoader(published, instruction_char_budget=8192).load("local/diagnosis")

    assert error.value.code == "context_budget_exceeded"


def test_repeated_load_uses_shared_content_cache_without_reopening_resource(tmp_path):
    ResourceCache, _, SkillLoader, _ = api()
    cache = ResourceCache()
    loader = SkillLoader(published_with_reference(tmp_path), cache=cache)

    assert loader.load("local/diagnosis").instruction == "diagnose safely"
    assert loader.load("local/diagnosis").instruction == "diagnose safely"

    assert cache.load_count == 1


def test_transient_resource_read_retries_at_most_twice(tmp_path, monkeypatch):
    _, _, SkillLoader, _ = api()
    published = published_with_reference(tmp_path)
    original = Path.read_bytes
    attempts = 0

    def transient(path):
        nonlocal attempts
        if path == published.root / "diagnosis.md" and attempts < 2:
            attempts += 1
            raise OSError(errno.EAGAIN, "try again")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", transient)
    assert SkillLoader(published).load("local/diagnosis").instruction == "diagnose safely"
    assert attempts == 2
