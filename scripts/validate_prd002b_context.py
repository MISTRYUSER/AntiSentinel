#!/usr/bin/env python3
"""PRD-002B Phase A context Working Set validation runner.

Records baseline / regression steps without logging secrets.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _append_step(path: Path, step: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(step, ensure_ascii=False, sort_keys=True) + "\n")


def _run_step(
    steps_path: Path,
    case_id: str,
    title: str,
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        detail = fn()
        ok = bool(detail.get("ok", True))
        error = detail.get("error")
    except Exception as exc:  # noqa: BLE001 - boundary for report
        ok = False
        detail = {}
        error = {"code": type(exc).__name__, "message": str(exc)[:500]}
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    step = {
        "case_id": case_id,
        "title": title,
        "ok": ok,
        "duration_ms": duration_ms,
        "detail": {k: v for k, v in detail.items() if k != "ok"},
        "error": error,
    }
    _append_step(steps_path, step)
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {case_id} {title} ({duration_ms} ms)")
    return step


def case_ws01() -> dict[str, Any]:
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

    incident = Incident.create(title="WS-01", source="eval", summary="multi-turn")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    registry = ToolRegistry(auto_discover=False)
    for name, summary in (
        ("read_logs", "error code E42 from turn1"),
        ("read_metrics", "cpu saturation turn2"),
        ("read_traces", "span drop turn3"),
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
        incident, session, model, registry=registry, config=RuntimeConfig(max_turns=4, recent_turn_limit=3)
    )
    fourth = str(model.requests[3].messages) if len(model.requests) >= 4 else ""
    checks = {
        "status_completed": result.status == "completed",
        "request_count": len(model.requests) == 4,
        "has_turn1_summary": "error code E42 from turn1" in fourth,
        "has_turn2_summary": "cpu saturation turn2" in fourth,
        "has_turn3_summary": "span drop turn3" in fourth,
        "no_secret": "SECRET-" not in fourth,
    }
    return {"ok": all(checks.values()), "checks": checks, "turn_count": result.turn_count}


def case_ws02() -> dict[str, Any]:
    from antisentinel.worker.runtime.working_set import ToolEvent, TurnRecord, create_empty

    ws = create_empty(recent_turn_limit=2)
    for index in range(1, 5):
        ws.append_turn(
            TurnRecord(
                turn_index=index,
                plan_summary=f"plan-{index}",
                tool_events=[
                    ToolEvent(
                        tool_name=f"tool_{index}",
                        args_fingerprint="fp",
                        status="succeeded",
                        result_summary=f"summary for turn {index}",
                        evidence_refs=[f"ev-{index}"],
                    )
                ],
                outcome=f"outcome-{index}",
            )
        )
    checks = {
        "recent_indexes": [turn.turn_index for turn in ws.recent_turns] == [3, 4],
        "compacted": ws.compacted_turns == 2,
        "digest_has_turn1": "turn=1" in ws.older_digest and "summary for turn 1" in ws.older_digest,
        "digest_has_ev1": "ev-1" in ws.older_digest,
    }
    return {"ok": all(checks.values()), "checks": checks}


def case_ws03() -> dict[str, Any]:
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.domain.turn import Turn
    from antisentinel.worker.runtime.context import ContextBuilder

    incident = Incident.create(title="redact", source="eval", summary="s")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    turn = Turn.create(session_id=session.session_id)
    request = ContextBuilder().build(
        incident,
        session,
        turn,
        prior_turns=[],
        task_results=[
            {
                "task_id": "t1",
                "status": "succeeded",
                "summary": "ok summary",
                "result": {"raw_log": "SECRET_RAW_LOG"},
                "evidence_refs": [{"evidence_id": "ev-1", "role": "supporting"}],
            }
        ],
        tools=[],
    )
    text = str(request.messages)
    checks = {
        "has_summary": "ok summary" in text,
        "has_evidence_id": "ev-1" in text,
        "no_secret": "SECRET_RAW_LOG" not in text,
    }
    return {"ok": all(checks.values()), "checks": checks}


def case_ws04() -> dict[str, Any]:
    from antisentinel.worker.runtime.messages import summarize_tool_result, summarize_turn_outcome
    from antisentinel.worker.runtime.working_set import ToolEvent

    hollow = summarize_tool_result(tool_name="read_health", status="succeeded", result_summary="tool calls completed")
    empty = summarize_tool_result(tool_name="read_health", status="succeeded", result_summary="")
    outcome = summarize_turn_outcome(
        [ToolEvent("read_health", "fp", "succeeded", "read_health:succeeded:503", [])]
    )
    checks = {
        "hollow_rewritten": "read_health" in hollow and hollow != "tool calls completed",
        "empty_fallback": "无详细摘要" in empty,
        "outcome_non_hollow": outcome != "tool calls completed" and "read_health" in outcome,
    }
    return {"ok": all(checks.values()), "checks": checks}


def case_ws05() -> dict[str, Any]:
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.ports.model import ModelError
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.checkpoint import InMemoryCheckpointStore
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    class FakeModel:
        def __init__(self, responses):
            self.responses = iter(responses)

        def complete(self, request):
            return next(self.responses)

    incident = Incident.create(title="ckpt", source="eval")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    store = InMemoryCheckpointStore()
    registry = ToolRegistry(auto_discover=False)
    registry.register(
        ToolDefinition(
            name="read_health",
            description="h",
            argument_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
            handler=lambda _: ToolExecutionResult(
                status="succeeded", result={"raw": "secret"}, result_summary="health endpoint returned 503"
            ),
        )
    )
    model = FakeModel(
        [
            {"tasks": [{"task_id": "t1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
            ModelError("process interrupted"),
        ]
    )
    RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(max_turns=3), checkpoint_store=store)
    snapshot = store.load(session.session_id)
    text = str(snapshot.working_set) if snapshot and snapshot.working_set else ""
    checks = {
        "has_snapshot": snapshot is not None,
        "has_working_set": bool(snapshot and snapshot.working_set),
        "has_summary": "503" in text,
        "no_secret": "secret" not in text,
    }
    return {"ok": all(checks.values()), "checks": checks}


def case_ws06() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.api.app import create_app
    from antisentinel.entry.application import DiagnosisApplicationService
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry

    captured: dict[str, FakeProviderModel] = {}

    def build_runtime():
        model = FakeProviderModel(
            [
                {"tasks": [{"task_id": "t1", "objective": "查日志", "tool_calls": [{"tool_name": "read_logs", "arguments": {"q": "1"}}]}]},
                {"tasks": [{"task_id": "t2", "objective": "查指标", "tool_calls": [{"tool_name": "read_metrics", "arguments": {"q": "2"}}]}]},
                {"tasks": [{"task_id": "t3", "objective": "查链路", "tool_calls": [{"tool_name": "read_traces", "arguments": {"q": "3"}}]}]},
                {"final": {"summary": "完成", "diagnosis": "根因 E42", "confidence": 0.91, "evidence_refs": []}},
            ]
        )
        captured["model"] = model
        registry = ToolRegistry(auto_discover=False)
        for name, summary in (
            ("read_logs", "error code E42 from turn1"),
            ("read_metrics", "cpu saturation turn2"),
            ("read_traces", "span drop turn3"),
        ):
            registry.register(
                ToolDefinition(
                    name=name,
                    description=name,
                    argument_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                    handler=lambda _a, summary=summary: ToolExecutionResult(
                        status="succeeded", result={"raw": f"SECRET-{summary}"}, result_summary=summary
                    ),
                )
            )
        return model, registry

    service = DiagnosisApplicationService.default_fake()
    service._runtime_builder = build_runtime  # type: ignore[attr-defined]
    client = TestClient(create_app(service))
    incident = client.post(
        "/api/incidents",
        json={"title": "WS-06 chain", "summary": "multi-turn", "source": "operator"},
    ).json()
    started = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "fake"},
    ).json()
    deadline = time.monotonic() + 5
    result = None
    while time.monotonic() < deadline:
        result = client.get(f"/api/sessions/{started['session_id']}").json()
        if result.get("status") != "running":
            break
        time.sleep(0.01)
    assert result is not None
    model = captured["model"]
    fourth = str(model.requests[3].messages) if len(model.requests) >= 4 else ""
    checks = {
        "session_completed": result.get("status") == "completed",
        "diagnosis": (result.get("final") or {}).get("diagnosis") == "根因 E42",
        "four_requests": len(model.requests) == 4,
        "has_turn1": "error code E42 from turn1" in fourth,
        "no_secret_context": "SECRET-" not in fourth,
        "no_secret_api": "SECRET-" not in json.dumps(result, ensure_ascii=False),
    }
    return {"ok": all(checks.values()), "checks": checks, "turn_count": result.get("turn_count")}


def case_ws07() -> dict[str, Any]:
    from antisentinel.evaluation.skillsbench_session import make_benchmark_context_builder
    from antisentinel.worker.runtime.context import ContextBuilder
    import antisentinel.evaluation.skillsbench_session as bench_mod

    prod = ContextBuilder()
    bench = make_benchmark_context_builder()
    checks = {
        "same_build_impl": prod.build.__func__ is bench.build.__func__,
        "no_legacy_class": not hasattr(bench_mod, "BenchmarkContextBuilder"),
        "bench_override": bench.system_override is not None and bench.benchmark_user_prompt is True,
    }
    return {"ok": all(checks.values()), "checks": checks}


def case_reg01(output: Path) -> dict[str, Any]:
    pytest_txt = output / "pytest.txt"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_working_set.py",
        "tests/test_runtime_context.py",
        "tests/test_runtime_loop.py",
        "tests/test_checkpoint.py",
        "tests/test_integration_smoke.py",
        "tests/test_memory_recall.py",
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Isolate gate from a developer's real .env.local so provider defaults stay deterministic.
    for name in (
        "ANTISENTINEL_MODEL_MODE",
        "ANTISENTINEL_MODEL_BASE_URL",
        "ANTISENTINEL_MODEL_NAME",
        "ANTISENTINEL_MODEL_API_KEY",
        "ANTISENTINEL_MODEL_TIMEOUT_SECONDS",
    ):
        env.pop(name, None)
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    pytest_txt.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "pytest_tail": (proc.stdout or "").strip().splitlines()[-3:],
        "artifact": str(pytest_txt.relative_to(output)) if pytest_txt.is_relative_to(output) else str(pytest_txt),
    }


def case_real01() -> dict[str, Any]:
    mode = os.getenv("ANTISENTINEL_MODEL_MODE", "").strip().lower()
    api_key = os.getenv("ANTISENTINEL_MODEL_API_KEY", "").strip()
    base_url = os.getenv("ANTISENTINEL_MODEL_BASE_URL", "").strip()
    model_name = os.getenv("ANTISENTINEL_MODEL_NAME", "").strip()
    if mode != "real" or not api_key or not base_url or not model_name:
        return {
            "ok": True,
            "skipped": True,
            "reason": "real mode or credentials not configured",
            "configured_mode": mode or None,
            "has_key": bool(api_key),
            "model_name": model_name or None,
        }

    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    model = OpenAICompatibleModelAdapter(
        base_url=base_url,
        api_key=api_key,
        model=model_name,
        timeout=float(os.getenv("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "60")),
    )
    registry = ToolRegistry(auto_discover=False)
    registry.register(
        ToolDefinition(
            name="read_health",
            description="Read mock service health",
            argument_schema={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
                "additionalProperties": False,
            },
            handler=lambda arguments: ToolExecutionResult(
                status="succeeded",
                result={"status": "degraded", "service": arguments.get("service")},
                result_summary=f"service {arguments.get('service')} returned degraded",
            ),
        )
    )
    incident = Incident.create(
        title="REAL-01 DeepSeek smoke",
        source="eval",
        summary="请先调用 read_health 检查 service=api，再根据工具摘要给出 final 诊断。不要编造未返回的原文。",
    )
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    try:
        result = RuntimeEngine().run(
            incident,
            session,
            model,
            registry=registry,
            config=RuntimeConfig(max_turns=4, max_total_tool_calls=4),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "skipped": False,
            "status": "exception",
            "error_code": type(exc).__name__,
            "error_message": str(exc)[:300],
            "model_name": model_name,
            "base_url_host": base_url.split("://", 1)[-1].split("/", 1)[0],
        }
    return {
        "ok": result.status == "completed",
        "skipped": False,
        "status": result.status,
        "turn_count": result.turn_count,
        "error_code": (result.error or {}).get("code") if result.error else None,
        "error_message": ((result.error or {}).get("message") or "")[:300] if result.error else None,
        "has_final": result.final is not None,
        "task_summary_count": len(result.task_summaries),
        "input_tokens": result.token_usage.input_tokens,
        "output_tokens": result.token_usage.output_tokens,
        "model_name": model_name,
        "base_url_host": base_url.split("://", 1)[-1].split("/", 1)[0],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PRD-002B Phase A Working Set continuity")
    parser.add_argument("--output", type=Path, required=True, help="empty output directory for this run")
    parser.add_argument("--real", action="store_true", help="also run REAL-01 against configured provider")
    args = parser.parse_args()
    output: Path = args.output
    if output.exists():
        raise SystemExit(f"output directory must not already exist: {output}")
    output.mkdir(parents=True)
    steps_path = output / "steps.jsonl"
    steps_path.write_text("", encoding="utf-8")

    steps: list[dict[str, Any]] = []
    steps.append(_run_step(steps_path, "WS-01", "engine multi-turn retains turn1 summary", case_ws01))
    steps.append(_run_step(steps_path, "WS-02", "working set folds older turns into digest", case_ws02))
    steps.append(_run_step(steps_path, "WS-03", "context redacts raw tool payloads", case_ws03))
    steps.append(_run_step(steps_path, "WS-04", "summaries are non-hollow", case_ws04))
    steps.append(_run_step(steps_path, "WS-05", "checkpoint persists working_set", case_ws05))
    steps.append(_run_step(steps_path, "WS-06", "HTTP full-chain multi-turn continuity", case_ws06))
    steps.append(_run_step(steps_path, "WS-07", "production and SkillsBench share pack", case_ws07))
    steps.append(_run_step(steps_path, "REG-01", "pytest gate for context continuity", lambda: case_reg01(output)))
    if args.real:
        steps.append(_run_step(steps_path, "REAL-01", "optional real DeepSeek smoke", case_real01))
    else:
        step = {
            "case_id": "REAL-01",
            "title": "optional real DeepSeek smoke",
            "ok": True,
            "duration_ms": 0,
            "detail": {"skipped": True, "reason": "pass --real to enable"},
            "error": None,
        }
        _append_step(steps_path, step)
        steps.append(step)
        print("[SKIP] REAL-01 optional real DeepSeek smoke")

    required = [s for s in steps if s["case_id"] != "REAL-01" or not s.get("detail", {}).get("skipped")]
    # REAL-01 skipped still ok; required excludes skipped real
    scored = [s for s in steps if not (s["case_id"] == "REAL-01" and s.get("detail", {}).get("skipped"))]
    passed = sum(1 for s in scored if s["ok"])
    failed = sum(1 for s in scored if not s["ok"])
    report = {
        "suite": "prd-002b-phase-a",
        "phase": "A",
        "purpose": "baseline_or_regression",
        "cases_total_scored": len(scored),
        "passed": passed,
        "failed": failed,
        "case_pass": failed == 0,
        "steps": steps,
        "catalog": str((ROOT / "docs/validation/prd-002b-phase-a/cases.md").relative_to(ROOT)),
    }
    _write_json(output / "report.json", report)
    lines = [
        "# PRD-002B Phase A Validation Report",
        "",
        f"- case_pass: **{report['case_pass']}**",
        f"- scored: {passed}/{len(scored)} passed, {failed} failed",
        f"- catalog: `{report['catalog']}`",
        "",
        "| Case | OK | ms | Notes |",
        "|------|----|----|-------|",
    ]
    for step in steps:
        note = ""
        detail = step.get("detail") or {}
        if detail.get("skipped"):
            note = f"skipped: {detail.get('reason')}"
        elif step.get("error"):
            note = str(step["error"])[:120]
        elif "checks" in detail:
            note = ",".join(k for k, v in detail["checks"].items() if not v) or "all checks ok"
        lines.append(
            f"| {step['case_id']} | {step['ok']} | {step['duration_ms']} | {note} |"
        )
    lines.append("")
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written to {output / 'report.json'} case_pass={report['case_pass']}")
    return 0 if report["case_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
