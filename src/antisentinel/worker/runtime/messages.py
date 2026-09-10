"""Stable, JSON-safe model message builders."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .working_set import ToolEvent, TurnRecord, WorkingSet

_HOLLOW_SUMMARIES = frozenset(
    {
        "",
        "tool calls completed",
        "done",
        "ok",
        "success",
        "succeeded",
    }
)


def build_system_message(*, skill_mode: bool = False) -> dict[str, str]:
    skill_rule = " You may load one listed skill and then read only its registered references when needed." if skill_mode else ""
    code_map_rule = (
        " Prefer code_map.find_symbols/get_node/get_neighbors to locate code; call code_map.read_source only when you need"
        " a specific chunk body. Source bodies are ephemeral pointers afterward—re-read if needed."
    )
    return {
        "role": "system",
        "content": (
            "你是 Incident 诊断运行时。所有面向用户的 summary、objective、diagnosis 和自然语言说明必须使用简体中文。"
            "Return exactly one JSON object using this schema: either "
            "{\"tasks\":[{\"task_id\":\"id\",\"objective\":\"简短中文目标\",\"tool_calls\":[{\"tool_name\":\"name\",\"arguments\":{}}]}]} "
            "or {\"final\":{\"summary\":\"简短中文摘要\",\"diagnosis\":\"中文结论\",\"confidence\":0.0,\"evidence_refs\":[]}}. "
            "Use at most 2 tool calls per turn, never repeat the same tool and arguments. "
            "After tool results, call another distinct tool only when it can resolve a remaining evidence gap; otherwise return final."
            + code_map_rule
            + skill_rule
        ),
    }


def build_task_results_message(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "role": "tool",
        "task_results": [
            {
                "task_id": item.get("task_id"),
                "status": item.get("status"),
                "summary": item.get("summary") or item.get("result_summary"),
                "evidence_refs": deepcopy(item.get("evidence_refs", [])),
            }
            for item in results
        ],
    }


def summarize_plan(tasks: list[Any], *, max_chars: int = 400) -> str:
    """Build a non-hollow plan summary from structured model tasks."""
    parts: list[str] = []
    for task in tasks:
        task_id = getattr(task, "task_id", None) or (task.get("task_id") if isinstance(task, dict) else None)
        objective = getattr(task, "objective", None) or (task.get("objective") if isinstance(task, dict) else None)
        tool_calls = getattr(task, "tool_calls", None)
        if tool_calls is None and isinstance(task, dict):
            tool_calls = task.get("tool_calls") or []
        tool_names: list[str] = []
        for call in tool_calls or []:
            name = getattr(call, "tool_name", None) or (call.get("tool_name") if isinstance(call, dict) else None)
            if name:
                tool_names.append(str(name))
        piece = f"{task_id or 'task'}:{objective or '无目标'}"
        if tool_names:
            piece += f"[{','.join(tool_names)}]"
        parts.append(piece)
    summary = "; ".join(parts) if parts else "无计划任务"
    if len(summary) > max_chars:
        return summary[: max_chars - 1] + "…"
    return summary


def summarize_tool_result(
    *,
    tool_name: str,
    status: str,
    result_summary: str | None,
    error: dict[str, Any] | None = None,
    max_chars: int = 240,
) -> str:
    """Prefer business summary; never leave a hollow placeholder as the only text."""
    raw = (result_summary or "").strip()
    if raw and raw.lower() not in _HOLLOW_SUMMARIES:
        text = f"{tool_name}:{status}:{raw}"
    elif error:
        code = error.get("code") or "error"
        message = str(error.get("message") or code)
        text = f"{tool_name}:{status}:{code}:{message}"
    else:
        text = f"{tool_name}:{status}:无详细摘要"
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def summarize_turn_outcome(tool_events: list[ToolEvent], *, plan_summary: str = "") -> str:
    if not tool_events:
        return f"计划已记录:{plan_summary}" if plan_summary else "本轮无工具调用"
    parts = [f"{event.tool_name}:{event.status}" for event in tool_events]
    detail = ";".join(parts)
    first = tool_events[0].result_summary
    if first and first.lower() not in _HOLLOW_SUMMARIES:
        return f"{detail}; {first}"[:400]
    return detail[:400]


def build_working_set_message(working_set: WorkingSet) -> dict[str, Any]:
    """Structured working-set view for the user role block (no raw payloads)."""
    return {
        "recent_turns": [
            {
                "turn_index": turn.turn_index,
                "plan_summary": turn.plan_summary,
                "outcome": turn.outcome,
                "tool_events": [
                    {
                        "tool_name": event.tool_name,
                        "args_fingerprint": event.args_fingerprint,
                        "status": event.status,
                        "result_summary": event.result_summary,
                        "evidence_refs": list(event.evidence_refs),
                    }
                    for event in turn.tool_events
                ],
            }
            for turn in working_set.recent_turns
        ],
        "older_digest": working_set.older_digest or None,
        "sticky": {
            "active_skill": working_set.sticky.active_skill,
            "source_slice_refs": deepcopy(working_set.sticky.source_slice_refs),
            "open_questions": list(working_set.sticky.open_questions),
            "active_hypotheses": list(working_set.sticky.active_hypotheses),
        },
    }


def build_tool_events_message(working_set: WorkingSet) -> dict[str, Any] | None:
    results = working_set.export_task_results()
    if not results:
        return None
    return build_task_results_message(results)


def turn_record_from_results(
    *,
    turn_index: int,
    plan_summary: str,
    tool_events: list[ToolEvent],
) -> TurnRecord:
    return TurnRecord(
        turn_index=turn_index,
        plan_summary=plan_summary,
        tool_events=tool_events,
        outcome=summarize_turn_outcome(tool_events, plan_summary=plan_summary),
    )
