"""Shared context pack: Working Set + channels under ContextBudget (PRD-002B Phase B)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.turn import Turn
from antisentinel.memory.models import MemoryContextView

from .budget import (
    TRUNCATION_MARKER,
    BudgetReport,
    ContextBudget,
    DroppedItem,
    apply_truncation_marker,
    default_budget,
    estimate_json,
    estimate_model_request,
    estimate_tokens,
)
from .messages import (
    build_system_message,
    build_task_results_message,
    build_tool_events_message,
    build_working_set_message,
)
from .source_window import window_source_content
from .working_set import WorkingSet


@dataclass
class SoftBudget:
    """Deprecated Phase A shim — mapped into ContextBudget when budget is omitted."""

    recent_turn_limit: int = 3
    max_source_slices: int = 4
    max_source_bytes: int = 32 * 1024


# Back-compat alias: callers may still type PackReport.
PackReport = BudgetReport


@dataclass
class PackInput:
    incident: Incident
    session: Session
    turn: Turn
    working_set: WorkingSet | None
    tools: list[dict[str, Any]]
    prior_turns: list[Turn] = field(default_factory=list)
    task_results: list[dict[str, Any]] = field(default_factory=list)
    memory_context: MemoryContextView | None = None
    skill_context: dict[str, Any] | None = None
    source_context: list[Any] | None = None
    system_override: str | None = None
    benchmark_user_prompt: bool = False
    budget: ContextBudget | None = None
    soft_budget: SoftBudget = field(default_factory=SoftBudget)


@dataclass
class PackResult:
    messages: list[dict[str, Any]]
    report: BudgetReport
    tools: list[dict[str, Any]] = field(default_factory=list)


def _resolve_budget(inp: PackInput) -> ContextBudget:
    if inp.budget is not None:
        return inp.budget
    # SoftBudget → ContextBudget bridge (migration window).
    soft = inp.soft_budget
    return ContextBudget(
        max_context_tokens=max(8192, soft.max_source_bytes // 2),
        recent_turn_limit=soft.recent_turn_limit,
        max_source_slices=soft.max_source_slices,
        source_window_by_suffix=default_budget(max_context_tokens=8192).source_window_by_suffix,
    )


def _trim_text_to_tokens(text: str, limit: int, *, version: str, marker: str = TRUNCATION_MARKER) -> str:
    if estimate_tokens(text, version=version) <= limit:
        return text
    max_bytes = max(0, limit * 3)
    encoded = text.encode("utf-8")
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    return apply_truncation_marker(truncated, marker=marker)


def _pack_tools(tools: list[dict[str, Any]], *, budget: ContextBudget, limit: int, report: BudgetReport) -> list[dict[str, Any]]:
    if not tools:
        report.blocks["tools"].used = 0
        return []
    core_n = budget.tools_core_limit
    packed: list[dict[str, Any]] = []
    used = 0
    for index, tool in enumerate(tools):
        item = deepcopy(tool)
        cost = estimate_json(item, version=budget.estimate_version)
        if index < core_n:
            packed.append(item)
            used += cost
            continue
        if used + cost <= limit:
            packed.append(item)
            used += cost
            continue
        # Truncate description for non-core tools.
        desc = str(item.get("description") or "")
        if desc:
            room = max(8, limit - used - estimate_json({**item, "description": ""}, version=budget.estimate_version))
            item["description"] = _trim_text_to_tokens(desc, room, version=budget.estimate_version, marker=budget.truncation_marker)
            item["description_truncated"] = True
            cost = estimate_json(item, version=budget.estimate_version)
            report.blocks["tools"].truncated = True
            report.dropped.append(DroppedItem(kind="tool_desc", reason="over_block", ref=str(item.get("name") or index)))
        if used + cost <= limit:
            packed.append(item)
            used += cost
        else:
            report.dropped.append(DroppedItem(kind="tool_desc", reason="over_block", ref=str(item.get("name") or index)))
            report.blocks["tools"].dropped += 1
    report.blocks["tools"].used = used
    return packed


def _pack_memory(view: MemoryContextView | None, *, budget: ContextBudget, limit: int, report: BudgetReport) -> dict[str, Any] | None:
    if view is None or not (view.digest or view.evidence_refs):
        report.blocks["memory"].used = 0
        return None
    digest = view.digest or ""
    message = {"role": "memory", "session_digest": digest, "evidence_refs": list(view.evidence_refs)}
    cost = estimate_json(message, version=budget.estimate_version)
    if cost > limit:
        room = max(0, limit - estimate_json({**message, "session_digest": ""}, version=budget.estimate_version))
        message["session_digest"] = _trim_text_to_tokens(digest, room, version=budget.estimate_version, marker=budget.truncation_marker)
        report.blocks["memory"].truncated = True
        cost = estimate_json(message, version=budget.estimate_version)
    report.blocks["memory"].used = cost
    return message


def _pack_skill(skill_context: dict[str, Any] | None, *, budget: ContextBudget, limit: int, report: BudgetReport) -> list[dict[str, Any]]:
    if skill_context is None:
        report.blocks["skill"].used = 0
        return []
    messages: list[dict[str, Any]] = []
    if "active_skills" in skill_context:
        messages = [{"role": "skill", **deepcopy(item)} for item in skill_context["active_skills"]]
    elif "selected_skill" in skill_context:
        messages = [{"role": "skill", **deepcopy(skill_context["selected_skill"])}]
    else:
        catalog = deepcopy(skill_context.get("available_skills", []))
        messages = [{"role": "skill_catalog", "skills": catalog}]

    def total() -> int:
        return sum(estimate_json(message, version=budget.estimate_version) for message in messages)

    # Late degradation: catalog descriptions first, then oldest references on skill payloads.
    if total() > limit and messages and messages[0].get("role") == "skill_catalog":
        skills = messages[0].get("skills") or []
        for item in skills:
            if total() <= limit:
                break
            desc = str(item.get("description") or "")
            if not desc:
                continue
            item["description"] = apply_truncation_marker(desc[:80], marker=budget.truncation_marker)
            report.blocks["skill"].truncated = True
            report.dropped.append(
                DroppedItem(kind="skill_ref", reason="over_block", ref=str(item.get("skill_id") or item.get("name") or ""))
            )
    if total() > limit:
        for message in messages:
            if message.get("role") != "skill":
                continue
            refs = message.get("references")
            if not isinstance(refs, list) or not refs:
                continue
            while refs and total() > limit:
                removed = refs.pop(0)
                report.dropped.append(
                    DroppedItem(
                        kind="skill_ref",
                        reason="over_block",
                        ref=str((removed or {}).get("reference_id") or (removed or {}).get("id") or "ref"),
                    )
                )
                report.blocks["skill"].dropped += 1
                report.blocks["skill"].truncated = True
    if total() > limit:
        # Last resort: truncate instruction bodies with readable marker (avoid wiping).
        for message in messages:
            if message.get("role") != "skill":
                continue
            for key in ("instructions", "content", "body"):
                if key in message and isinstance(message[key], str) and message[key]:
                    room = max(32, limit // max(1, len(messages)))
                    message[key] = _trim_text_to_tokens(
                        message[key], room, version=budget.estimate_version, marker=budget.truncation_marker
                    )
                    report.blocks["skill"].truncated = True
        # If still over, keep messages but report overage; strict handled by caller.
    report.blocks["skill"].used = total()
    return messages


def _pack_source(
    source_context: list[Any] | None,
    *,
    budget: ContextBudget,
    limit: int,
    report: BudgetReport,
) -> dict[str, Any] | None:
    if not source_context:
        report.blocks["source"].used = 0
        return None
    slices: list[dict[str, Any]] = []
    used = 0
    remaining = limit
    for index, item in enumerate(source_context):
        if len(slices) >= budget.max_source_slices:
            report.dropped.append(DroppedItem(kind="source", reason="slice_cap", ref=str(getattr(item, "path", index))))
            report.blocks["source"].dropped += 1
            continue
        path = str(getattr(item, "path", "") or "")
        content = getattr(item, "content", "") or ""
        # CodeMap-first pointer: no body — pack stub only (model should re-call read_source).
        if not content:
            slice_obj = {
                "evidence_id": getattr(item, "evidence_id", None),
                "repository_id": getattr(item, "repository_id", None),
                "snapshot_id": getattr(item, "snapshot_id", None),
                "commit_sha": getattr(item, "commit_sha", None),
                "path": path,
                "content_hash": getattr(item, "content_hash", None),
                "pointer": True,
                "note": "source body not pinned; call code_map.read_source to reload",
            }
            cost = estimate_json(slice_obj, version=budget.estimate_version)
            if cost > remaining:
                report.dropped.append(DroppedItem(kind="source", reason="over_block", ref=path or str(index)))
                report.blocks["source"].dropped += 1
                continue
            slices.append(slice_obj)
            used += cost
            remaining = max(0, limit - used)
            continue
        stub = {
            "evidence_id": getattr(item, "evidence_id", None),
            "repository_id": getattr(item, "repository_id", None),
            "snapshot_id": getattr(item, "snapshot_id", None),
            "commit_sha": getattr(item, "commit_sha", None),
            "path": path,
            "content": "",
            "content_hash": getattr(item, "content_hash", None),
            "truncated": True,
        }
        overhead = estimate_json(stub, version=budget.estimate_version)
        content_limit = max(0, remaining - overhead)
        if content_limit <= 0:
            report.dropped.append(DroppedItem(kind="source", reason="over_block", ref=path or str(index)))
            report.blocks["source"].dropped += 1
            continue
        windowed = window_source_content(content, budget=budget, path=path, token_limit=content_limit)
        if not windowed.content and windowed.truncated:
            report.dropped.append(DroppedItem(kind="source", reason="over_block", ref=path or str(index)))
            report.blocks["source"].dropped += 1
            continue
        slice_obj = {
            "evidence_id": getattr(item, "evidence_id", None),
            "repository_id": getattr(item, "repository_id", None),
            "snapshot_id": getattr(item, "snapshot_id", None),
            "commit_sha": getattr(item, "commit_sha", None),
            "path": path,
            "content": windowed.content,
            "content_hash": getattr(item, "content_hash", None),
        }
        if windowed.truncated:
            slice_obj["truncated"] = True
            report.truncated_slices.append(
                {
                    "path": path,
                    "evidence_id": str(getattr(item, "evidence_id", "") or ""),
                    "before_tokens": windowed.before_tokens,
                    "after_tokens": windowed.after_tokens,
                }
            )
            report.blocks["source"].truncated = True
        cost = estimate_json(slice_obj, version=budget.estimate_version)
        if cost > remaining:
            report.dropped.append(DroppedItem(kind="source", reason="over_block", ref=path or str(index)))
            report.blocks["source"].dropped += 1
            continue
        slices.append(slice_obj)
        used += cost
        remaining = max(0, limit - used)
    report.blocks["source"].used = used
    if not slices:
        return None
    return {"role": "source_context", "slices": slices}


def _shrink_working_set(working_set: WorkingSet, *, budget: ContextBudget, limit: int, report: BudgetReport) -> None:
    """Mutate working_set to fit working block; never drop below min_recent_turns."""
    min_k = budget.min_recent_turns

    def working_cost() -> int:
        return estimate_json(build_working_set_message(working_set), version=budget.estimate_version)

    # Digest first.
    while working_cost() > limit and working_set.older_digest:
        target = max(32, estimate_tokens(working_set.older_digest, version=budget.estimate_version) // 2)
        working_set.compact_older(target)
        if working_set.older_digest and not working_set.older_digest.endswith(budget.truncation_marker.strip()):
            # compact_older uses ellipsis; also ensure readable marker when heavily cut.
            if working_set.older_digest.startswith("…") or working_set.older_digest.endswith("…"):
                working_set.older_digest = apply_truncation_marker(
                    working_set.older_digest.rstrip("…"), marker=budget.truncation_marker
                )
        report.blocks["working"].truncated = True
        if estimate_tokens(working_set.older_digest, version=budget.estimate_version) <= target:
            break

    # Lower K but stop at min_recent_turns.
    while working_cost() > limit and len(working_set.recent_turns) > min_k:
        working_set.set_recent_turn_limit(len(working_set.recent_turns) - 1)
        report.compacted_turns = working_set.compacted_turns
        report.blocks["working"].truncated = True

    # Shorten summaries on remaining turns.
    if working_cost() > limit:
        for turn in working_set.recent_turns:
            if working_cost() <= limit:
                break
            turn.plan_summary = _trim_text_to_tokens(
                turn.plan_summary, max(24, limit // 8), version=budget.estimate_version, marker=budget.truncation_marker
            )
            for event in turn.tool_events:
                event.result_summary = _trim_text_to_tokens(
                    event.result_summary,
                    max(24, limit // 8),
                    version=budget.estimate_version,
                    marker=budget.truncation_marker,
                )
            turn.outcome = _trim_text_to_tokens(
                turn.outcome, max(24, limit // 8), version=budget.estimate_version, marker=budget.truncation_marker
            )
            report.blocks["working"].truncated = True


def _clear_channel_drops(report: BudgetReport, *, kind: str) -> None:
    report.dropped = [item for item in report.dropped if item.kind != kind]
    if kind == "source":
        report.truncated_slices = []
        report.blocks["source"].dropped = 0
        report.blocks["source"].truncated = False
    if kind == "skill":
        report.blocks["skill"].dropped = 0
        report.blocks["skill"].truncated = False
    if kind == "memory":
        report.blocks["memory"].dropped = 0
        report.blocks["memory"].truncated = False
    if kind == "tool_desc":
        report.blocks["tools"].dropped = 0
        report.blocks["tools"].truncated = False


def _replace_channel_messages(
    messages: list[dict[str, Any]],
    *,
    roles: set[str],
    new_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    kept = [message for message in messages if message.get("role") not in roles]
    return kept + new_messages


def _sync_working_message(messages: list[dict[str, Any]], working_set: WorkingSet | None) -> None:
    if working_set is None:
        return
    for message in messages:
        if message.get("role") == "user" and "working_set" in message:
            message["working_set"] = build_working_set_message(working_set)


def _degrade_until_fit(
    *,
    inp: PackInput,
    budget: ContextBudget,
    floors: dict[str, int],
    limits: dict[str, int],
    messages: list[dict[str, Any]],
    packed_tools: list[dict[str, Any]],
    report: BudgetReport,
    working_set: WorkingSet | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Shrink elastic channels until wire estimate fits; never below floors."""
    source_limit = limits["source"]
    memory_limit = limits["memory"]
    skill_limit = limits["skill"]
    working_limit = limits["working"]
    tools_limit = limits["tools"]

    for _ in range(16):
        current = estimate_model_request(messages, packed_tools, version=budget.estimate_version)
        if current <= budget.max_context_tokens:
            report.total_estimated = current
            report.within_budget = True
            return messages, packed_tools

        # 1) Source first (anti-S2 / most recoverable).
        if inp.source_context and source_limit > max(floors["source"], 64):
            source_limit = max(floors["source"], source_limit // 2)
            report.degrade_steps.append(f"source->{source_limit}")
            _clear_channel_drops(report, kind="source")
            source_message = _pack_source(inp.source_context, budget=budget, limit=source_limit, report=report)
            report.blocks["source"].limit = source_limit
            messages = _replace_channel_messages(
                messages,
                roles={"source_context"},
                new_messages=[source_message] if source_message is not None else [],
            )
            continue

        # 2) Memory.
        if inp.memory_context is not None and memory_limit > max(floors["memory"], 32):
            memory_limit = max(floors["memory"], memory_limit // 2)
            report.degrade_steps.append(f"memory->{memory_limit}")
            _clear_channel_drops(report, kind="memory")
            memory_message = _pack_memory(inp.memory_context, budget=budget, limit=memory_limit, report=report)
            report.blocks["memory"].limit = memory_limit
            messages = _replace_channel_messages(
                messages,
                roles={"memory"},
                new_messages=[memory_message] if memory_message is not None else [],
            )
            continue

        # 3) Skill body / refs (keep catalog/header via floor).
        if inp.skill_context is not None and skill_limit > max(floors["skill"], 32):
            skill_limit = max(floors["skill"], skill_limit // 2)
            report.degrade_steps.append(f"skill->{skill_limit}")
            _clear_channel_drops(report, kind="skill")
            skill_messages = _pack_skill(inp.skill_context, budget=budget, limit=skill_limit, report=report)
            report.blocks["skill"].limit = skill_limit
            messages = _replace_channel_messages(
                messages,
                roles={"skill", "skill_catalog"},
                new_messages=skill_messages,
            )
            continue

        # 4) Working set toward floor (never below min_recent_turns inside helper).
        if working_set is not None and working_limit > max(floors["working"], 64):
            working_limit = max(floors["working"], working_limit // 2)
            report.degrade_steps.append(f"working->{working_limit}")
            _shrink_working_set(working_set, budget=budget, limit=working_limit, report=report)
            report.blocks["working"].limit = working_limit
            _sync_working_message(messages, working_set)
            # Refresh working used estimate on user message.
            for message in messages:
                if message.get("role") == "user":
                    report.blocks["working"].used = estimate_json(message, version=budget.estimate_version)
            continue

        # 5) Non-core tools last.
        if inp.tools and tools_limit > max(floors["tools"], 64):
            tools_limit = max(floors["tools"], tools_limit // 2)
            report.degrade_steps.append(f"tools->{tools_limit}")
            _clear_channel_drops(report, kind="tool_desc")
            packed_tools = _pack_tools(inp.tools, budget=budget, limit=tools_limit, report=report)
            report.blocks["tools"].limit = tools_limit
            continue

        break

    report.total_estimated = estimate_model_request(messages, packed_tools, version=budget.estimate_version)
    report.within_budget = report.total_estimated <= budget.max_context_tokens
    return messages, packed_tools


def _refill_by_deficit(
    *,
    inp: PackInput,
    budget: ContextBudget,
    soft_caps: dict[str, int],
    messages: list[dict[str, Any]],
    packed_tools: list[dict[str, Any]],
    report: BudgetReport,
) -> list[dict[str, Any]]:
    """Spend headroom by deficit: skill → memory → source(soft) → source(hard max)."""
    from .budget import DEFAULT_WIRE_SAFETY_SCALE

    target = int(budget.max_context_tokens * budget.target_fill_ratio)
    current = estimate_model_request(messages, packed_tools, version=budget.estimate_version)
    if current >= target:
        return messages

    def headroom_struct() -> int:
        wire = max(0, budget.max_context_tokens - estimate_model_request(messages, packed_tools, version=budget.estimate_version))
        return max(0, int(wire / max(DEFAULT_WIRE_SAFETY_SCALE, 1.0)))

    source_hard = budget.source_hard_max()

    # 1) Skill first so instructions are not left empty while source hogs refill.
    if inp.skill_context is not None:
        room = headroom_struct()
        if room >= 64:
            skill_limit = max(soft_caps["skill"], report.blocks["skill"].used + room)
            _clear_channel_drops(report, kind="skill")
            skill_messages = _pack_skill(inp.skill_context, budget=budget, limit=skill_limit, report=report)
            trial = _replace_channel_messages(messages, roles={"skill", "skill_catalog"}, new_messages=skill_messages)
            trial_est = estimate_model_request(trial, packed_tools, version=budget.estimate_version)
            if trial_est <= budget.max_context_tokens:
                messages = trial
                report.blocks["skill"].limit = skill_limit

    # 2) Memory.
    if inp.memory_context is not None:
        room = headroom_struct()
        if room >= 64:
            memory_limit = max(soft_caps["memory"], report.blocks["memory"].used + room)
            _clear_channel_drops(report, kind="memory")
            memory_message = _pack_memory(inp.memory_context, budget=budget, limit=memory_limit, report=report)
            new_msgs = [memory_message] if memory_message is not None else []
            trial = _replace_channel_messages(messages, roles={"memory"}, new_messages=new_msgs)
            trial_est = estimate_model_request(trial, packed_tools, version=budget.estimate_version)
            if trial_est <= budget.max_context_tokens:
                messages = trial
                report.blocks["memory"].limit = memory_limit

    # 3) Source up to soft cap (anti-S2 preference).
    if inp.source_context:
        room = headroom_struct()
        current = estimate_model_request(messages, packed_tools, version=budget.estimate_version)
        if room >= 64 and current < target:
            source_limit = min(soft_caps["source"], report.blocks["source"].used + room)
            source_limit = max(source_limit, report.blocks["source"].used)
            if source_limit > report.blocks["source"].used:
                _clear_channel_drops(report, kind="source")
                source_message = _pack_source(inp.source_context, budget=budget, limit=source_limit, report=report)
                new_msgs = [source_message] if source_message is not None else []
                trial = _replace_channel_messages(messages, roles={"source_context"}, new_messages=new_msgs)
                trial_est = estimate_model_request(trial, packed_tools, version=budget.estimate_version)
                if trial_est <= budget.max_context_tokens:
                    messages = trial
                    report.blocks["source"].limit = source_limit

    # 4) Only if still under target, allow source toward hard max (after floors reserved).
    if inp.source_context:
        room = headroom_struct()
        current = estimate_model_request(messages, packed_tools, version=budget.estimate_version)
        if room >= 64 and current < target:
            source_limit = min(source_hard, report.blocks["source"].used + room)
            if source_limit > report.blocks["source"].limit:
                _clear_channel_drops(report, kind="source")
                source_message = _pack_source(inp.source_context, budget=budget, limit=source_limit, report=report)
                new_msgs = [source_message] if source_message is not None else []
                trial = _replace_channel_messages(messages, roles={"source_context"}, new_messages=new_msgs)
                trial_est = estimate_model_request(trial, packed_tools, version=budget.estimate_version)
                if trial_est <= budget.max_context_tokens:
                    messages = trial
                    report.blocks["source"].limit = source_limit
                else:
                    # restore previous packed source via soft cap
                    _clear_channel_drops(report, kind="source")
                    restore = min(soft_caps["source"], source_hard)
                    source_message = _pack_source(inp.source_context, budget=budget, limit=restore, report=report)
                    messages = _replace_channel_messages(
                        messages,
                        roles={"source_context"},
                        new_messages=[source_message] if source_message is not None else [],
                    )
                    report.blocks["source"].limit = restore

    return messages


def pack_context(inp: PackInput) -> PackResult:
    budget = _resolve_budget(inp)
    floors = budget.floors()
    soft_caps = budget.soft_caps()
    report = budget.empty_report()
    skill_mode = inp.skill_context is not None
    natural = budget.max_context_tokens

    if inp.system_override is not None:
        system_message: dict[str, Any] = {"role": "system", "content": inp.system_override}
    else:
        system_message = build_system_message(skill_mode=skill_mode)
    system_cost = estimate_json(system_message, version=budget.estimate_version)
    report.blocks["system"].used = system_cost
    report.blocks["system"].limit = floors["system"]
    if system_cost > floors["system"] and budget.strict:
        raise ValueError(
            f"system prompt exceeds system budget: used={system_cost} limit={floors['system']}"
        )

    working_set = inp.working_set
    if working_set is not None:
        report.recent_turn_count = len(working_set.recent_turns)
        report.tool_event_count = working_set.tool_event_count()
        report.compacted_turns = working_set.compacted_turns
        # First pass: generous working limit (soft cap), degrade later if needed.
        _shrink_working_set(working_set, budget=budget, limit=max(soft_caps["working"], floors["working"]), report=report)

    messages: list[dict[str, Any]] = [system_message]

    if inp.benchmark_user_prompt:
        user_message: dict[str, Any] = {
            "role": "user",
            "content": inp.incident.summary or inp.incident.title,
        }
    else:
        user_message = {
            "role": "user",
            "incident": {
                "incident_id": inp.incident.incident_id,
                "title": inp.incident.title,
                "source": inp.incident.source,
                "status": inp.incident.status.value,
                "summary": inp.incident.summary,
            },
            "session": {
                "session_id": inp.session.session_id,
                "incident_id": inp.session.incident_id,
                "status": inp.session.status.value,
                "summary": inp.session.summary,
            },
            "turn": {"turn_id": inp.turn.turn_id, "status": inp.turn.status.value},
            "prior_turns": [
                {
                    "turn_id": prior.turn_id,
                    "status": prior.status.value,
                    "summary": prior.output_summary or prior.context_summary,
                }
                for prior in inp.prior_turns
            ],
        }
        if working_set is not None and (
            working_set.recent_turns
            or working_set.older_digest
            or working_set.sticky.active_skill
            or working_set.sticky.source_slice_refs
        ):
            user_message["working_set"] = build_working_set_message(working_set)
    messages.append(user_message)
    report.blocks["working"].used = estimate_json(user_message, version=budget.estimate_version)
    report.blocks["working"].limit = soft_caps["working"]
    if working_set is not None:
        tool_message = build_tool_events_message(working_set)
        if tool_message is not None:
            messages.append(tool_message)
            report.blocks["working"].used += estimate_json(tool_message, version=budget.estimate_version)
    elif inp.task_results:
        tool_message = build_task_results_message(inp.task_results)
        messages.append(tool_message)
        report.blocks["working"].used += estimate_json(tool_message, version=budget.estimate_version)

    # Natural pack: generous limits except source soft-cap (anti-S2 on first pass).
    memory_limit = natural
    source_limit = soft_caps["source"]
    skill_limit = natural
    tools_limit = natural

    memory_message = _pack_memory(inp.memory_context, budget=budget, limit=memory_limit, report=report)
    if memory_message is not None:
        messages.append(memory_message)
    report.blocks["memory"].limit = memory_limit

    source_message = _pack_source(inp.source_context, budget=budget, limit=source_limit, report=report)
    if source_message is not None:
        messages.append(source_message)
    report.blocks["source"].limit = source_limit

    skill_messages = _pack_skill(inp.skill_context, budget=budget, limit=skill_limit, report=report)
    messages.extend(skill_messages)
    report.blocks["skill"].limit = skill_limit

    packed_tools = _pack_tools(inp.tools, budget=budget, limit=tools_limit, report=report)
    report.blocks["tools"].limit = tools_limit

    limits = {
        "system": floors["system"],
        "working": soft_caps["working"],
        "memory": memory_limit,
        "source": source_limit,
        "skill": skill_limit,
        "tools": tools_limit,
    }
    messages, packed_tools = _degrade_until_fit(
        inp=inp,
        budget=budget,
        floors=floors,
        limits=limits,
        messages=messages,
        packed_tools=packed_tools,
        report=report,
        working_set=working_set,
    )

    messages = _refill_by_deficit(
        inp=inp,
        budget=budget,
        soft_caps=soft_caps,
        messages=messages,
        packed_tools=packed_tools,
        report=report,
    )

    report.total_estimated = estimate_model_request(messages, packed_tools, version=budget.estimate_version)
    report.within_budget = report.total_estimated <= budget.max_context_tokens
    if budget.strict and not report.within_budget:
        raise ValueError(
            f"context_pack_exceeded: estimated={report.total_estimated} max={budget.max_context_tokens}"
        )

    if working_set is not None:
        report.recent_turn_count = len(working_set.recent_turns)
        report.tool_event_count = working_set.tool_event_count()
        report.compacted_turns = working_set.compacted_turns

    return PackResult(messages=messages, report=report, tools=packed_tools)


OnBudgetReport = Callable[[BudgetReport], None]
