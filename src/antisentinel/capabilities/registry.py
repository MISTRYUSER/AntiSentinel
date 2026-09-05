"""Immutable catalog views for locally assembled skills."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from antisentinel.tools.manifest import ToolDefinition
from antisentinel.tools.registry import ToolRegistry

from .manifest import SkillManifest


class FrozenToolRegistry:
    """Returns copies so a catalog's frozen tool metadata cannot be mutated."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._definitions = {item.name: _copy_definition(item) for item in definitions}

    def resolve(self, name: str) -> ToolDefinition | None:
        definition = self._definitions.get(name)
        return _copy_definition(definition) if definition else None

    def manifests(self) -> list[dict[str, Any]]:
        return [self.resolve(name).manifest() for name in sorted(self._definitions)]

    def register(self, definition: ToolDefinition) -> None:
        raise TypeError("capability catalog tool registry is immutable")


def _copy_definition(value: ToolDefinition) -> ToolDefinition:
    return ToolDefinition(name=value.name, description=value.description, argument_schema=deepcopy(value.argument_schema),
                          handler=value.handler, requires_approval=value.requires_approval, read_only=value.read_only)


class SkillRegistry:
    def __init__(self, skills: list[SkillManifest]) -> None:
        self._skills = {item.skill_id: item for item in skills}

    def get(self, skill_id: str) -> SkillManifest | None:
        return self._skills.get(skill_id)

    def list(self) -> tuple[SkillManifest, ...]:
        return tuple(self._skills[key] for key in sorted(self._skills))


@dataclass(frozen=True)
class CatalogDiagnostic:
    code: str
    field: str
    message: str
    package_root: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message, "package_root": self.package_root}


@dataclass(frozen=True)
class CapabilityCatalog:
    skills: SkillRegistry
    tool_registry: FrozenToolRegistry
    diagnostics: tuple[CatalogDiagnostic, ...]
    snapshot_id: str
    executable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "skills": [item.to_dict() for item in self.skills.list()],
                "tools": self.tool_registry.manifests(), "diagnostics": [item.to_dict() for item in self.diagnostics],
                "executable": self.executable}

    def available_skills(self, policy: Any) -> tuple[SkillManifest, ...]:
        from .availability import check_availability
        return tuple(item for item in self.skills.list() if check_availability(self, item.skill_id, policy).available)


def catalog_snapshot_id(skills: list[SkillManifest], definitions: list[ToolDefinition]) -> str:
    data = {"skills": [item.to_dict() for item in sorted(skills, key=lambda item: item.skill_id)],
            "tools": [item.manifest() for item in sorted(definitions, key=lambda item: item.name)], "schema_revision": 1}
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
