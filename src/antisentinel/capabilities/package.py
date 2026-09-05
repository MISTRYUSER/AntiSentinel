"""Build and open immutable local capability releases."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from .manifest import MAX_INSTRUCTION_BYTES, MAX_REFERENCE_BYTES, PluginManifest, read_plugin_manifest


MAX_PACKAGE_RESOURCE_BYTES = 8 * 1024 * 1024


class PackageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PublishedPackage:
    release_id: str
    root: Path
    plugin: PluginManifest
    resource_hashes: dict[str, str]
    lock: dict[str, Any]


def build_local_package(source_root: Path | str, output_root: Path | str) -> PublishedPackage:
    source = Path(source_root)
    target = Path(output_root)
    plugin = read_plugin_manifest(source)
    resource_bytes = _registered_resources(plugin)
    resource_hashes = {path: _sha256(value) for path, value in resource_bytes.items()}
    lock = _lock_value(plugin, resource_hashes)
    release_id = _sha256(_canonical(lock))
    version_root = target / plugin.plugin_id / plugin.version
    release_root = version_root / release_id
    index_path = version_root / "release-lock.json"
    if index_path.exists():
        existing = json.loads(index_path.read_text(encoding="utf-8"))
        if existing["release_id"] != release_id:
            raise PackageError("same_version_content_conflict", "same plugin version has different content")
        return open_published_package(release_root)
    stage = version_root / f".{release_id}.staging"
    if stage.exists():
        raise PackageError("publish_conflict", "staging directory already exists")
    stage.mkdir(parents=True)
    try:
        for relative, value in resource_bytes.items():
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        (stage / "plugin.json").write_bytes((source / "plugin.json").read_bytes())
        _write_json(stage / "release-lock.json", {"release_id": release_id, **lock})
        os.replace(stage, release_root)
        _write_json(index_path, {"release_id": release_id})
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return open_published_package(release_root)


def open_published_package(root: Path | str) -> PublishedPackage:
    release_root = Path(root)
    lock_path = release_root / "release-lock.json"
    try:
        raw_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError("resource_missing", "published release lock is missing or invalid") from exc
    release_id = raw_lock.get("release_id")
    if not isinstance(release_id, str) or len(release_id) != 64:
        raise PackageError("invalid_manifest", "published release lock has invalid release_id")
    plugin = read_plugin_manifest(release_root)
    hashes = raw_lock.get("resource_hashes")
    if not isinstance(hashes, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in hashes.items()):
        raise PackageError("invalid_manifest", "published release lock has invalid resource_hashes")
    expected = _lock_value(plugin, hashes)
    if _sha256(_canonical(expected)) != release_id:
        raise PackageError("snapshot_mismatch", "published release lock does not match metadata")
    return PublishedPackage(release_id=release_id, root=release_root, plugin=plugin, resource_hashes=dict(hashes), lock=expected)


def _registered_resources(plugin: PluginManifest) -> dict[str, bytes]:
    paths: set[str] = set()
    for skill in plugin.skills:
        paths.add(skill.instructions_path)
        paths.update(reference.path for reference in skill.references)
    values: dict[str, bytes] = {}
    total = 0
    for relative in sorted(paths):
        data = (plugin.root / relative).read_bytes()
        maximum = MAX_INSTRUCTION_BYTES if any(skill.instructions_path == relative for skill in plugin.skills) else MAX_REFERENCE_BYTES
        if len(data) > maximum:
            raise PackageError("resource_too_large", f"resource exceeds its declared limit: {relative}")
        total += len(data)
        if total > MAX_PACKAGE_RESOURCE_BYTES:
            raise PackageError("resource_too_large", "registered resources exceed 8 MiB")
        values[relative] = data
    return values


def _lock_value(plugin: PluginManifest, hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "plugin_id": plugin.plugin_id,
        "plugin_version": plugin.version,
        "manifest_schema_version": 1,
        "resource_hashes": dict(sorted(hashes.items())),
        "skills": [{"skill_id": item.skill_id, "skill_version": item.version,
                    "required_tools": list(item.required_tools)} for item in plugin.skills],
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
