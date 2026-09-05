"""Published local packages preserve exact bytes for the requested version."""

import json

import pytest

from tests.test_skill_catalog import package, write_manifest


def package_api():
    from antisentinel.capabilities.package import PackageError, build_local_package, open_published_package
    return PackageError, build_local_package, open_published_package


def test_pack_is_deterministic_and_copies_registered_bytes(tmp_path):
    PackageError, build, _ = package_api()
    source = tmp_path / "source"
    package(source)
    (source / "diagnosis.md").write_text("original instructions")

    first = build(source, tmp_path / "published")
    second = build(source, tmp_path / "published")

    assert first.release_id == second.release_id
    assert first.resource_hashes == second.resource_hashes
    assert (first.root / "diagnosis.md").read_text() == "original instructions"
    assert len(first.release_id) == 64
    assert first.lock["plugin_version"] == "1.0.0"


def test_same_plugin_version_with_different_content_is_rejected(tmp_path):
    PackageError, build, _ = package_api()
    source = tmp_path / "source"
    package(source)
    build(source, tmp_path / "published")
    (source / "diagnosis.md").write_text("changed instructions")

    with pytest.raises(PackageError) as error:
        build(source, tmp_path / "published")

    assert error.value.code == "same_version_content_conflict"


def test_new_version_can_coexist_and_old_release_stays_readable(tmp_path):
    _, build, open_published_package = package_api()
    source = tmp_path / "source"
    manifest = package(source)
    (source / "diagnosis.md").write_text("version one")
    version_one = build(source, tmp_path / "published")
    manifest["version"] = "1.0.1"
    manifest["skills"][0]["version"] = "1.0.1"
    manifest["skills"][1]["version"] = "1.0.1"
    write_manifest(source, manifest)
    (source / "diagnosis.md").write_text("version two")

    version_two = build(source, tmp_path / "published")
    restored_one = open_published_package(version_one.root)

    assert version_one.release_id != version_two.release_id
    assert restored_one.plugin.version == "1.0.0"
    assert (restored_one.root / "diagnosis.md").read_text() == "version one"
    assert (version_two.root / "diagnosis.md").read_text() == "version two"


def test_release_lock_keeps_only_identity_and_hashes_not_instruction_text(tmp_path):
    _, build, _ = package_api()
    source = tmp_path / "source"
    package(source)
    (source / "diagnosis.md").write_text("do not put this in lock")

    published = build(source, tmp_path / "published")
    lock_text = (published.root / "release-lock.json").read_text()

    assert "do not put this in lock" not in lock_text
    assert json.loads(lock_text)["resource_hashes"]["diagnosis.md"] == published.resource_hashes["diagnosis.md"]
