"""Run the frozen local diagnosis smoke case against DeepSeek A/B/C groups."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
from antisentinel.capabilities.loader import SkillLoader
from antisentinel.capabilities.package import build_local_package
from antisentinel.capabilities.runtime_state import SkillRuntimeState
from antisentinel.capabilities.tools import InMemorySkillStateStore, SkillRuntime
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _published(output: Path):
    source = output / "fixture" / "diagnosis"
    source.mkdir(parents=True)
    (source / "instructions.md").write_text("先调用 read_health 获取实际状态；收到业务工具结果后输出最终中文诊断。", encoding="utf-8")
    _write(source / "plugin.json", {"schema_version": 1, "plugin_id": "diagnosis", "version": "1.0.0", "tool_exports": [], "skills": [{"skill_id": "diagnosis/diagnosis", "version": "1.0.0", "name": "diagnosis", "description": "诊断 Redis 健康异常并引用实际工具结果。", "use_cases": ["Redis健康失败", "服务异常诊断"], "instructions_path": "instructions.md", "required_tools": ["read_health"], "references": []}]})
    return build_local_package(source, output / "releases")


def main() -> int:
    output = Path(sys.argv[1])
    if output.exists():
        raise SystemExit("output must not exist")
    api_key = os.getenv("ANTISENTINEL_MODEL_API_KEY", "")
    base_url = os.getenv("ANTISENTINEL_MODEL_BASE_URL", "")
    model_name = os.getenv("ANTISENTINEL_MODEL_NAME", "")
    if not all((api_key, base_url, model_name)):
        raise SystemExit("DeepSeek environment is incomplete")
    published = _published(output)
    trials = []
    for group in ("A", "B", "C"):
        for trial_id in range(1, 4):
            calls = []
            registry = ToolRegistry(auto_discover=False)
            registry.register(ToolDefinition("read_health", "读取固定健康状态", {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, lambda _, calls=calls: (calls.append(1) or ToolExecutionResult(status="succeeded", result={"status": "healthy"}, result_summary="Redis health is healthy"))))
            skill_runtime = None
            if group in {"B", "C"}:
                skill_runtime = SkillRuntime(SkillLoader(published), SkillRuntimeState(run_id=f"{group}-{trial_id}", release_id=published.release_id), InMemorySkillStateStore(), frozenset({"read_health"}), frozenset())
                if group == "B":
                    skill_runtime.select("diagnosis/diagnosis")
            model = OpenAICompatibleModelAdapter(base_url=base_url, api_key=api_key, model=model_name, timeout=30)
            incident = Incident.create(title="Redis 健康检查失败，请使用可用能力检查后给出诊断。", source="skill-abc")
            session = Session.create(incident_id=incident.incident_id, participant_ids=["evaluation"])
            started = monotonic()
            result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(max_turns=4), skill_runtime=skill_runtime)
            selected = result.skill_usage.get("selected_skill_id") if result.skill_usage else None
            passed = result.status == "completed" and len(calls) == 1 and ((group == "A" and selected is None) or (group in {"B", "C"} and selected == "diagnosis/diagnosis"))
            trials.append({"case_id": "skill-execution-01", "group": group, "trial_id": trial_id, "status": "passed" if passed else "failed", "runtime_status": result.status, "selected_skill_id": selected, "health_calls": len(calls), "model_calls": result.turn_count, "input_tokens": result.token_usage.input_tokens, "output_tokens": result.token_usage.output_tokens, "elapsed_seconds": round(monotonic() - started, 3), "trace_id": result.trace_id, "error": result.error})
            with (output / "trials.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(trials[-1], ensure_ascii=False, sort_keys=True) + "\n")
    groups = {group: [item for item in trials if item["group"] == group] for group in ("A", "B", "C")}
    report = {"case_pass": len(trials) == 9 and all(item["status"] == "passed" for item in trials), "trial_count": len(trials), "groups": {key: {"trials": len(values), "passed": sum(item["status"] == "passed" for item in values), "input_tokens": sum(item["input_tokens"] for item in values), "output_tokens": sum(item["output_tokens"] for item in values), "elapsed_seconds": round(sum(item["elapsed_seconds"] for item in values), 3)} for key, values in groups.items()}}
    _write(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
