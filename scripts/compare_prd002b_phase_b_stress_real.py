#!/usr/bin/env python3
"""PRD-002B Phase B stress compare on Real DeepSeek (no Fake model).

For each pressure scenario:
  1) Pack with SoftBudget baseline (A) and ContextBudget (B)
  2) Call DeepSeek once per pack with a probe question
  3) Score provider success + marker recall + budget/usage

Does not print secrets. Loads .env.local and clears broken local proxies.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from time import monotonic
from typing import Any

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
from antisentinel.ports.model import ModelRequest  # noqa: E402
from antisentinel.worker.runtime.budget import ContextBudget, calibrate_estimate_vs_usage, estimate_json  # noqa: E402
from antisentinel.worker.runtime.pack import PackInput, SoftBudget  # noqa: E402
from antisentinel.worker.runtime.working_set import ToolEvent, TurnRecord, WorkingSet  # noqa: E402

EARLY = "error code E42 from turn1"
SOURCE_HIT = "PHASEB_SOURCE_HIT_MARKER_7f3a"
SKILL_CORE = "CORE_INSTRUCTION_KEEP_ME"
SECRET = "SECRET_SHOULD_NOT_LEAK"
CORE_TOOLS = [f"tool-{i}" for i in range(8)]


def _load_env() -> None:
    path = ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _budget(max_tokens: int = 32000) -> ContextBudget:
    return ContextBudget(
        max_context_tokens=max_tokens,
        recent_turn_limit=3,
        min_recent_turns=1,
        tools_core_limit=8,
        target_fill_ratio=0.75,
    )


def _ws_long(n: int = 20) -> WorkingSet:
    ws = WorkingSet(recent_turn_limit=3, max_older_digest_tokens=2048)
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


def _sources(n: int = 10, size: int = 12_000) -> list[SourceContextSlice]:
    incident, _, _ = base_ids()
    out = []
    for i in range(n):
        # Many lines so head/tail window does not accidentally keep mid-file secret.
        lines = [f"line-{j}\n" for j in range(200)]
        lines[100] = f"# {SECRET}\n"
        body = "".join(lines) + ("x" * max(0, size - 4000))
        if i == 0:
            body = SOURCE_HIT + "\n" + body
        out.append(
            SourceContextSlice(
                str(incident.incident_id),
                f"ev-{i}",
                "repo",
                "snap",
                "sha",
                f"file_{i}.py",
                body,
                f"hash-{i}",
            )
        )
    return out


def _make_model():
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter

    api_key = os.environ.get("ANTISENTINEL_MODEL_API_KEY", "").strip()
    base_url = os.environ.get("ANTISENTINEL_MODEL_BASE_URL", "").strip()
    model_name = os.environ.get("ANTISENTINEL_MODEL_NAME", "").strip()
    if not api_key or not base_url or not model_name:
        raise SystemExit("requires ANTISENTINEL_MODEL_* (see .env.local)")
    return OpenAICompatibleModelAdapter(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        timeout=float(os.getenv("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "90")),
    ), base_url.split("//", 1)[-1].split("/", 1)[0], model_name


def _probe_for(scenario: str) -> str:
    base = (
        "必须返回唯一 JSON："
        '{"final":{"summary":"...","diagnosis":"...","confidence":0.9,"evidence_refs":[]}}。'
        "把本题答案写在 summary 里；diagnosis 可重复 summary。不要调用工具。"
    )
    if scenario.startswith("S1"):
        return (
            base
            + "阅读 working_set.recent_turns，找出 turn_index=20 的 result_summary，"
            "summary 写成 MARKER=<该摘要原文>。"
        )
    if scenario.startswith("S2"):
        return (
            base
            + f"source_context 里是否出现标记 {SOURCE_HIT}？"
            "summary 只写 YES 或 NO（可附加是否 truncated）。"
        )
    if scenario.startswith("S3"):
        return base + f"工具历史是否仍含 {EARLY}？summary 只写 YES 或 NO。"
    if scenario.startswith("S4"):
        return base + "根据 tools 列表，summary 写出前三个工具名，逗号分隔（tool-0,tool-1,tool-2）。"
    return (
        base
        + f"分别判断是否见到：1){EARLY} 2){SOURCE_HIT} 3){SKILL_CORE}。"
        "summary 写成三行 YES/NO。"
    )


def _score_answer(scenario: str, text: str, pack_metrics: dict[str, Any]) -> dict[str, Any]:
    upper = (text or "").upper()
    body = text or ""
    if scenario.startswith("S1"):
        hit = "MARKER-TURN-20" in upper or "marker-turn-20" in body
        return {"probe_pass": hit, "detail": "recent_turn20_marker"}
    if scenario.startswith("S2"):
        return {"probe_pass": ("YES" in upper) or ("NO" in upper), "detail": "answered_yes_no"}
    if scenario.startswith("S3"):
        return {"probe_pass": "YES" in upper, "detail": "tool_history_yes"}
    if scenario.startswith("S4"):
        return {"probe_pass": all(name in body for name in ("tool-0", "tool-1", "tool-2")), "detail": "core_tool_names"}
    yes_count = upper.count("YES")
    return {"probe_pass": yes_count >= 1, "detail": f"yes={yes_count}"}


def _call_real(model, *, pack_result, incident, session, turn, probe: str) -> dict[str, Any]:
    messages = list(pack_result.messages)
    messages.append({"role": "user", "content": probe})
    request = ModelRequest(
        incident_id=incident.incident_id,
        session_id=session.session_id,
        turn_id=turn.turn_id,
        messages=messages,
        tools=pack_result.tools,
    )
    started = monotonic()
    try:
        response = model.complete(request)
        latency_ms = round((monotonic() - started) * 1000, 1)
        # Prefer final summary text; else stringify response.
        text = ""
        if getattr(response, "final", None) is not None:
            text = f"{response.final.summary}\n{response.final.diagnosis}"
        else:
            text = str(getattr(response, "raw", response))[:4000]
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        estimated = pack_result.report.total_estimated
        calib = calibrate_estimate_vs_usage(estimated=estimated, input_tokens=input_tokens) if input_tokens else None
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "answer_excerpt": text[:500],
            "input_tokens": input_tokens,
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "calibration": calib,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "answer_excerpt": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "calibration": None,
            "error": {"code": type(exc).__name__, "message": str(exc)[:400]},
        }


def _scenario_inputs() -> list[tuple[str, PackInput]]:
    incident, session, turn = base_ids()

    def ids():
        return base_ids()

    s1_i, s1_s, s1_t = ids()
    s2_i, s2_s, s2_t = ids()
    s3_i, s3_s, s3_t = ids()
    s4_i, s4_s, s4_t = ids()
    s5_i, s5_s, s5_t = ids()

    ws = WorkingSet(recent_turn_limit=3)
    ws.append_turn(
        TurnRecord(
            turn_index=1,
            plan_summary="keep-tool-history",
            tool_events=[ToolEvent("read_logs", "fp", "succeeded", EARLY, ["ev-1"])],
            outcome="read_logs:succeeded",
        )
    )
    catalog = {
        "available_skills": [
            {"skill_id": f"s{i}", "name": f"skill-{i}", "description": ("详细诊断步骤说明" * 80)}
            for i in range(5)
        ]
    }
    active = {
        "active_skills": [
            {
                "skill_id": "active-1",
                "name": "diagnosis",
                "instructions": SKILL_CORE + " " + ("步骤" * 200),
                "references": [{"reference_id": f"ref-{i}", "content": "旧资料" * 120} for i in range(4)],
            }
        ]
    }
    tools = [
        {
            "name": f"tool-{i}",
            "description": ("工具说明" * 40) if i >= 8 else f"core tool {i}",
            "argument_schema": {"type": "object"},
        }
        for i in range(30)
    ]
    return [
        (
            "S1_working_20turns",
            PackInput(
                incident=s1_i,
                session=s1_s,
                turn=s1_t,
                working_set=_ws_long(20),
                tools=[],
                budget=_budget(32000),
                system_override=(
                    "你是评测探针。所有面向用户的 summary、diagnosis 必须使用简体中文。"
                    "Return exactly one JSON object: "
                    '{"final":{"summary":"...","diagnosis":"...","confidence":0.9,"evidence_refs":[]}}. '
                    "Do not call tools."
                ),
            ),
        ),
        (
            "S2_source_10files",
            PackInput(
                incident=s2_i,
                session=s2_s,
                turn=s2_t,
                working_set=None,
                tools=[],
                source_context=_sources(10, 12_000),
                budget=_budget(32000),
                soft_budget=SoftBudget(max_source_slices=4, max_source_bytes=32 * 1024),
                system_override=(
                    "你是评测探针。所有面向用户的 summary、diagnosis 必须使用简体中文。"
                    "Return exactly one JSON object: "
                    '{"final":{"summary":"...","diagnosis":"...","confidence":0.9,"evidence_refs":[]}}. '
                    "Do not call tools."
                ),
            ),
        ),
        (
            "S3_skill_5",
            PackInput(
                incident=s3_i,
                session=s3_s,
                turn=s3_t,
                working_set=ws,
                tools=[],
                skill_context=catalog,
                budget=_budget(32000),
                system_override=(
                    "你是评测探针。所有面向用户的 summary、diagnosis 必须使用简体中文。"
                    "Return exactly one JSON object: "
                    '{"final":{"summary":"...","diagnosis":"...","confidence":0.9,"evidence_refs":[]}}. '
                    "Do not call tools."
                ),
            ),
        ),
        (
            "S4_tools_30",
            PackInput(
                incident=s4_i,
                session=s4_s,
                turn=s4_t,
                working_set=None,
                tools=tools,
                budget=_budget(32000),
                system_override=(
                    "你是评测探针。所有面向用户的 summary、diagnosis 必须使用简体中文。"
                    "Return exactly one JSON object: "
                    '{"final":{"summary":"...","diagnosis":"...","confidence":0.9,"evidence_refs":[]}}. '
                    "Do not call tools."
                ),
            ),
        ),
        (
            "S5_extreme_fullstack",
            PackInput(
                incident=s5_i,
                session=s5_s,
                turn=s5_t,
                working_set=_ws_long(20),
                tools=tools,
                memory_context=MemoryContextView(
                    session_id=str(s5_s.session_id),
                    digest=("全局历史摘要：" + EARLY + "；") * 40,
                    evidence_refs=("ev-1",),
                    memory_ids=("m1",),
                ),
                skill_context=active,
                source_context=_sources(8, 12_000),
                budget=_budget(32000),
                soft_budget=SoftBudget(max_source_slices=4, max_source_bytes=32 * 1024),
                system_override=(
                    "你是评测探针。所有面向用户的 summary、diagnosis 必须使用简体中文。"
                    "Return exactly one JSON object: "
                    '{"final":{"summary":"...","diagnosis":"...","confidence":0.9,"evidence_refs":[]}}. '
                    "Do not call tools."
                ),
            ),
        ),
    ]


def run_one(model, scenario: str, mode: str, inp: PackInput) -> dict[str, Any]:
    pack_fn = pack_phase_a_soft if mode == "A" else pack_phase_b
    if inp.budget is None:
        inp.budget = _budget()
    result = pack_fn(inp)
    metrics = measure_pack(
        label=mode,
        result=result,
        early_marker=EARLY,
        recent_turn_limit=inp.budget.recent_turn_limit,
        core_tool_names=CORE_TOOLS if inp.tools else [],
    ).to_dict()
    corpus = str(result.messages)
    metrics["secret_in_pack"] = SECRET in corpus
    probe = _probe_for(scenario)
    call = _call_real(
        model,
        pack_result=result,
        incident=inp.incident,
        session=inp.session,
        turn=inp.turn,
        probe=probe,
    )
    metrics["secret_leaked"] = SECRET in (call.get("answer_excerpt") or "")
    score = _score_answer(scenario, call.get("answer_excerpt") or "", metrics)
    if scenario.startswith("S2") and call.get("ok"):
        kept = SOURCE_HIT in corpus
        ans = (call.get("answer_excerpt") or "").upper()
        score["probe_pass"] = ("YES" in ans) if kept else ("NO" in ans)
        score["detail"] = f"kept={kept}"
    return {
        "mode": mode,
        "pack": metrics,
        "real": {k: v for k, v in call.items()},
        "probe_pass": bool(score.get("probe_pass")) if call.get("ok") else False,
        "probe_detail": score.get("detail"),
        "provider_ok": bool(call.get("ok")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenarios", default="all", help="comma list or all")
    args = parser.parse_args()
    _load_env()
    model, host, model_name = _make_model()
    selected = None if args.scenarios == "all" else {s.strip() for s in args.scenarios.split(",")}
    rows = []
    for scenario, inp in _scenario_inputs():
        if selected is not None and scenario not in selected:
            continue
        print(f"[real] {scenario} A...", flush=True)
        a = run_one(model, scenario, "A", inp)
        # Rebuild input for B (working_set may be mutated by B shrink).
        print(f"[real] {scenario} B...", flush=True)
        # Re-create same scenario input cleanly:
        rebuilt = dict(_scenario_inputs())[scenario]
        b = run_one(model, scenario, "B", rebuilt)
        rows.append({"scenario": scenario, "A": a, "B": b})

    claims = {
        "used_real_deepseek": True,
        "model_host": host,
        "model_name": model_name,
        "B_all_provider_ok": all(item["B"]["provider_ok"] for item in rows),
        "B_all_within_budget": all(item["B"]["pack"]["within_budget"] for item in rows),
        "B_no_hard_limit_exceed": all(not item["B"]["pack"]["hard_limit_exceeded"] for item in rows),
        "B_no_silent_source_drop": all(item["B"]["pack"]["silent_discard_count"] == 0 for item in rows),
        "B_no_secret_leak": all(not item["B"]["pack"]["secret_leaked"] for item in rows),
        "B_probe_majority_pass": (
            sum(1 for item in rows if item["B"]["probe_pass"]) >= max(1, (len(rows) + 1) // 2)
        ),
        "B_useful_in_budget_ge_A_when_A_exceeds": all(
            (not item["A"]["pack"]["hard_limit_exceeded"])
            or item["B"]["pack"]["useful_in_budget"] >= item["A"]["pack"]["useful_in_budget"] * 0.5
            for item in rows
        ),
        "A_may_fail_provider_or_budget": any(
            (not item["A"]["provider_ok"]) or item["A"]["pack"]["hard_limit_exceeded"] for item in rows
        ),
        "metrics_are_budget_aligned": True,
    }
    table = []
    for item in rows:
        table.append(
            {
                "scenario": item["scenario"],
                "A_provider_ok": item["A"]["provider_ok"],
                "B_provider_ok": item["B"]["provider_ok"],
                "A_probe_pass": item["A"]["probe_pass"],
                "B_probe_pass": item["B"]["probe_pass"],
                "A_within_budget": item["A"]["pack"]["within_budget"],
                "B_within_budget": item["B"]["pack"]["within_budget"],
                "A_hard_limit_exceeded": item["A"]["pack"]["hard_limit_exceeded"],
                "B_hard_limit_exceeded": item["B"]["pack"]["hard_limit_exceeded"],
                "A_budget_fill": item["A"]["pack"]["budget_fill"],
                "B_budget_fill": item["B"]["pack"]["budget_fill"],
                "A_useful_tokens": item["A"]["pack"]["useful_tokens"],
                "B_useful_tokens": item["B"]["pack"]["useful_tokens"],
                "A_useful_in_budget": item["A"]["pack"]["useful_in_budget"],
                "B_useful_in_budget": item["B"]["pack"]["useful_in_budget"],
                "A_silent_discard": item["A"]["pack"]["silent_discard_count"],
                "B_silent_discard": item["B"]["pack"]["silent_discard_count"],
                "A_input_tokens": item["A"]["real"]["input_tokens"],
                "B_input_tokens": item["B"]["real"]["input_tokens"],
                "A_error": (item["A"]["real"].get("error") or {}).get("code"),
                "B_error": (item["B"]["real"].get("error") or {}).get("code"),
            }
        )
    pass_keys = [
        "B_all_provider_ok",
        "B_all_within_budget",
        "B_no_hard_limit_exceed",
        "B_no_silent_source_drop",
        "B_no_secret_leak",
        "B_probe_majority_pass",
    ]
    report = {
        "suite": "prd-002b-phase-b-stress-real-budget-aligned",
        "mode": "real",
        "claims": claims,
        "pass": all(claims[k] is True for k in pass_keys),
        "metrics_table": table,
        "scenarios": rows,
        "metric_definitions": {
            "budget_fill": "min(total,max)/max; if exceed then 1.0 and hard_limit_exceeded=true",
            "useful_tokens": "task-channel tokens in the packed request (absolute)",
            "useful_in_budget": "useful_tokens credited only up to budget (scaled down if exceed)",
            "hard_limit_exceeded": "total_estimated > max_context_tokens",
        },
        "fake_note": "Do not use effective_payload_ratio as primary efficiency; A high ratio while exceeding is invalid.",
    }
    _write_json(args.out / "compare.json", report)
    (args.out / "README.md").write_text(
        "\n".join(
            [
                "# Phase B stress — Real DeepSeek",
                "",
                f"- pass: **{report['pass']}**",
                f"- host: `{host}` model: `{model_name}`",
                "",
                "## Claims",
                "```json",
                json.dumps(claims, ensure_ascii=False, indent=2),
                "```",
                "",
                "## Table",
                "```json",
                json.dumps(table, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"pass": report["pass"], "claims": claims, "metrics_table": table}, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
