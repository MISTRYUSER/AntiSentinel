"""Strict, metadata-only manifests for trusted local capability packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any


MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTRUCTION_BYTES = 64 * 1024
MAX_REFERENCE_BYTES = 256 * 1024
MAX_SKILLS = 32
MAX_REFERENCES = 32
_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class ManifestError(ValueError):
    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class ReferenceManifest:
    reference_id: str
    description: str
    path: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {"reference_id": self.reference_id, "description": self.description, "path": self.path,
                "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    version: str
    name: str
    description: str
    use_cases: tuple[str, ...]
    instructions_path: str
    instructions_size_bytes: int
    required_tools: tuple[str, ...]
    references: tuple[ReferenceManifest, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"skill_id": self.skill_id, "version": self.version, "name": self.name,
                "description": self.description, "use_cases": list(self.use_cases),
                "instructions_path": self.instructions_path, "instructions_size_bytes": self.instructions_size_bytes,
                "required_tools": list(self.required_tools),
                "references": [item.to_dict() for item in self.references]}


@dataclass(frozen=True)
class PluginManifest:
    root: Path
    plugin_id: str
    version: str
    skills: tuple[SkillManifest, ...]
    tool_exports: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"plugin_id": self.plugin_id, "version": self.version,
                "skills": [item.to_dict() for item in self.skills], "tool_exports": list(self.tool_exports)}


def read_plugin_manifest(root: Path) -> PluginManifest:
    manifest_path = root / "plugin.json"
    try:
        stat = manifest_path.lstat()
    except FileNotFoundError as exc:
        raise ManifestError("resource_missing", "plugin.json", "plugin manifest is missing") from exc
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ManifestError("invalid_manifest", "plugin.json", "plugin manifest must be a regular file")
    if stat.st_size > MAX_MANIFEST_BYTES:
        raise ManifestError("resource_too_large", "plugin.json", "plugin manifest exceeds 65536 bytes")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise ManifestError("invalid_manifest", "plugin.json", "plugin manifest must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError("invalid_manifest", "plugin.json", f"invalid JSON: {exc.msg}") from exc
    except ValueError as exc:
        raise ManifestError("invalid_manifest", "plugin.json", str(exc)) from exc
    return _parse_manifest(root, value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_manifest(root: Path, value: Any) -> PluginManifest:
    _object(value, "manifest", {"schema_version", "plugin_id", "version", "skills", "tool_exports"})
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise ManifestError("invalid_manifest", "schema_version", "schema_version must equal integer 1")
    plugin_id = _identifier(value["plugin_id"], "plugin_id")
    version = _version(value["version"], "version")
    exports = _strings(value["tool_exports"], "tool_exports")
    if len(set(exports)) != len(exports):
        raise ManifestError("invalid_manifest", "tool_exports", "tool_exports contains duplicates")
    raw_skills = value["skills"]
    if not isinstance(raw_skills, list) or not raw_skills or len(raw_skills) > MAX_SKILLS:
        raise ManifestError("invalid_manifest", "skills", "skills must contain 1 to 32 entries")
    skills = tuple(_parse_skill(root, plugin_id, item, index) for index, item in enumerate(raw_skills))
    ids = [item.skill_id for item in skills]
    if len(set(ids)) != len(ids):
        raise ManifestError("invalid_manifest", "skills", "skills contains duplicate skill_id")
    return PluginManifest(root=root, plugin_id=plugin_id, version=version, skills=skills, tool_exports=tuple(exports))


def _parse_skill(root: Path, plugin_id: str, value: Any, index: int) -> SkillManifest:
    field = f"skills[{index}]"
    _object(value, field, {"skill_id", "version", "name", "description", "use_cases", "instructions_path", "required_tools", "references"})
    name = _identifier(value["name"], f"{field}.name")
    skill_id = _string(value["skill_id"], f"{field}.skill_id")
    if skill_id != f"{plugin_id}/{name}":
        raise ManifestError("invalid_manifest", f"{field}.skill_id", "skill_id must be plugin_id/name")
    use_cases = _strings(value["use_cases"], f"{field}.use_cases", maximum=8, item_maximum=128)
    if not use_cases:
        raise ManifestError("invalid_manifest", f"{field}.use_cases", "use_cases must not be empty")
    description = _string(value["description"], f"{field}.description", maximum=512)
    instructions_path = _safe_resource(root, value["instructions_path"], f"{field}.instructions_path", MAX_INSTRUCTION_BYTES)
    required_tools = _strings(value["required_tools"], f"{field}.required_tools")
    refs = value["references"]
    if not isinstance(refs, list) or len(refs) > MAX_REFERENCES:
        raise ManifestError("invalid_manifest", f"{field}.references", "references must contain at most 32 entries")
    references = tuple(_parse_reference(root, item, field, ref_index) for ref_index, item in enumerate(refs))
    if len({item.reference_id for item in references}) != len(references):
        raise ManifestError("invalid_manifest", f"{field}.references", "references contains duplicate reference_id")
    return SkillManifest(skill_id=skill_id, version=_version(value["version"], f"{field}.version"), name=name,
                         description=description, use_cases=tuple(use_cases), instructions_path=instructions_path.relative_to(root).as_posix(),
                         instructions_size_bytes=instructions_path.stat().st_size, required_tools=tuple(required_tools),
                         references=references)


def _parse_reference(root: Path, value: Any, field: str, index: int) -> ReferenceManifest:
    current = f"{field}.references[{index}]"
    _object(value, current, {"reference_id", "description", "path"})
    path = _safe_resource(root, value["path"], f"{current}.path", MAX_REFERENCE_BYTES)
    return ReferenceManifest(reference_id=_identifier(value["reference_id"], f"{current}.reference_id"),
                             description=_string(value["description"], f"{current}.description", maximum=512),
                             path=path.relative_to(root).as_posix(), size_bytes=path.stat().st_size)


def _safe_resource(root: Path, raw_path: Any, field: str, maximum: int) -> Path:
    relative = _string(raw_path, field)
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ManifestError("invalid_manifest", field, "resource path must stay inside package")
    resolved = root / candidate
    try:
        stat = resolved.lstat()
    except FileNotFoundError as exc:
        raise ManifestError("resource_missing", field, "registered resource is missing") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise ManifestError("invalid_manifest", field, "registered resource must be a regular file")
    if stat.st_size > maximum:
        raise ManifestError("resource_too_large", field, f"registered resource exceeds {maximum} bytes")
    return resolved


def _object(value: Any, field: str, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ManifestError("invalid_manifest", field, f"{field} must contain exactly: {', '.join(sorted(expected))}")


def _string(value: Any, field: str, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value or (maximum is not None and len(value) > maximum):
        raise ManifestError("invalid_manifest", field, f"{field} must be a non-empty string")
    return value


def _identifier(value: Any, field: str) -> str:
    value = _string(value, field)
    if not _NAME.fullmatch(value):
        raise ManifestError("invalid_manifest", field, f"{field} must use lowercase letters, digits, and hyphens")
    return value


def _version(value: Any, field: str) -> str:
    value = _string(value, field)
    if not _VERSION.fullmatch(value):
        raise ManifestError("invalid_manifest", field, f"{field} must be semantic version major.minor.patch")
    return value


def _strings(value: Any, field: str, maximum: int | None = None, item_maximum: int | None = None) -> list[str]:
    if not isinstance(value, list) or (maximum is not None and len(value) > maximum):
        raise ManifestError("invalid_manifest", field, f"{field} must be a list")
    return [_string(item, field, item_maximum) for item in value]
