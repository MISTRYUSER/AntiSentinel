#!/usr/bin/env python3
"""Phase A OFF vs ON comparison for Working Set continuity.

OFF = enable_working_set=False (legacy last-turn-only + hollow turn summary)
ON  = enable_working_set=True  (Phase A Working Set)

Writes a comparison report; does not call a real LLM.
"""

from __future__ import annotations

import argparse
import json
from math import ceil
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TURN1 = "error code E42 from turn1"
TURN2 = "cpu saturation turn2"
TURN3 = "span drop turn3"
MARKERS = (TURN1, TURN2, TURN3)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _estimate_tokens(text: str) -> int:
    return ceil(len(text.encode("utf-8")) / 3) if text else 0


def _run_multiturn(*, enable_working_set: bool) -> dict[str, Any]:
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    class FakeModel:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return next(self.responses)

    incident = Incident.create(title="CMP-A multi-turn", source="eval", summary="compare working set")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    registry = ToolRegistry(auto_discover=False)
    for name, summary in (
        ("read_logs", TURN1),
        ("read_metrics", TURN2),
        ("read_traces", TURN3),
    ):
        registry.register(
            ToolDefinition(
                name=name,
                description=name,
                argument_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                handler=lambda _a, summary=summary: ToolExecutionResult(
                    status="succeeded",
                    result={"raw": f"SECRET-{summary}"},
                    result_summary=summary,
                ),
            )
        )
    model = FakeModel(
        [
            {"tasks": [{"task_id": "t1", "objective": "logs", "tool_calls": [{"tool_name": "read_logs", "arguments": {"q": "1"}}]}]},
            {"tasks": [{"task_id": "t2", "objective": "metrics", "tool_calls": [{"tool_name": "read_metrics", "arguments": {"q": "2"}}]}]},
            {"tasks": [{"task_id": "t3", "objective": "traces", "tool_calls": [{"tool_name": "read_traces", "arguments": {"q": "3"}}]}]},
            {"final": {"summary": "done", "diagnosis": "E42 root cause", "confidence": 0.9, "evidence_refs": []}},
        ]
    )
    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry,
        config=RuntimeConfig(max_turns=4, recent_turn_limit=3, enable_working_set=enable_working_set),
    )
    fourth = str(model.requests[3].messages) if len(model.requests) >= 4 else ""
    prior_summaries = []
    for request in model.requests:
        for message in request.messages:
            if message.get("role") != "user":
                continue
            for prior in message.get("prior_turns") or []:
                prior_summaries.append(str(prior.get("summary") or ""))

    hollow_prior = sum(1 for item in prior_summaries if item.strip() == "tool calls completed")
    non_hollow_prior = sum(
        1 for item in prior_summaries if item.strip() and item.strip() != "tool calls completed"
    )
    marker_hits = {marker: marker in fourth for marker in MARKERS}
    coverage = sum(1 for hit in marker_hits.values() if hit) / len(MARKERS)
    return {
        "enable_working_set": enable_working_set,
        "status": result.status,
        "request_count": len(model.requests),
        "turn_count": result.turn_count,
        "final_request_has_turn1": marker_hits[TURN1],
        "final_request_has_turn2": marker_hits[TURN2],
        "final_request_has_turn3": marker_hits[TURN3],
        "early_summary_coverage": round(coverage, 4),
        "hollow_prior_turn_summaries": hollow_prior,
        "non_hollow_prior_turn_summaries": non_hollow_prior,
        "prior_turn_summary_count": len(prior_summaries),
        "secret_leaked": "SECRET-" in fourth,
        "final_request_estimated_tokens": _estimate_tokens(fourth),
        "has_working_set_block": any(
            "working_set" in message for message in (model.requests[3].messages if len(model.requests) >= 4 else [])
        ),
        "ws01_pass": all(
            [
                result.status == "completed",
                len(model.requests) == 4,
                marker_hits[TURN1],
                marker_hits[TURN2],
                marker_hits[TURN3],
                "SECRET-" not in fourth,
            ]
        ),
    }


def _run_sparsity_fixture(*, enable_working_set: bool) -> dict[str, Any]:
    """Two-turn fixture focused on hollow prior_turns text."""
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    class FakeModel:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.requests = []

        def complete(self, request):
            self.requests.append(request)
            return next(self.responses)

    incident = Incident.create(title="CMP-A sparse", source="eval")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    registry = ToolRegistry(auto_discover=False)
    registry.register(
        ToolDefinition(
            name="read_health",
            description="h",
            argument_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
            handler=lambda _: ToolExecutionResult(
                status="succeeded", result={"raw": "SECRET"}, result_summary="health endpoint returned 503"
            ),
        )
    )
    model = FakeModel(
        [
            {"tasks": [{"task_id": "t1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
            {"final": {"summary": "done", "diagnosis": "degraded", "confidence": 0.8, "evidence_refs": []}},
        ]
    )
    RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry,
        config=RuntimeConfig(max_turns=2, enable_working_set=enable_working_set),
    )
    second = model.requests[1]
    prior = []
    for message in second.messages:
        if message.get("role") == "user":
            prior.extend(message.get("prior_turns") or [])
    summaries = [str(item.get("summary") or "") for item in prior]
    text = str(second.messages)
    return {
        "enable_working_set": enable_working_set,
        "prior_summaries": summaries,
        "hollow_only_priors": bool(summaries) and all(item == "tool calls completed" for item in summaries),
        "has_plan_or_tool_detail": ("read_health" in text) or ("inspect" in text) or ("503" in text),
        "has_working_set_block": any("working_set" in message for message in second.messages),
    }


def _delta(off: Any, on: Any) -> Any:
    if isinstance(off, bool) and isinstance(on, bool):
        if off == on:
            return "same"
        return "improved" if on and not off else "regressed"
    if isinstance(off, (int, float)) and isinstance(on, (int, float)):
        return round(on - off, 4)
    return {"off": off, "on": on}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Phase A Working Set OFF vs ON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: Path = args.output
    if output.exists():
        raise SystemExit(f"output directory must not already exist: {output}")
    output.mkdir(parents=True)

    off_multi = _run_multiturn(enable_working_set=False)
    on_multi = _run_multiturn(enable_working_set=True)
    off_sparse = _run_sparsity_fixture(enable_working_set=False)
    on_sparse = _run_sparsity_fixture(enable_working_set=True)

    metrics = [
        {
            "metric": "ws01_pass (turn4 still has turn1/2/3 summaries, no secret)",
            "off": off_multi["ws01_pass"],
            "on": on_multi["ws01_pass"],
            "delta": _delta(off_multi["ws01_pass"], on_multi["ws01_pass"]),
            "better_when": "ON true / OFF false",
        },
        {
            "metric": "early_summary_coverage (fraction of turn1..3 markers in final request)",
            "off": off_multi["early_summary_coverage"],
            "on": on_multi["early_summary_coverage"],
            "delta": _delta(off_multi["early_summary_coverage"], on_multi["early_summary_coverage"]),
            "better_when": "higher",
        },
        {
            "metric": "final_request_has_turn1",
            "off": off_multi["final_request_has_turn1"],
            "on": on_multi["final_request_has_turn1"],
            "delta": _delta(off_multi["final_request_has_turn1"], on_multi["final_request_has_turn1"]),
            "better_when": "ON true",
        },
        {
            "metric": "hollow_prior_turn_summaries (count of 'tool calls completed')",
            "off": off_multi["hollow_prior_turn_summaries"],
            "on": on_multi["hollow_prior_turn_summaries"],
            "delta": _delta(off_multi["hollow_prior_turn_summaries"], on_multi["hollow_prior_turn_summaries"]),
            "better_when": "lower",
        },
        {
            "metric": "non_hollow_prior_turn_summaries",
            "off": off_multi["non_hollow_prior_turn_summaries"],
            "on": on_multi["non_hollow_prior_turn_summaries"],
            "delta": _delta(off_multi["non_hollow_prior_turn_summaries"], on_multi["non_hollow_prior_turn_summaries"]),
            "better_when": "higher",
        },
        {
            "metric": "sparsity_fixture hollow_only_priors",
            "off": off_sparse["hollow_only_priors"],
            "on": on_sparse["hollow_only_priors"],
            "delta": "improved" if off_sparse["hollow_only_priors"] and not on_sparse["hollow_only_priors"] else _delta(off_sparse["hollow_only_priors"], on_sparse["hollow_only_priors"]),
            "better_when": "ON false",
        },
        {
            "metric": "sparsity_fixture has_plan_or_tool_detail",
            "off": off_sparse["has_plan_or_tool_detail"],
            "on": on_sparse["has_plan_or_tool_detail"],
            "delta": _delta(off_sparse["has_plan_or_tool_detail"], on_sparse["has_plan_or_tool_detail"]),
            "better_when": "ON true",
        },
        {
            "metric": "final_request_estimated_tokens (utf8/3 heuristic; continuity may cost tokens)",
            "off": off_multi["final_request_estimated_tokens"],
            "on": on_multi["final_request_estimated_tokens"],
            "delta": _delta(off_multi["final_request_estimated_tokens"], on_multi["final_request_estimated_tokens"]),
            "better_when": "informational for Phase A (often ON >= OFF)",
        },
        {
            "metric": "secret_leaked",
            "off": off_multi["secret_leaked"],
            "on": on_multi["secret_leaked"],
            "delta": _delta(off_multi["secret_leaked"], on_multi["secret_leaked"]),
            "better_when": "both false",
        },
    ]

    improved = [
        row
        for row in metrics
        if row["delta"] == "improved"
        or (
            isinstance(row["delta"], (int, float))
            and (
                (row["better_when"] == "higher" and row["delta"] > 0)
                or (row["better_when"] == "lower" and row["delta"] < 0)
            )
        )
        or (row["metric"].startswith("ws01") and row["delta"] == "improved")
        or (row["better_when"] == "ON true" and row["on"] is True and row["off"] is False)
        or (row["better_when"] == "ON false" and row["on"] is False and row["off"] is True)
    ]

    report = {
        "suite": "prd-002b-phase-a-compare",
        "off_means": "enable_working_set=False (legacy last-turn-only)",
        "on_means": "enable_working_set=True (Phase A Working Set)",
        "claims": {
            "continuity_fixed": on_multi["ws01_pass"] and not off_multi["ws01_pass"],
            "history_less_hollow": off_sparse["hollow_only_priors"] and not on_sparse["hollow_only_priors"],
            "token_saved": False,
            "token_saved_note": "Phase A trades tokens for continuity; savings are Phase B/C goals",
            "hallucination_reduced": None,
            "hallucination_note": "Not measured in this Fake compare; needs annotated Real set",
        },
        "metrics": metrics,
        "improved_metric_count": len(improved),
        "off_multiturn": off_multi,
        "on_multiturn": on_multi,
        "off_sparsity": off_sparse,
        "on_sparsity": on_sparse,
    }
    _write_json(output / "compare.json", report)

    lines = [
        "# PRD-002B Phase A OFF vs ON Compare",
        "",
        "- OFF: `enable_working_set=False`（改造前：只留上一轮 + 空洞 turn summary）",
        "- ON: `enable_working_set=True`（Phase A Working Set）",
        "",
        "## 结论（可宣称）",
        "",
        f"- 连续性修复（WS-01 契约）: **{report['claims']['continuity_fixed']}** "
        f"(OFF ws01_pass={off_multi['ws01_pass']}, ON={on_multi['ws01_pass']})",
        f"- 历史更不空洞: **{report['claims']['history_less_hollow']}** "
        f"(OFF hollow_only={off_sparse['hollow_only_priors']}, ON={on_sparse['hollow_only_priors']})",
        f"- Token 节约: **{report['claims']['token_saved']}** — {report['claims']['token_saved_note']}",
        f"- 幻觉减少: **未测** — {report['claims']['hallucination_note']}",
        "",
        "## 指标表",
        "",
        "| Metric | OFF | ON | Δ |",
        "|--------|-----|----|---|",
    ]
    for row in metrics:
        lines.append(f"| {row['metric']} | {row['off']} | {row['on']} | {row['delta']} |")
    lines.extend(
        [
            "",
            f"最终请求估算 token：OFF={off_multi['final_request_estimated_tokens']}, "
            f"ON={on_multi['final_request_estimated_tokens']} "
            f"(Δ={on_multi['final_request_estimated_tokens'] - off_multi['final_request_estimated_tokens']})",
            "",
        ]
    )
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nreport: {output / 'compare.json'}")
    return 0 if report["claims"]["continuity_fixed"] and report["claims"]["history_less_hollow"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
