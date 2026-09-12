#!/usr/bin/env python3
"""PRD-002B Phase B stress compare: SoftBudget baseline vs ContextBudget.

Scenarios (pressure, not average):
  S1 20-turn working fidelity
  S2 10-file source density / silent discard
  S3 5-skill late degradation
  S4 30-tool core floor
  S5 full-stack extreme (hard limit + effective payload)

Groups:
  B     = Phase B ContextBudget (experimental)
  A     = Phase A SoftBudget replay (control)
  A_rep = SoftBudget replay again (negative control / stability)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from antisentinel.code_map.source_context import SourceContextSlice  # noqa: E402
from antisentinel.evaluation.prd002b_phase_b_stress import (  # noqa: E402
    base_ids,
    measure_pack,
    pack_phase_a_soft,
    pack_phase_b,
)
from antisentinel.memory.models import MemoryContextView  # noqa: E402
from antisentinel.worker.runtime.budget import ContextBudget  # noqa: E402
from antisentinel.worker.runtime.pack import PackInput, SoftBudget  # noqa: E402
from antisentinel.worker.runtime.working_set import ToolEvent, TurnRecord, WorkingSet  # noqa: E402

EARLY = "error code E42 from turn1"
SECRET = "SECRET_SHOULD_NOT_LEAK"
CORE_TOOLS = [f"tool-{i}" for i in range(8)]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _budget(max_tokens: int = 8192) -> ContextBudget:
    return ContextBudget(max_context_tokens=max_tokens, recent_turn_limit=3, min_recent_turns=1, tools_core_limit=8)


def _ws_long(n: int = 20) -> WorkingSet:
    ws = WorkingSet(recent_turn_limit=3, max_older_digest_tokens=512)
    for index in range(1, n + 1):
        marker = EARLY if index == 1 else f"marker-turn-{index}"
        ws.append_turn(
            TurnRecord(
                turn_index=index,
                plan_summary=f"t{index}:inspect[{marker}]",
                tool_events=[ToolEvent(f"read_{index}", f"fp{index}", "succeeded", marker, [f"ev-{index}"])],
                outcome=f"read_{index}:succeeded",
            )
        )
    return ws


def _sources(n: int = 10, size: int = 20_000) -> list[SourceContextSlice]:
    incident, _, _ = base_ids()
    return [
        SourceContextSlice(
            str(incident.incident_id),
            f"ev-{i}",
            "repo",
            "snap",
            "sha",
            f"file_{i}.py",
            ("# " + SECRET + "\n") + ("x" * size) + f"\n# hit-line-{i}\n",
            f"hash-{i}",
        )
        for i in range(n)
    ]


def _skills() -> dict[str, Any]:
    catalog = {
        "available_skills": [
            {"skill_id": f"s{i}", "name": f"skill-{i}", "description": ("详细诊断步骤说明" * 120)}
            for i in range(5)
        ]
    }
    # Also include an active skill payload for late-degradation stage 2/3 pressure in extreme.
    return catalog


def _active_skills() -> dict[str, Any]:
    return {
        "active_skills": [
            {
                "skill_id": "active-1",
                "name": "diagnosis",
                "instructions": "CORE_INSTRUCTION_KEEP_ME " + ("步骤" * 400),
                "references": [
                    {"reference_id": f"ref-{i}", "content": "旧资料" * 200}
                    for i in range(5)
                ],
            }
        ]
    }


def _tools(n: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "name": f"tool-{i}",
            "description": ("工具说明" * 80) if i >= 8 else f"core tool {i}",
            "argument_schema": {"type": "object"},
        }
        for i in range(n)
    ]


def _pack(mode: str, inp: PackInput):
    if mode.startswith("A"):
        return pack_phase_a_soft(inp)
    return pack_phase_b(inp)


def run_scenario(name: str, builder: Callable[[], PackInput], modes: tuple[str, ...] = ("B", "A", "A_rep")) -> dict[str, Any]:
    rows = {}
    for mode in modes:
        inp = builder()
        # Ensure budget object present for both paths (A uses it only for max reporting).
        if inp.budget is None:
            inp.budget = _budget()
        result = _pack(mode, inp)
        metrics = measure_pack(
            label=mode,
            result=result,
            early_marker=EARLY,
            recent_turn_limit=inp.budget.recent_turn_limit if inp.budget else 3,
            core_tool_names=CORE_TOOLS if inp.tools else [],
        )
        rows[mode] = metrics.to_dict()
        rows[mode]["secret_leaked"] = SECRET in str(result.messages)
    return {"scenario": name, "groups": rows}


def scenario_s1_working() -> PackInput:
    incident, session, turn = base_ids()
    return PackInput(
        incident=incident,
        session=session,
        turn=turn,
        working_set=_ws_long(20),
        tools=[],
        budget=_budget(8192),
        system_override="sys",
    )


def scenario_s1_working_tight() -> PackInput:
    """Same 20-turn WS under tighter global budget to stress working block."""
    incident, session, turn = base_ids()
    return PackInput(
        incident=incident,
        session=session,
        turn=turn,
        working_set=_ws_long(20),
        tools=[],
        budget=_budget(2048),
        system_override="sys",
    )


def scenario_s2_source() -> PackInput:
    incident, session, turn = base_ids()
    return PackInput(
        incident=incident,
        session=session,
        turn=turn,
        working_set=None,
        tools=[],
        source_context=_sources(10, 20_000),
        budget=_budget(8192),
        soft_budget=SoftBudget(max_source_slices=4, max_source_bytes=32 * 1024),
        system_override="sys",
    )


def scenario_s3_skill() -> PackInput:
    incident, session, turn = base_ids()
    # Catalog pressure for late degradation step 1; also keep a tiny working marker.
    ws = WorkingSet(recent_turn_limit=3)
    ws.append_turn(
        TurnRecord(
            turn_index=1,
            plan_summary="keep-tool-history",
            tool_events=[ToolEvent("read_logs", "fp", "succeeded", EARLY, ["ev-1"])],
            outcome="read_logs:succeeded",
        )
    )
    return PackInput(
        incident=incident,
        session=session,
        turn=turn,
        working_set=ws,
        tools=[],
        skill_context=_skills(),
        budget=_budget(8192),
        system_override="sys",
    )


def scenario_s4_tools() -> PackInput:
    incident, session, turn = base_ids()
    return PackInput(
        incident=incident,
        session=session,
        turn=turn,
        working_set=None,
        tools=_tools(30),
        budget=_budget(8192),
        system_override="sys",
    )


def scenario_s5_extreme() -> PackInput:
    incident, session, turn = base_ids()
    return PackInput(
        incident=incident,
        session=session,
        turn=turn,
        working_set=_ws_long(20),
        tools=_tools(30),
        memory_context=MemoryContextView(
            session_id=str(session.session_id),
            digest=("全局历史摘要：" + EARLY + "；") * 80,
            evidence_refs=("ev-1", "ev-2"),
            memory_ids=("m1",),
        ),
        skill_context=_active_skills(),
        source_context=_sources(10, 20_000),
        budget=_budget(8192),
        soft_budget=SoftBudget(max_source_slices=4, max_source_bytes=32 * 1024),
        system_override="sys",
    )


def _delta(b: dict[str, Any], a: dict[str, Any], key: str) -> Any:
    if key not in b or key not in a:
        return None
    if isinstance(b[key], (int, float)) and isinstance(a[key], (int, float)):
        return round(b[key] - a[key], 4)
    if b[key] == a[key]:
        return "same"
    return "changed"


def synthesize(results: list[dict[str, Any]]) -> dict[str, Any]:
    claims = {
        "token_not_blown_on_B": True,
        "short_term_memory_protected_on_B": True,
        "less_silent_source_drop_on_B": True,
        "skill_late_degrade_or_core_kept_on_B": True,
        "tools_core_floor_on_B": True,
        "bill_observability_on_B": True,
        "baseline_stable_A_vs_A_rep": True,
        "ai_task_quality_real_llm": None,
        "ai_task_quality_note": "Fake stress cannot score diagnosis quality; needs annotated Real DeepSeek set",
    }
    metrics_table = []
    for item in results:
        b = item["groups"]["B"]
        a = item["groups"]["A"]
        a_rep = item["groups"]["A_rep"]
        if b.get("hard_limit_exceeded") or not b.get("within_budget"):
            claims["token_not_blown_on_B"] = False
        if item["scenario"].startswith("S1_working"):
            # Primary KPI from plan: recent_turns completion rate under long history.
            if float(b.get("recent_turn_completion_rate") or 0) < 1.0 or int(b.get("recent_turn_count") or 0) < 1:
                claims["short_term_memory_protected_on_B"] = False
            if item["scenario"] == "S1_working_20turns_tight2048" and int(b.get("recent_turn_count") or 0) < 1:
                claims["short_term_memory_protected_on_B"] = False
        if item["scenario"] == "S2_source_10files":
            if b.get("silent_discard_count", 0) > 0:
                claims["less_silent_source_drop_on_B"] = False
            if a.get("silent_discard_count", 0) == 0:
                # Control must actually exhibit the Phase A failure mode.
                claims["less_silent_source_drop_on_B"] = False
        if item["scenario"] == "S3_skill_5":
            if not (b.get("early_marker_hit") and (b.get("skill_catalog_truncated") or b.get("within_budget"))):
                claims["skill_late_degrade_or_core_kept_on_B"] = False
        if item["scenario"] == "S4_tools_30":
            if not b.get("tools_core_intact"):
                claims["tools_core_floor_on_B"] = False
        if not b.get("bill_fields_present"):
            claims["bill_observability_on_B"] = False
        if a.get("total_estimated") != a_rep.get("total_estimated"):
            claims["baseline_stable_A_vs_A_rep"] = False
        metrics_table.append(
            {
                "scenario": item["scenario"],
                "B_within_budget": b.get("within_budget"),
                "A_within_budget": a.get("within_budget"),
                "B_hard_limit_exceeded": b.get("hard_limit_exceeded"),
                "A_hard_limit_exceeded": a.get("hard_limit_exceeded"),
                "B_recent_turn_completion_rate": b.get("recent_turn_completion_rate"),
                "A_recent_turn_completion_rate": a.get("recent_turn_completion_rate"),
                "B_early_marker_hit": b.get("early_marker_hit"),
                "A_early_marker_hit": a.get("early_marker_hit"),
                "B_silent_discard": b.get("silent_discard_count"),
                "A_silent_discard": a.get("silent_discard_count"),
                "B_truncated_slices": b.get("truncated_slice_count"),
                "A_truncated_slices": a.get("truncated_slice_count"),
                "B_effective_payload_ratio": b.get("effective_payload_ratio"),
                "A_effective_payload_ratio": a.get("effective_payload_ratio"),
                "delta_effective_payload_ratio": _delta(b, a, "effective_payload_ratio"),
                "delta_total_estimated": _delta(b, a, "total_estimated"),
                "B_tools_core_intact": b.get("tools_core_intact"),
                "A_tools_core_intact": a.get("tools_core_intact"),
                "A_vs_A_rep_stable": a.get("total_estimated") == a_rep.get("total_estimated"),
            }
        )
    s5 = next(item for item in results if item["scenario"] == "S5_extreme_fullstack")
    claims["extreme_B_within_A_may_blow"] = bool(s5["groups"]["B"]["within_budget"]) and (
        (not s5["groups"]["A"]["within_budget"]) or s5["groups"]["A"]["hard_limit_exceeded"]
    )
    pass_keys = [k for k, v in claims.items() if k not in {"ai_task_quality_real_llm", "ai_task_quality_note"}]
    return {
        "suite": "prd-002b-phase-b-stress-compare",
        "claims": claims,
        "pass": all(claims[k] is True for k in pass_keys),
        "metrics_table": metrics_table,
        "scenarios": results,
        "memory_note": (
            "Memory channel = long-term fact view (session digest + evidence_refs). "
            "Short-term continuity = Working Set recent_turns + older_digest."
        ),
        "source_edit_note": (
            "If a source slice is dropped, model cannot see that file body this turn; "
            "Phase B prefers window+truncated marker over Phase A silent break so the model "
            "knows content is incomplete and can re-read via tools/evidence_refs."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    results = [
        run_scenario("S1_working_20turns", scenario_s1_working),
        run_scenario("S1_working_20turns_tight2048", scenario_s1_working_tight),
        run_scenario("S2_source_10files", scenario_s2_source),
        run_scenario("S3_skill_5", scenario_s3_skill),
        run_scenario("S4_tools_30", scenario_s4_tools),
        run_scenario("S5_extreme_fullstack", scenario_s5_extreme),
    ]
    report = synthesize(results)
    _write_json(args.out / "compare.json", report)
    _write_json(
        args.out / "README.md".replace(".md", ".meta.json") if False else args.out / "summary.json",
        {"pass": report["pass"], "claims": report["claims"], "metrics_table": report["metrics_table"]},
    )
    readme = args.out / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# PRD-002B Phase B stress compare",
                "",
                f"- pass: **{report['pass']}**",
                "- groups: B=ContextBudget, A=SoftBudget baseline, A_rep=negative control",
                "- Fake model / pack-level; Real DeepSeek task quality is separate",
                "",
                "## Claims",
                "```json",
                json.dumps(report["claims"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"pass": report["pass"], "claims": report["claims"]}, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
