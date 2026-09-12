#!/usr/bin/env python3
"""Calibrate estimate_model_request (wire) vs provider usage.input_tokens.

Fixes prior methodology bugs:
  - compared structured-dict estimate to averaged multi-turn usage
  - ignored adapter wire wrapping ([AntiSentinel context] + json.dumps)

Does not change the utf8_ceil_div3_v1 formula; uses wire-shaped payloads for totals.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from antisentinel.worker.runtime.budget import (  # noqa: E402
    DEFAULT_WIRE_SAFETY_SCALE,
    ESTIMATE_VERSION,
    calibrate_estimate_vs_usage,
    estimate_json,
    estimate_model_request,
)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


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


def run_fake_calibration() -> list[dict[str, Any]]:
    messages = [
        {"role": "system", "content": "你是 Incident 诊断运行时。返回 JSON。"},
        {
            "role": "user",
            "incident": {"title": "API 502", "summary": "checkout intermittent"},
            "working_set": {"recent_turns": [{"turn_index": 1, "plan_summary": "t1"}]},
        },
    ]
    structured = estimate_json(messages)
    wire = estimate_model_request(messages, [])
    fake_usage = max(1, int(wire * 0.95))
    sample = calibrate_estimate_vs_usage(estimated=wire, input_tokens=fake_usage)
    sample["structured_estimated"] = structured
    sample["wire_estimated"] = wire
    return [sample]


def run_real_calibration(*, samples: int) -> list[dict[str, Any]]:
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.ports.model import ModelRequest
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    api_key = os.environ.get("ANTISENTINEL_MODEL_API_KEY", "").strip()
    base_url = os.environ.get("ANTISENTINEL_MODEL_BASE_URL", "").strip()
    model_name = os.environ.get("ANTISENTINEL_MODEL_NAME", "").strip()
    if not api_key or not base_url or not model_name:
        raise SystemExit("real calibration requires ANTISENTINEL_MODEL_* in environment")

    records: list[dict[str, Any]] = []
    for index in range(samples):
        incident = Incident.create(
            title=f"calib-wire-{index}",
            source="eval",
            summary="请调用 read_health(service=api) 一次，然后给出 final。",
        )
        session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
        registry = ToolRegistry(auto_discover=False)
        registry.register(
            ToolDefinition(
                name="read_health",
                description="health",
                argument_schema={
                    "type": "object",
                    "properties": {"service": {"type": "string"}},
                    "required": ["service"],
                    "additionalProperties": False,
                },
                handler=lambda arguments: ToolExecutionResult(
                    status="succeeded",
                    result={"ok": True},
                    result_summary=f"service {arguments.get('service')} ok",
                ),
            )
        )
        model = OpenAICompatibleModelAdapter(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            timeout=float(os.getenv("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "60")),
        )
        per_call: list[dict[str, Any]] = []
        original = model.complete

        def wrapped(request: ModelRequest, _orig=original, _bucket=per_call):
            structured = sum(estimate_json(message) for message in request.messages) + estimate_json(request.tools)
            wire_raw = estimate_model_request(request.messages, request.tools, safety_scale=1.0)
            wire_scaled = estimate_model_request(request.messages, request.tools)
            response = _orig(request)
            usage_in = int(getattr(getattr(response, "usage", None), "input_tokens", 0) or 0)
            sample = calibrate_estimate_vs_usage(estimated=wire_raw, input_tokens=usage_in)
            sample["structured_estimated"] = structured
            sample["wire_raw_estimated"] = wire_raw
            sample["wire_scaled_estimated"] = wire_scaled
            sample["structured_ratio"] = (structured / usage_in) if usage_in else None
            sample["scaled_ratio"] = (wire_scaled / usage_in) if usage_in else None
            sample["safety_scale"] = DEFAULT_WIRE_SAFETY_SCALE
            _bucket.append(sample)
            return response

        model.complete = wrapped  # type: ignore[method-assign]
        result = RuntimeEngine().run(
            incident,
            session,
            model,
            registry=registry,
            config=RuntimeConfig(max_turns=3, max_total_tool_calls=3, max_context_tokens=8192),
        )
        for sample in per_call:
            sample["session_status"] = result.status
            sample["turn_count"] = result.turn_count
            records.append(sample)
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    abs_errors = [r["abs_error"] for r in records]
    ratios = [r["ratio"] for r in records if r.get("ratio") is not None]
    structured_ratios = [r["structured_ratio"] for r in records if r.get("structured_ratio") is not None]
    scaled_ratios = [r["scaled_ratio"] for r in records if r.get("scaled_ratio") is not None]
    return {
        "count": len(records),
        "estimate_version": ESTIMATE_VERSION,
        "safety_scale": DEFAULT_WIRE_SAFETY_SCALE,
        "abs_error_mean": statistics.mean(abs_errors),
        "abs_error_p50": statistics.median(abs_errors),
        "abs_error_max": max(abs_errors),
        "wire_raw_ratio_mean": statistics.mean(ratios) if ratios else None,
        "wire_raw_ratio_p50": statistics.median(ratios) if ratios else None,
        "wire_scaled_ratio_mean": statistics.mean(scaled_ratios) if scaled_ratios else None,
        "structured_ratio_mean": statistics.mean(structured_ratios) if structured_ratios else None,
        "note": (
            "ratio=estimated/usage; structured~0.6 was methodology+shape bug; "
            "wire_raw~0.84 residual tokenizer gap; pack uses wire*safety_scale"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate wire estimate vs provider usage")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    if args.real:
        _load_env()
    if args.output.exists():
        raise SystemExit(f"output must not exist: {args.output}")
    args.output.mkdir(parents=True)
    records = run_real_calibration(samples=args.samples) if args.real else run_fake_calibration()
    summary = summarize(records)
    report = {"mode": "real" if args.real else "fake", "summary": summary, "samples": records}
    _write_json(args.output / "calibration.json", report)
    readme = [
        "# estimate_tokens calibration (wire)",
        "",
        f"- mode: {report['mode']}",
        f"- version: {ESTIMATE_VERSION}",
        f"- samples: {summary.get('count')}",
        f"- wire raw ratio mean/p50: {summary.get('wire_raw_ratio_mean')} / {summary.get('wire_raw_ratio_p50')}",
        f"- wire scaled ratio mean (pack gate): {summary.get('wire_scaled_ratio_mean')}",
        f"- structured ratio mean (legacy under-count): {summary.get('structured_ratio_mean')}",
        f"- safety_scale: {summary.get('safety_scale')}",
        f"- abs_error mean/p50/max: {summary.get('abs_error_mean')} / {summary.get('abs_error_p50')} / {summary.get('abs_error_max')}",
        "",
        "Pack `total_estimated` uses `estimate_model_request` (wire × safety_scale).",
        "",
    ]
    (args.output / "README.md").write_text("\n".join(readme), encoding="utf-8")
    print("\n".join(readme))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
