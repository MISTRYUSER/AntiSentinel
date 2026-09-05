"""Run-specific visibility checks; this is not execution authorization."""

from __future__ import annotations

from dataclasses import dataclass

from .registry import CapabilityCatalog


@dataclass(frozen=True)
class RunPolicy:
    allowed_tools: frozenset[str]
    disabled_skills: frozenset[str] = frozenset()
    read_only_mode: bool = True
    allow_approval: bool = False


@dataclass(frozen=True)
class Availability:
    available: bool
    reason_codes: tuple[str, ...]


def check_availability(catalog: CapabilityCatalog, skill_id: str, policy: RunPolicy) -> Availability:
    skill = catalog.skills.get(skill_id)
    if skill is None:
        return Availability(False, ("unknown_skill",))
    reasons: list[str] = []
    if skill_id in policy.disabled_skills:
        reasons.append("skill_disabled")
    for name in skill.required_tools:
        tool = catalog.tool_registry.resolve(name)
        if tool is None:
            reasons.append("missing_tool_dependency")
        elif name not in policy.allowed_tools:
            reasons.append("tool_not_allowed")
        elif policy.read_only_mode and not tool.read_only:
            reasons.append("read_only_required")
        elif not policy.allow_approval and tool.requires_approval:
            reasons.append("approval_not_allowed")
    return Availability(not reasons, tuple(dict.fromkeys(reasons)))
