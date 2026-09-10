#!/usr/bin/env python3
"""Optional Real DeepSeek smoke for Phase B modules (no secrets in output)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _load_dotenv_local() -> None:
    path = ROOT / ".env.local"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run_smoke() -> dict[str, Any]:
    # Broken local proxies often break Real calls.
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    api_key = os.environ.get("ANTISENTINEL_MODEL_API_KEY", "").strip()
    base_url = os.environ.get("ANTISENTINEL_MODEL_BASE_URL", "").strip()
    model_name = os.environ.get("ANTISENTINEL_MODEL_NAME", "").strip()
    if not api_key or not base_url or not model_name:
        return {"ok": False, "skipped": True, "reason": "missing ANTISENTINEL_MODEL_*"}

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
            description="Read service health",
            argument_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
            handler=lambda arguments: ToolExecutionResult(
                status="succeeded",
                result={"ok": False},
                result_summary="health endpoint returned 503",
            ),
        )
    )
    incident = Incident.create(
        title="phase-b real smoke",
        source="eval",
        summary="先调用 read_health(service=api) 一次，再给出 final JSON。",
    )
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry,
        config=RuntimeConfig(max_turns=3, max_context_tokens=8192, context_strict=True),
    )
    built = [event for event in result.events if event.type == "context.built"]
    return {
        "ok": result.status == "completed",
        "skipped": False,
        "status": result.status,
        "turn_count": result.turn_count,
        "context_built_count": len(built),
        "within_budget": all(event.payload.get("within_budget") for event in built) if built else None,
        "usage": {
            "input_tokens": result.token_usage.input_tokens,
            "output_tokens": result.token_usage.output_tokens,
        },
        "error": result.error,
        "model_provider_host": base_url.split("//", 1)[-1].split("/", 1)[0],
        "model_name": model_name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    _load_dotenv_local()
    report = run_smoke()
    _write_json(args.out / "report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "error"}, ensure_ascii=False, indent=2))
    if report.get("skipped"):
        return 0
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
