"""Phase A SoftBudget-style pack (eval-only control) vs Phase B ContextBudget.

Used by stress compare; not a production dual path.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.turn import Turn
from antisentinel.memory.models import MemoryContextView
from antisentinel.worker.runtime.budget import (
    BudgetReport,
    BlockUsage,
    ContextBudget,
    DroppedItem,
    ESTIMATE_VERSION,
    estimate_json,
    estimate_model_request,
)
from antisentinel.worker.runtime.messages import (
    build_system_message,
    build_task_results_message,
    build_tool_events_message,
    build_working_set_message,
)
from antisentinel.worker.runtime.pack import PackInput, PackResult, SoftBudget, pack_context
from antisentinel.worker.runtime.working_set import WorkingSet


def pack_phase_b(inp: PackInput) -> PackResult:
    """Current Phase B authority path."""
    return pack_context(inp)


def pack_phase_a_soft(inp: PackInput) -> PackResult:
    """Replay Phase A SoftBudget behavior: source byte/slice cap with silent break; no global token gate."""
    soft = inp.soft_budget or SoftBudget()
    skill_mode = inp.skill_context is not None
    if inp.system_override is not None:
        messages: list[dict[str, Any]] = [{"role": "system", "content": inp.system_override}]
    else:
        messages = [build_system_message(skill_mode=skill_mode)]

    working_set = inp.working_set
    if inp.benchmark_user_prompt:
        user_message: dict[str, Any] = {"role": "user", "content": inp.incident.summary or inp.incident.title}
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
        if working_set is not None and (working_set.recent_turns or working_set.older_digest or working_set.sticky.active_skill):
            user_message["working_set"] = build_working_set_message(working_set)
    messages.append(user_message)

    if working_set is not None:
        tool_message = build_tool_events_message(working_set)
        if tool_message is not None:
            messages.append(tool_message)
    elif inp.task_results:
        messages.append(build_task_results_message(inp.task_results))

    if inp.memory_context is not None and (inp.memory_context.digest or inp.memory_context.evidence_refs):
        messages.append(
            {
                "role": "memory",
                "session_digest": inp.memory_context.digest,
                "evidence_refs": list(inp.memory_context.evidence_refs),
            }
        )

    dropped: list[DroppedItem] = []
    source_slice_count = 0
    silent_breaks = 0
    if inp.source_context:
        budget_bytes = soft.max_source_bytes
        slices = []
        for index, item in enumerate(inp.source_context):
            if len(slices) >= soft.max_source_slices:
                dropped.append(DroppedItem(kind="source", reason="slice_cap", ref=str(getattr(item, "path", index))))
                continue
            content = getattr(item, "content", "") or ""
            size = len(content.encode("utf-8"))
            if size > budget_bytes:
                # Phase A: silent break — no truncated marker, remaining slices abandoned.
                silent_breaks += 1
                dropped.append(DroppedItem(kind="source", reason="silent_break", ref=str(getattr(item, "path", index))))
                for later in inp.source_context[index + 1 :]:
                    dropped.append(
                        DroppedItem(kind="source", reason="silent_break_tail", ref=str(getattr(later, "path", "")))
                    )
                break
            slices.append(
                {
                    "evidence_id": getattr(item, "evidence_id", None),
                    "repository_id": getattr(item, "repository_id", None),
                    "snapshot_id": getattr(item, "snapshot_id", None),
                    "commit_sha": getattr(item, "commit_sha", None),
                    "path": getattr(item, "path", None),
                    "content": content,
                    "content_hash": getattr(item, "content_hash", None),
                }
            )
            budget_bytes -= size
        source_slice_count = len(slices)
        if slices:
            messages.append({"role": "source_context", "slices": slices})

    if inp.skill_context is not None:
        if "active_skills" in inp.skill_context:
            messages.extend({"role": "skill", **deepcopy(item)} for item in inp.skill_context["active_skills"])
        elif "selected_skill" in inp.skill_context:
            messages.append({"role": "skill", **deepcopy(inp.skill_context["selected_skill"])})
        else:
            messages.append({"role": "skill_catalog", "skills": deepcopy(inp.skill_context.get("available_skills", []))})

    tools = list(deepcopy(inp.tools))
    max_tokens = inp.budget.max_context_tokens if inp.budget is not None else 8192
    total = estimate_model_request(messages, tools)
    report = BudgetReport(
        estimate_version=ESTIMATE_VERSION,
        max_context_tokens=max_tokens,
        strict=False,
        blocks={
            name: BlockUsage(used=0, limit=0)
            for name in ("system", "working", "memory", "source", "skill", "tools")
        },
        dropped=dropped,
        recent_turn_count=len(working_set.recent_turns) if working_set else 0,
        tool_event_count=working_set.tool_event_count() if working_set else 0,
        compacted_turns=working_set.compacted_turns if working_set else 0,
        total_estimated=total,
        within_budget=total <= max_tokens,
    )
    report.blocks["source"].used = source_slice_count
    report.blocks["source"].dropped = len([item for item in dropped if item.kind == "source"])
    # Encode silent discard observability gap for metrics.
    report.truncated_slices = []
    if silent_breaks:
        report.dropped.append(DroppedItem(kind="source", reason="silent_break_count", ref=str(silent_breaks)))
    return PackResult(messages=messages, report=report, tools=tools)


@dataclass
class StressMetrics:
    label: str
    within_budget: bool
    total_estimated: int
    max_context_tokens: int
    recent_turn_count: int
    recent_turn_limit: int
    early_marker_hit: bool
    source_slice_count: int
    truncated_slice_count: int
    dropped_count: int
    silent_discard_count: int
    skill_instructions_present: bool
    skill_catalog_truncated: bool
    tools_core_intact: bool
    tools_packed_count: int
    useful_tokens: int
    useful_in_budget: int
    budget_fill: float
    overhead_ratio: float
    # Deprecated alias for docs continuity; prefer budget_fill / useful_in_budget.
    effective_payload_ratio: float
    bill_fields_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "within_budget": self.within_budget,
            "total_estimated": self.total_estimated,
            "max_context_tokens": self.max_context_tokens,
            "hard_limit_exceeded": self.total_estimated > self.max_context_tokens,
            "budget_fill": round(self.budget_fill, 4),
            "useful_tokens": self.useful_tokens,
            "useful_in_budget": self.useful_in_budget,
            "overhead_ratio": round(self.overhead_ratio, 4),
            "recent_turn_count": self.recent_turn_count,
            "recent_turn_completion_rate": (
                self.recent_turn_count / self.recent_turn_limit if self.recent_turn_limit else 0.0
            ),
            "early_marker_hit": self.early_marker_hit,
            "source_slice_count": self.source_slice_count,
            "truncated_slice_count": self.truncated_slice_count,
            "dropped_count": self.dropped_count,
            "silent_discard_count": self.silent_discard_count,
            "skill_instructions_present": self.skill_instructions_present,
            "skill_catalog_truncated": self.skill_catalog_truncated,
            "tools_core_intact": self.tools_core_intact,
            "tools_packed_count": self.tools_packed_count,
            "effective_payload_ratio": round(self.effective_payload_ratio, 4),
            "bill_fields_present": self.bill_fields_present,
        }


def measure_pack(
    *,
    label: str,
    result: PackResult,
    early_marker: str,
    recent_turn_limit: int,
    core_tool_names: list[str],
) -> StressMetrics:
    corpus = str(result.messages)
    source_slices = []
    for message in result.messages:
        if message.get("role") == "source_context":
            source_slices.extend(message.get("slices") or [])
    silent = sum(1 for item in result.report.dropped if item.reason.startswith("silent_break"))
    skill_instructions = False
    catalog_truncated = False
    for message in result.messages:
        if message.get("role") == "skill":
            for key in ("instructions", "content", "body"):
                if message.get(key):
                    skill_instructions = True
        if message.get("role") == "skill_catalog":
            for item in message.get("skills") or []:
                desc = str(item.get("description") or "")
                if "[truncated]" in desc:
                    catalog_truncated = True
    packed_names = [str(tool.get("name") or "") for tool in result.tools]
    core_intact = all(name in packed_names for name in core_tool_names) if core_tool_names else True
    task_roles = {"user", "tool", "memory", "skill", "skill_catalog", "source_context"}
    useful = sum(estimate_json(message) for message in result.messages if message.get("role") in task_roles)
    useful += estimate_json(result.tools)
    total = max(1, int(result.report.total_estimated))
    max_tokens = max(1, int(result.report.max_context_tokens))
    # Primary efficiency metrics (budget-aligned).
    if total > max_tokens:
        budget_fill = 1.0  # over-full; exceed flagged separately
        # Credit only the share that could fit.
        useful_in_budget = int(useful * (max_tokens / total))
    else:
        budget_fill = total / max_tokens
        useful_in_budget = useful
    overhead_ratio = max(0.0, 1.0 - (useful / total))
    payload = result.report.to_event_payload()
    bill_ok = all(key in payload for key in ("estimate_version", "total_estimated", "within_budget", "blocks"))
    return StressMetrics(
        label=label,
        within_budget=bool(result.report.within_budget),
        total_estimated=int(result.report.total_estimated),
        max_context_tokens=max_tokens,
        recent_turn_count=int(result.report.recent_turn_count),
        recent_turn_limit=recent_turn_limit,
        early_marker_hit=early_marker in corpus,
        source_slice_count=len(source_slices),
        truncated_slice_count=len(result.report.truncated_slices)
        + sum(1 for item in source_slices if item.get("truncated")),
        dropped_count=len(result.report.dropped),
        silent_discard_count=silent,
        skill_instructions_present=skill_instructions,
        skill_catalog_truncated=catalog_truncated,
        tools_core_intact=core_intact,
        tools_packed_count=len(result.tools),
        useful_tokens=useful,
        useful_in_budget=useful_in_budget,
        budget_fill=budget_fill,
        overhead_ratio=overhead_ratio,
        effective_payload_ratio=useful / total,
        bill_fields_present=bill_ok,
    )


def base_ids() -> tuple[Incident, Session, Turn]:
    incident = Incident.create(title="stress", source="eval", summary="budget stress")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    turn = Turn.create(session_id=session.session_id)
    return incident, session, turn
