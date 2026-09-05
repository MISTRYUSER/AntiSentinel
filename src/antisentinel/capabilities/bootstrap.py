"""Trusted local plugin assembly with all-or-nothing package registration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from antisentinel.tools.discovery import _definitions_from
from antisentinel.tools.manifest import ToolDefinition
from antisentinel.tools.registry import ToolRegistry

from .manifest import ManifestError, PluginManifest, read_plugin_manifest
from .registry import CapabilityCatalog, CatalogDiagnostic, FrozenToolRegistry, SkillRegistry, catalog_snapshot_id
from .availability import RunPolicy, check_availability


@dataclass(frozen=True)
class PluginConfig:
    root: Path | str
    required: bool = True
    trusted_tool_modules: dict[str, ModuleType] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))


class CatalogBuildError(RuntimeError):
    def __init__(self, diagnostics: tuple[CatalogDiagnostic, ...]) -> None:
        super().__init__("required capability packages failed validation")
        self.diagnostics = diagnostics


@dataclass
class _Candidate:
    config: PluginConfig
    manifest: PluginManifest
    tools: list[ToolDefinition]


def assemble_plugins(configs: list[PluginConfig], base_registry: ToolRegistry) -> CapabilityCatalog:
    base = [base_registry.resolve(item["name"]) for item in base_registry.manifests()]
    base = [item for item in base if item is not None]
    candidates: list[_Candidate] = []
    diagnostics: list[CatalogDiagnostic] = []
    required_errors: list[CatalogDiagnostic] = []
    for config in configs:
        try:
            manifest = read_plugin_manifest(config.root)
            exports = _exports(config, manifest)
            names = {item.name for item in base}
            if any(item.name in names for item in exports) or len({item.name for item in exports}) != len(exports):
                raise ManifestError("invalid_manifest", "tool_exports", "tool export conflicts with an existing tool")
            all_tools = {item.name: item for item in [*base, *exports]}
            for skill in manifest.skills:
                missing = [name for name in skill.required_tools if name not in all_tools]
                if missing:
                    raise ManifestError("missing_tool_dependency", f"skills[{skill.skill_id}].required_tools",
                                        f"missing tool dependency: {', '.join(missing)}")
            candidates.append(_Candidate(config, manifest, exports))
        except ManifestError as exc:
            diagnostic = CatalogDiagnostic(exc.code, exc.field, str(exc), str(config.root))
            diagnostics.append(diagnostic)
            if config.required:
                required_errors.append(diagnostic)
    conflicts = _conflicts(candidates)
    if conflicts:
        conflict_field = {id(candidate): identity for identity, pair in conflicts.items() for candidate in pair}
        conflicted = set(conflict_field)
        for candidate in candidates:
            identity = conflict_field.get(id(candidate))
            if identity is None:
                continue
            diagnostic = CatalogDiagnostic("invalid_manifest", identity, f"conflicting {identity} across packages", str(candidate.config.root))
            diagnostics.append(diagnostic)
            if candidate.config.required:
                required_errors.append(diagnostic)
        candidates = [item for item in candidates if id(item) not in conflicted]
    if required_errors:
        raise CatalogBuildError(tuple(required_errors))
    definitions = [*base, *(definition for candidate in candidates for definition in candidate.tools)]
    skills = [skill for candidate in candidates for skill in candidate.manifest.skills]
    return CapabilityCatalog(skills=SkillRegistry(skills), tool_registry=FrozenToolRegistry(definitions),
                             diagnostics=tuple(diagnostics), snapshot_id=catalog_snapshot_id(skills, definitions))


def _exports(config: PluginConfig, manifest: PluginManifest) -> list[ToolDefinition]:
    result: list[ToolDefinition] = []
    for name in manifest.tool_exports:
        module = config.trusted_tool_modules.get(name)
        if module is None:
            raise ManifestError("invalid_manifest", "tool_exports", f"tool module is not trusted: {name}")
        try:
            result.extend(_definitions_from(module))
        except Exception as exc:  # normalized at manifest boundary
            raise ManifestError("invalid_manifest", "tool_exports", str(exc)) from exc
    return result


def _conflicts(candidates: list[_Candidate]) -> dict[str, list[_Candidate]]:
    values: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        values.setdefault(f"plugin_id:{candidate.manifest.plugin_id}", []).append(candidate)
        for skill in candidate.manifest.skills:
            values.setdefault(f"skill_id:{skill.skill_id}", []).append(candidate)
        for tool in candidate.tools:
            values.setdefault(f"tool:{tool.name}", []).append(candidate)
    return {key: value for key, value in values.items() if len(value) > 1}
