"""Run-scoped atomic skill activation and reference disclosure state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .loader import LoadedReference, LoadedSkill


class SkillStateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillStateStore(Protocol):
    def save(self, value: dict[str, Any]) -> None:
        """Persist the whole next state before it becomes observable."""


@dataclass(frozen=True)
class PreparedActivation:
    skill: LoadedSkill
    expected_revision: int
    idempotent: bool = False


@dataclass(frozen=True)
class PreparedReference:
    reference: LoadedReference
    expected_revision: int
    idempotent: bool = False


@dataclass(frozen=True)
class CommitResult:
    error: dict[str, str] | None = None
    idempotent: bool = False


class SkillRuntimeState:
    def __init__(self, *, run_id: str, release_id: str, reference_char_budget: int = 32768) -> None:
        self.run_id = run_id
        self.release_id = release_id
        self.reference_char_budget = reference_char_budget
        self.revision = 0
        self.selected_skill_id: str | None = None
        self.selected_skill_version: str | None = None
        self.instruction_hash: str | None = None
        self._active_skills: dict[str, tuple[str, str]] = {}
        self._disclosed: dict[str, LoadedReference] = {}

    @property
    def active_skill_ids(self) -> tuple[str, ...]:
        return tuple(self._active_skills)

    @property
    def disclosed_reference_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._disclosed))

    def prepare_activation(self, skill: LoadedSkill) -> PreparedActivation:
        self._check_release(skill.release_id)
        if skill.skill_id in self._active_skills:
            if self._active_skills[skill.skill_id] == (skill.version, skill.instruction_hash):
                return PreparedActivation(skill, self.revision, idempotent=True)
            raise SkillStateError("snapshot_mismatch", "active skill identity changed")
        return PreparedActivation(skill, self.revision)

    def commit_activation(self, prepared: PreparedActivation, store: SkillStateStore) -> CommitResult:
        if prepared.idempotent:
            return CommitResult(idempotent=True)
        if prepared.expected_revision != self.revision:
            return CommitResult(error={"code": "activation_conflict", "message": "runtime state changed"})
        next_active = {**self._active_skills, prepared.skill.skill_id: (prepared.skill.version, prepared.skill.instruction_hash)}
        next_state = self._as_dict(selected=prepared.skill, disclosed=self._disclosed, revision=self.revision + 1, active=next_active)
        try:
            store.save(next_state)
        except OSError as exc:
            return CommitResult(error={"code": "checkpoint_write_failed", "message": str(exc)})
        self._active_skills = next_active
        if self.selected_skill_id is None:
            self.selected_skill_id = prepared.skill.skill_id
            self.selected_skill_version = prepared.skill.version
            self.instruction_hash = prepared.skill.instruction_hash
        self.revision += 1
        return CommitResult()

    def prepare_reference(self, reference: LoadedReference) -> PreparedReference:
        self._check_release(reference.release_id)
        if reference.skill_id not in self._active_skills:
            raise SkillStateError("skill_not_active", "reference skill is not active")
        key = f"{reference.skill_id}:{reference.reference_id}"
        if key in self._disclosed:
            return PreparedReference(reference, self.revision, idempotent=True)
        total = sum(len(item.content) for item in self._disclosed.values()) + len(reference.content)
        if total > self.reference_char_budget:
            raise SkillStateError("context_budget_exceeded", "reference disclosure exceeds cumulative budget")
        return PreparedReference(reference, self.revision)

    def commit_reference(self, prepared: PreparedReference, store: SkillStateStore) -> CommitResult:
        if prepared.idempotent:
            return CommitResult(idempotent=True)
        if prepared.expected_revision != self.revision:
            return CommitResult(error={"code": "activation_conflict", "message": "runtime state changed"})
        next_disclosed = {**self._disclosed, f"{prepared.reference.skill_id}:{prepared.reference.reference_id}": prepared.reference}
        next_state = self._as_dict(selected=None, disclosed=next_disclosed, revision=self.revision + 1)
        try:
            store.save(next_state)
        except OSError as exc:
            return CommitResult(error={"code": "checkpoint_write_failed", "message": str(exc)})
        self._disclosed = next_disclosed
        self.revision += 1
        return CommitResult()

    def _as_dict(self, *, selected: LoadedSkill | None, disclosed: dict[str, LoadedReference], revision: int, active: dict[str, tuple[str, str]] | None = None) -> dict[str, Any]:
        skill_id = selected.skill_id if selected else self.selected_skill_id
        skill_version = selected.version if selected else self.selected_skill_version
        instruction_hash = selected.instruction_hash if selected else self.instruction_hash
        return {"run_id": self.run_id, "release_id": self.release_id, "revision": revision,
                "selected_skill_id": skill_id, "selected_skill_version": skill_version,
                "instruction_hash": instruction_hash,
                "active_skills": [{"skill_id": key, "skill_version": value[0], "instruction_hash": value[1]} for key, value in (active or self._active_skills).items()],
                "disclosed_references": [{"skill_id": item.skill_id, "reference_id": item.reference_id, "content_hash": item.content_hash}
                                         for item in sorted(disclosed.values(), key=lambda value: value.reference_id)]}

    def _check_release(self, release_id: str) -> None:
        if release_id != self.release_id:
            raise SkillStateError("snapshot_mismatch", "loaded content belongs to another release")
