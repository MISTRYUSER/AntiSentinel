"""Run-scoped Skill control tools and progressive model-context views."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry

from .loader import LoadedReference, LoadedSkill, SkillLoadError, SkillLoader
from .runtime_state import SkillRuntimeState, SkillStateError, SkillStateStore


class InMemorySkillStateStore:
    def __init__(self) -> None:
        self.value: dict[str, Any] | None = None

    def save(self, value: dict[str, Any]) -> None:
        self.value = deepcopy(value)


@dataclass
class SkillRuntime:
    loader: SkillLoader
    state: SkillRuntimeState
    state_store: SkillStateStore
    allowed_tool_names: frozenset[str]
    base_tool_names: frozenset[str]

    def __post_init__(self) -> None:
        self._loaded_skills: dict[str, LoadedSkill] = {}
        self._references: dict[str, LoadedReference] = {}
        self._allow_followup_tools = True

    def bind_registry(self, base: ToolRegistry) -> ToolRegistry:
        registry = ToolRegistry(auto_discover=False)
        for manifest in base.manifests():
            definition = base.resolve(manifest["name"])
            assert definition is not None
            registry.register(definition)
        registry.register(ToolDefinition(
            name="skill.load", description="Load one available skill by exact id.",
            argument_schema={"type": "object", "properties": {"skill_id": {"type": "string"}},
                             "required": ["skill_id"], "additionalProperties": False}, handler=self._load,
        ))
        registry.register(ToolDefinition(
            name="skill.read_reference", description="Read one reference from the active skill.",
            argument_schema={"type": "object", "properties": {"reference_id": {"type": "string"}, "skill_id": {"type": "string"}},
                             "required": ["reference_id"], "additionalProperties": False}, handler=self._read_reference,
        ))
        return registry

    def select(self, skill_id: str) -> ToolExecutionResult:
        return self._load({"skill_id": skill_id})

    def visible_tool_names(self) -> frozenset[str]:
        names = {"skill.load"} | set(self.base_tool_names & self.allowed_tool_names)
        if self._loaded_skills:
            names.update({"skill.read_reference"})
            for skill in self._loaded_skills.values():
                names.update(set(self._required_tools(skill)) & self.allowed_tool_names)
        return frozenset(names)

    def manifests(self, registry: ToolRegistry) -> list[dict[str, Any]]:
        visible = self.visible_tool_names()
        return [item for item in registry.manifests() if item["name"] in visible]

    def context_payload(self) -> dict[str, Any]:
        if not self._loaded_skills:
            return {"available_skills": [
                {"skill_id": skill.skill_id, "version": skill.version, "description": skill.description,
                 "use_cases": list(skill.use_cases)}
                for skill in self.loader.package.plugin.skills
                if set(skill.required_tools) <= self.allowed_tool_names
            ]}
        return {"active_skills": [
            {"skill_id": skill.skill_id, "version": skill.version, "instruction_hash": skill.instruction_hash,
             "instructions": skill.instruction, "allow_followup_tools": self._allow_followup_tools,
             "references": [{"reference_id": item.reference_id, "content_hash": item.content_hash, "content": item.content}
                            for item in self._references.values() if item.skill_id == skill.skill_id]}
            for skill in self._loaded_skills.values()
        ]}

    def note_turn_results(self, successful_tool_names: list[str]) -> None:
        """Only successful Skill control calls justify another tool-enabled turn."""
        self._allow_followup_tools = bool(successful_tool_names) and all(
            name in {"skill.load", "skill.read_reference"} for name in successful_tool_names
        )

    def snapshot(self) -> dict[str, Any]:
        return self.state._as_dict(selected=None, disclosed=self.state._disclosed, revision=self.state.revision)

    def restore(self, snapshot: dict[str, Any]) -> None:
        if snapshot.get("release_id") != self.state.release_id:
            raise SkillStateError("snapshot_mismatch", "checkpoint belongs to another release")
        active = snapshot.get("active_skills", [])
        if not isinstance(active, list):
            raise SkillStateError("snapshot_mismatch", "checkpoint active_skills is invalid")
        loaded: dict[str, LoadedSkill] = {}
        for item in active:
            skill = self.loader.load(item["skill_id"])
            if skill.version != item.get("skill_version") or skill.instruction_hash != item.get("instruction_hash"):
                raise SkillStateError("snapshot_mismatch", "checkpoint skill identity changed")
            loaded[skill.skill_id] = skill
        references: dict[str, LoadedReference] = {}
        for item in snapshot.get("disclosed_references", []):
            reference = self.loader.read_reference(item["skill_id"], item["reference_id"])
            if reference.content_hash != item.get("content_hash"):
                raise SkillStateError("snapshot_mismatch", "checkpoint reference identity changed")
            references[f"{reference.skill_id}:{reference.reference_id}"] = reference
        self._loaded_skills = loaded
        self._references = references
        self.state._active_skills = {skill.skill_id: (skill.version, skill.instruction_hash) for skill in loaded.values()}
        self.state._disclosed = references
        self.state.revision = int(snapshot.get("revision", 0))
        self.state.selected_skill_id = snapshot.get("selected_skill_id")
        self.state.selected_skill_version = snapshot.get("selected_skill_version")
        self.state.instruction_hash = snapshot.get("instruction_hash")

    def usage(self) -> dict[str, Any]:
        return {"release_id": self.state.release_id, "selected_skill_id": self.state.selected_skill_id,
                "selected_skill_version": self.state.selected_skill_version, "instruction_hash": self.state.instruction_hash,
                "active_skill_ids": list(self._loaded_skills),
                "reference_hashes": {key: value.content_hash for key, value in self._references.items()}}

    def _load(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        try:
            loaded = self.loader.load(arguments["skill_id"])
            missing = sorted(set(self._required_tools(loaded)) - self.allowed_tool_names)
            if missing:
                return _rejected("tool_not_allowed", f"skill requires unavailable tool: {', '.join(missing)}")
            prepared = self.state.prepare_activation(loaded)
            committed = self.state.commit_activation(prepared, self.state_store)
            if committed.error is not None:
                return ToolExecutionResult(status="failed", error=committed.error, result_summary=committed.error["message"])
            self._loaded_skills[loaded.skill_id] = loaded
            return ToolExecutionResult(status="succeeded", result={"skill_id": loaded.skill_id, "version": loaded.version,
                                       "instruction_hash": loaded.instruction_hash, "idempotent": committed.idempotent},
                                       result_summary=f"loaded skill {loaded.skill_id}@{loaded.version}")
        except (SkillLoadError, SkillStateError) as exc:
            return _rejected(exc.code, str(exc))

    def _read_reference(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        requested_skill_id = arguments.get("skill_id")
        if requested_skill_id is None and len(self._loaded_skills) == 1:
            requested_skill_id = next(iter(self._loaded_skills))
        if requested_skill_id is None:
            return _rejected("skill_id_required", "skill_id is required when multiple skills are active")
        loaded = self._loaded_skills.get(requested_skill_id)
        if loaded is None:
            return _rejected("skill_not_active", "no skill is active")
        try:
            reference = self.loader.read_reference(loaded.skill_id, arguments["reference_id"])
            prepared = self.state.prepare_reference(reference)
            committed = self.state.commit_reference(prepared, self.state_store)
            if committed.error is not None:
                return ToolExecutionResult(status="failed", error=committed.error, result_summary=committed.error["message"])
            self._references[f"{reference.skill_id}:{reference.reference_id}"] = reference
            return ToolExecutionResult(status="succeeded", result={"reference_id": reference.reference_id,
                                       "content_hash": reference.content_hash, "idempotent": committed.idempotent},
                                       result_summary=f"read skill reference {reference.reference_id}")
        except (SkillLoadError, SkillStateError) as exc:
            return _rejected(exc.code, str(exc))

    def _required_tools(self, loaded: LoadedSkill) -> tuple[str, ...]:
        skill_id = loaded.skill_id
        skill = next(item for item in self.loader.package.plugin.skills if item.skill_id == skill_id)
        return skill.required_tools


def _rejected(code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(status="rejected", error={"code": code, "message": message}, result_summary=message)
