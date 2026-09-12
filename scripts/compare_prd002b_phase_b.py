#!/usr/bin/env python3
"""Phase B TIGHT vs WIDE ContextBudget comparison (Fake model, no secrets)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TURN1 = "error code E42 from turn1"
SECRET = "SECRET_RAW_SHOULD_NOT_LEAK"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _pack_source_case(*, max_context_tokens: int) -> dict[str, Any]:
    from antisentinel.code_map.source_context import SourceContextSlice
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.domain.turn import Turn
    from antisentinel.worker.runtime.budget import ContextBudget
    from antisentinel.worker.runtime.pack import PackInput, pack_context

    incident = Incident.create(title="pack-source", source="eval")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    turn = Turn.create(session_id=session.session_id)
    slices = [
        SourceContextSlice(
            str(incident.incident_id),
            f"ev-{i}",
            "repo",
            "snap",
            "sha",
            f"f{i}.py",
            ("x" * 12_000) + f"\n# marker-{i}\n",
            f"hash-{i}",
        )
        for i in range(5)
    ]
    result = pack_context(
        PackInput(
            incident=incident,
            session=session,
            turn=turn,
            working_set=None,
            tools=[],
            source_context=slices,
            budget=ContextBudget(max_context_tokens=max_context_tokens, strict=True),
            system_override="sys",
        )
    )
    source_messages = [m for m in result.messages if m.get("role") == "source_context"]
    packed = source_messages[0]["slices"] if source_messages else []
    payload = result.report.to_event_payload()
    return {
        "max_context_tokens": max_context_tokens,
        "within_budget": result.report.within_budget,
        "total_estimated": result.report.total_estimated,
        "truncated_slice_count": payload["truncated_slice_count"],
        "dropped_count": payload["dropped_count"],
        "source_slice_count": len(packed),
        "has_truncated_flag": any(item.get("truncated") for item in packed),
        "payload_has_bodies": "truncated_slices" in payload,
    }


def _runtime_continuity(*, max_context_tokens: int) -> dict[str, Any]:
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    class FakeModel:
        def __init__(self) -> None:
            self.requests: list[Any] = []
            self._n = 0

        def complete(self, request):
            self.requests.append(request)
            self._n += 1
            if self._n == 1:
                return {
                    "tasks": [
                        {
                            "task_id": "t1",
                            "objective": "inspect",
                            "tool_calls": [{"tool_name": "read_logs", "arguments": {"q": "e"}}],
                        }
                    ]
                }
            return {"final": {"summary": "done", "diagnosis": "ok", "confidence": 0.9, "evidence_refs": []}}

    incident = Incident.create(title="CMP-B", source="eval", summary="budget compare")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["eval"])
    registry = ToolRegistry(auto_discover=False)
    registry.register(
        ToolDefinition(
            name="read_logs",
            description="logs",
            argument_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
            handler=lambda arguments: ToolExecutionResult(
                status="succeeded",
                result={"raw": SECRET},
                result_summary=TURN1,
            ),
        )
    )
    model = FakeModel()
    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry,
        config=RuntimeConfig(max_context_tokens=max_context_tokens, max_turns=4),
    )
    built = [event for event in result.events if event.type == "context.built"]
    last_payload = built[-1].payload if built else {}
    corpus = "\n".join(str(request.messages) for request in model.requests)
    return {
        "status": result.status,
        "context_built_count": len(built),
        "within_budget": last_payload.get("within_budget"),
        "early_hit": TURN1 in corpus,
        "secret_leaked": SECRET in corpus,
        "last_payload_keys": sorted(last_payload.keys()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    wide_pack = _pack_source_case(max_context_tokens=1_000_000)
    tight_pack = _pack_source_case(max_context_tokens=8192)
    wide_rt = _runtime_continuity(max_context_tokens=1_000_000)
    tight_rt = _runtime_continuity(max_context_tokens=8192)
    gates = {
        "tight_pack_within_budget": tight_pack["within_budget"] is True,
        "wide_pack_within_budget": wide_pack["within_budget"] is True,
        "tight_source_pressure": (tight_pack["truncated_slice_count"] + tight_pack["dropped_count"]) > 0,
        "tight_slice_cap": tight_pack["source_slice_count"] <= 4,
        "wide_keeps_more_or_equal": wide_pack["source_slice_count"] >= tight_pack["source_slice_count"],
        "tight_continuity": tight_rt["early_hit"] is True,
        "no_secret_leak": (not tight_rt["secret_leaked"]) and (not wide_rt["secret_leaked"]),
        "context_built_present": tight_rt["context_built_count"] >= 1 and wide_rt["context_built_count"] >= 1,
        "desensitized_payload": "truncated_slices" not in (tight_rt.get("last_payload_keys") or []),
        "event_payload_no_bodies": tight_pack["payload_has_bodies"] is False,
    }
    report = {
        "wide_pack": wide_pack,
        "tight_pack": tight_pack,
        "wide_runtime": wide_rt,
        "tight_runtime": tight_rt,
        "gates": gates,
        "pass": all(gates.values()),
    }
    _write_json(args.out / "compare.json", report)
    print(json.dumps({"pass": report["pass"], "gates": gates}, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
