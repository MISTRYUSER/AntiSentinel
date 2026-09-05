"""Isolated validation runner for the PRD-004 Skill / Plugin stages."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from antisentinel.capabilities.bootstrap import PluginConfig, assemble_plugins  # noqa: E402
from antisentinel.capabilities.loader import SkillLoader  # noqa: E402
from antisentinel.capabilities.package import build_local_package  # noqa: E402
from antisentinel.capabilities.runtime_state import SkillRuntimeState  # noqa: E402
from antisentinel.capabilities.tools import InMemorySkillStateStore, SkillRuntime  # noqa: E402
from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter  # noqa: E402
from antisentinel.domain.incident import Incident  # noqa: E402
from antisentinel.domain.session import Session  # noqa: E402
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult  # noqa: E402
from antisentinel.tools.registry import ToolRegistry  # noqa: E402
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine  # noqa: E402


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _package(root: Path, *, instructions_path: str = "diagnosis.md", required_tools: list[str] | None = None,
             extra_manifest: dict[str, Any] | None = None, references: list[dict[str, str]] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "diagnosis.md").write_text("body must never be read during registration", encoding="utf-8")
    (root / "runbook.md").write_text("body must never be read during registration", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "plugin_id": root.name.replace("_", "-"),
        "version": "1.0.0",
        "tool_exports": [],
        "skills": [
            {"skill_id": f"{root.name.replace('_', '-')}/diagnosis", "version": "1.0.0", "name": "diagnosis",
             "description": "diagnose", "use_cases": ["health"], "instructions_path": instructions_path,
             "required_tools": required_tools or [], "references": references or []},
            {"skill_id": f"{root.name.replace('_', '-')}/runbook", "version": "1.0.0", "name": "runbook",
             "description": "run", "use_cases": ["health"], "instructions_path": "runbook.md",
             "required_tools": [], "references": []},
        ],
    }
    manifest["plugin_id"] = "valid" if root.name == "valid" else manifest["plugin_id"]
    for item in manifest["skills"]:
        item["skill_id"] = f"{manifest['plugin_id']}/{item['name']}"
    manifest.update(extra_manifest or {})
    _write_json(root / "plugin.json", manifest)


def run_stage_41(output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output directory must not already exist")
    fixtures = output / "fixtures"
    valid = fixtures / "valid"
    _package(valid)
    bad_roots: list[Path] = []
    for name in ("missing_tool", "missing_resource", "parent_path", "absolute_path", "extra_field", "invalid_reference"):
        root = fixtures / name
        _package(root)
        bad_roots.append(root)
    _package(bad_roots[0], required_tools=["not-registered"])
    missing = json.loads((bad_roots[1] / "plugin.json").read_text())
    missing["skills"][0]["instructions_path"] = "absent.md"
    _write_json(bad_roots[1] / "plugin.json", missing)
    parent = json.loads((bad_roots[2] / "plugin.json").read_text())
    parent["skills"][0]["instructions_path"] = "../outside.md"
    _write_json(bad_roots[2] / "plugin.json", parent)
    absolute = json.loads((bad_roots[3] / "plugin.json").read_text())
    absolute["skills"][0]["instructions_path"] = str(bad_roots[3] / "diagnosis.md")
    _write_json(bad_roots[3] / "plugin.json", absolute)
    extra = json.loads((bad_roots[4] / "plugin.json").read_text())
    extra["unexpected"] = True
    _write_json(bad_roots[4] / "plugin.json", extra)
    invalid_ref = json.loads((bad_roots[5] / "plugin.json").read_text())
    invalid_ref["skills"][0]["references"] = [{"reference_id": "guide", "path": "missing.md"}]
    _write_json(bad_roots[5] / "plugin.json", invalid_ref)

    original_read_text = Path.read_text

    def guard_body_reads(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.suffix == ".md":
            raise AssertionError(f"registration read resource body: {path}")
        return original_read_text(path, *args, **kwargs)

    Path.read_text = guard_body_reads  # type: ignore[assignment]
    try:
        catalog = assemble_plugins(
            [PluginConfig(valid), *(PluginConfig(item, required=False) for item in bad_roots)],
            ToolRegistry(auto_discover=False),
        )
    finally:
        Path.read_text = original_read_text  # type: ignore[assignment]
    catalog_value = catalog.to_dict()
    diagnostics = [item.to_dict() for item in catalog.diagnostics]
    report = {
        "case_pass": len(catalog.skills.list()) == 2 and len(diagnostics) == 6 and not catalog.tool_registry.manifests(),
        "body_reads": 0,
        "diagnostic_count": len(diagnostics),
        "artifact_count": 3,
        "skill_count": len(catalog.skills.list()),
        "tool_leaks": len(catalog.tool_registry.manifests()),
    }
    _write_json(output / "catalog.json", {"skill_count": len(catalog.skills.list()), **catalog_value})
    _write_json(output / "diagnostics.json", {"diagnostic_count": len(diagnostics), "items": diagnostics})
    _write_json(output / "report.json", report)
    return report


class _StateStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.value: dict[str, Any] | None = None

    def save(self, value: dict[str, Any]) -> None:
        if self.fail:
            raise OSError("intentional checkpoint failure")
        self.value = value


def run_stage_42(output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output directory must not already exist")
    stage_41 = run_stage_41(output / "stage41")
    source = output / "fixtures" / "source"
    _package(source)
    (source / "references").mkdir()
    (source / "references" / "guide.md").write_text("version one reference", encoding="utf-8")
    source_manifest = json.loads((source / "plugin.json").read_text())
    source_manifest["skills"][0]["references"] = [{"reference_id": "guide", "description": "guide", "path": "references/guide.md"}]
    _write_json(source / "plugin.json", source_manifest)
    first = build_local_package(source, output / "releases")
    old_loader = SkillLoader(first)
    old_state = SkillRuntimeState(run_id="old-run", release_id=first.release_id)
    state_store = _StateStore()
    old_state.commit_activation(old_state.prepare_activation(old_loader.load(f"{first.plugin.plugin_id}/diagnosis")), state_store)

    source_manifest["version"] = "1.0.1"
    for skill in source_manifest["skills"]:
        skill["version"] = "1.0.1"
    _write_json(source / "plugin.json", source_manifest)
    (source / "references" / "guide.md").write_text("version two reference", encoding="utf-8")
    second = build_local_package(source, output / "releases")
    old_reference = old_loader.read_reference(f"{first.plugin.plugin_id}/diagnosis", "guide")
    old_state.commit_reference(old_state.prepare_reference(old_reference), state_store)

    failed_state = SkillRuntimeState(run_id="failed-run", release_id=first.release_id)
    failed = failed_state.commit_activation(
        failed_state.prepare_activation(old_loader.load(f"{first.plugin.plugin_id}/diagnosis")), _StateStore(fail=True)
    )
    report = {
        "case_pass": stage_41["case_pass"] and old_reference.content == "version one reference" and failed.error is not None and failed_state.selected_skill_id is None,
        "activation_failures": 0 if failed_state.selected_skill_id is None else 1,
        "old_reference_content": old_reference.content,
        "release_count": 2,
        "state_revision": old_state.revision,
    }
    _write_json(output / "releases.json", {"first_release_id": first.release_id, "second_release_id": second.release_id})
    _write_json(output / "state.json", state_store.value)
    _write_json(output / "report.json", report)
    return report


def run_stage_43(output: Path) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output directory must not already exist")
    api_key = os.getenv("ANTISENTINEL_MODEL_API_KEY", "").strip()
    base_url = os.getenv("ANTISENTINEL_MODEL_BASE_URL", "").strip()
    model_name = os.getenv("ANTISENTINEL_MODEL_NAME", "").strip()
    if not api_key or not base_url or not model_name:
        raise ValueError("real model configuration is incomplete")
    source = output / "fixture" / "source"
    _package(source, required_tools=["read_health"])
    (source / "diagnosis.md").write_text("先调用 read_health 获取实际健康状态；收到工具结果后再输出最终中文诊断。", encoding="utf-8")
    published = build_local_package(source, output / "releases")
    loader = SkillLoader(published)
    state = SkillRuntimeState(run_id="deepseek-stage43", release_id=published.release_id)
    skill_runtime = SkillRuntime(loader=loader, state=state, state_store=InMemorySkillStateStore(),
                                 allowed_tool_names=frozenset({"read_health"}), base_tool_names=frozenset())
    selected = skill_runtime.select(f"{published.plugin.plugin_id}/diagnosis")
    if selected.status != "succeeded":
        raise ValueError("explicit skill selection failed")
    calls: list[str] = []
    registry = ToolRegistry(auto_discover=False)
    registry.register(ToolDefinition(
        name="read_health", description="Read the fixed local health fixture.",
        argument_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        handler=lambda _: (calls.append("read_health") or ToolExecutionResult(
            status="succeeded", result={"service": "fixture", "status": "healthy"},
            result_summary="fixture health check returned healthy",
        )),
    ))
    incident = Incident.create(title="请按已激活 diagnosis Skill 诊断本地健康状态", source="stage43-case")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["stage43"])
    model = OpenAICompatibleModelAdapter(base_url=base_url, api_key=api_key, model=model_name, timeout=30)
    result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(max_turns=4), skill_runtime=skill_runtime)
    report = {
        "case_pass": result.status == "completed" and len(calls) >= 1 and state.selected_skill_id == f"{published.plugin.plugin_id}/diagnosis",
        "runtime_status": result.status,
        "model_calls": result.turn_count,
        "tool_calls": len(result.tool_calls),
        "health_calls": len(calls),
        "input_tokens": result.token_usage.input_tokens,
        "output_tokens": result.token_usage.output_tokens,
        "selected_skill_id": state.selected_skill_id,
        "error": result.error,
    }
    _write_json(output / "report.json", report)
    _write_json(output / "runtime-result.json", {"status": result.status, "error": result.error,
                                                   "task_summaries": list(result.task_summaries)})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=("4.1", "4.2", "4.3"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        report = {"4.1": run_stage_41, "4.2": run_stage_42, "4.3": run_stage_43}[args.stage](Path(args.output))
    except (AssertionError, OSError, ValueError) as exc:
        print(json.dumps({"case_pass": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["case_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
