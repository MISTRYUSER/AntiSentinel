"""Stable, JSON-safe model message builders."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_system_message(*, skill_mode: bool = False) -> dict[str, str]:
    skill_rule = " You may load one listed skill and then read only its registered references when needed." if skill_mode else ""
    return {
        "role": "system",
        "content": "你是 Incident 诊断运行时。所有面向用户的 summary、objective、diagnosis 和自然语言说明必须使用简体中文。Return exactly one JSON object using this schema: either {\"tasks\":[{\"task_id\":\"id\",\"objective\":\"简短中文目标\",\"tool_calls\":[{\"tool_name\":\"name\",\"arguments\":{}}]}]} or {\"final\":{\"summary\":\"简短中文摘要\",\"diagnosis\":\"中文结论\",\"confidence\":0.0,\"evidence_refs\":[]}}. Use at most 2 tool calls per turn, never repeat the same tool and arguments. After tool results, call another distinct tool only when it can resolve a remaining evidence gap; otherwise return final." + skill_rule,
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
