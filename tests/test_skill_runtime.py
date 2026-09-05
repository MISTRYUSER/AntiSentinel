"""Skill disclosure must be run-scoped and enforced before cached execution."""

from pathlib import Path

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.tools.manifest import ToolDefinition
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine
from antisentinel.worker.runtime.checkpoint import InMemoryCheckpointStore
from antisentinel.ports.model import ModelError

from tests.test_skill_catalog import package, registry as base_registry, write_manifest


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def make_session():
    incident = Incident.create(title="Skill runtime", source="test")
    return incident, Session.create(incident_id=incident.incident_id, participant_ids=["operator"])


def skill_runtime(tmp_path, handler):
    from antisentinel.capabilities.loader import SkillLoader
    from antisentinel.capabilities.package import build_local_package
    from antisentinel.capabilities.runtime_state import SkillRuntimeState
    from antisentinel.capabilities.tools import InMemorySkillStateStore, SkillRuntime

    source = tmp_path / "source"
    manifest = package(source, tools=["read_health"])
    (source / "diagnosis.md").write_text("Use health evidence before diagnosing.")
    (source / "references").mkdir()
    (source / "references" / "guide.md").write_text("Guide: inspect the health endpoint.")
    manifest["skills"][0]["references"] = [{"reference_id": "guide", "description": "health guide", "path": "references/guide.md"}]
    write_manifest(source, manifest)
    published = build_local_package(source, tmp_path / "published")
    registry = ToolRegistry(auto_discover=False)
    registry.register(ToolDefinition(
        name="read_health", description="read health", argument_schema={"type": "object", "properties": {}, "required": []},
        handler=handler,
    ))
    runtime = SkillRuntime(
        loader=SkillLoader(published), state=SkillRuntimeState(run_id="run", release_id=published.release_id),
        state_store=InMemorySkillStateStore(), allowed_tool_names=frozenset({"read_health"}), base_tool_names=frozenset(),
    )
    return runtime, registry


def test_load_only_discloses_required_tool_on_next_model_turn(tmp_path):
    calls = []
    runtime, registry = skill_runtime(tmp_path, lambda _: calls.append("health") or {"status": "ok"})
    incident, session = make_session()
    model = FakeModel([
        {"tasks": [{"task_id": "load", "objective": "load", "tool_calls": [
            {"tool_name": "skill.load", "arguments": {"skill_id": "local/diagnosis"}},
            {"tool_name": "read_health", "arguments": {}},
        ]}]},
        {"tasks": [{"task_id": "inspect", "objective": "inspect", "tool_calls": [
            {"tool_name": "read_health", "arguments": {}},
        ]}]},
        {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}},
    ])

    result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(), skill_runtime=runtime)

    assert result.status == "completed"
    assert result.skill_usage["selected_skill_id"] == "local/diagnosis"
    assert "skill.loaded" in {event.type for event in result.events}
    assert calls == ["health"]
    assert [item["name"] for item in model.requests[0].tools] == ["skill.load"]
    assert {item["name"] for item in model.requests[1].tools} == {"skill.load", "skill.read_reference", "read_health"}
    assert "Use health evidence before diagnosing." not in str(model.requests[0].messages)
    assert str(model.requests[1].messages).count("Use health evidence before diagnosing.") == 1
    assert any(attempt.error and attempt.error["code"] == "tool_not_disclosed" for attempt in result.attempts)


def test_explicit_selection_exposes_instruction_and_tool_before_first_model_call(tmp_path):
    runtime, registry = skill_runtime(tmp_path, lambda _: {"status": "ok"})
    runtime.select("local/diagnosis")
    incident, session = make_session()
    model = FakeModel([{"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}}])

    result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(), skill_runtime=runtime)

    assert result.status == "completed"
    assert {item["name"] for item in model.requests[0].tools} == {"skill.load", "skill.read_reference", "read_health"}
    assert str(model.requests[0].messages).count("Use health evidence before diagnosing.") == 1


def test_two_active_skills_are_injected_in_order_and_references_are_namespaced(tmp_path):
    runtime, registry = skill_runtime(tmp_path, lambda _: {"status": "ok"})
    runtime.select("local/diagnosis")
    runtime.select("local/runbook")
    payload = runtime.context_payload()
    assert [item["skill_id"] for item in payload["active_skills"]] == ["local/diagnosis", "local/runbook"]
    assert runtime.visible_tool_names() >= {"skill.load", "skill.read_reference", "read_health"}
    result = runtime._read_reference({"skill_id": "local/diagnosis", "reference_id": "guide"})
    assert result.status == "succeeded"
    assert "local/diagnosis:guide" in runtime.usage()["reference_hashes"]


def test_unknown_skill_never_runs_business_tool(tmp_path):
    calls = []
    runtime, registry = skill_runtime(tmp_path, lambda _: calls.append("health") or {"status": "ok"})
    incident, session = make_session()
    model = FakeModel([
        {"tasks": [{"task_id": "load", "objective": "load", "tool_calls": [{"tool_name": "skill.load", "arguments": {"skill_id": "local/unknown"}}]}]},
        {"final": {"summary": "fallback", "diagnosis": "unknown", "confidence": 0.1, "evidence_refs": []}},
    ])

    result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(), skill_runtime=runtime)

    assert result.status == "completed"
    assert calls == []
    assert result.attempts[0].error["code"] == "unknown_skill"
    assert runtime.state.selected_skill_id is None
    assert "skill.load_failed" in {event.type for event in result.events}


def test_reference_is_not_injected_until_read_and_is_run_scoped(tmp_path):
    runtime, registry = skill_runtime(tmp_path, lambda _: {"status": "ok"})
    incident, session = make_session()
    model = FakeModel([
        {"tasks": [{"task_id": "load", "objective": "load", "tool_calls": [{"tool_name": "skill.load", "arguments": {"skill_id": "local/diagnosis"}}]}]},
        {"tasks": [{"task_id": "reference", "objective": "read", "tool_calls": [{"tool_name": "skill.read_reference", "arguments": {"reference_id": "guide"}}]}]},
        {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}},
    ])

    result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(), skill_runtime=runtime)

    assert "Guide: inspect the health endpoint." not in str(model.requests[1].messages)
    assert str(model.requests[2].messages).count("Guide: inspect the health endpoint.") == 1
    assert "skill.reference_read" in {event.type for event in result.events}
    assert result.skill_usage["reference_hashes"]["local/diagnosis:guide"]


def test_checkpoint_captures_active_skill_identity_after_load(tmp_path):
    runtime, registry = skill_runtime(tmp_path, lambda _: {"status": "ok"})
    incident, session = make_session()
    store = InMemoryCheckpointStore()
    model = FakeModel([
        {"tasks": [{"task_id": "load", "objective": "load", "tool_calls": [{"tool_name": "skill.load", "arguments": {"skill_id": "local/diagnosis"}}]}]},
        ModelError("stop after load"),
    ])

    result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(),
                                 skill_runtime=runtime, checkpoint_store=store)
    checkpoint = store.load(session.session_id)

    assert result.status == "failed"
    assert checkpoint.skill_state["selected_skill_id"] == "local/diagnosis"
    assert checkpoint.skill_state["release_id"] == runtime.state.release_id


def test_resume_restores_all_active_skills_before_next_model_call(tmp_path):
    runtime, registry = skill_runtime(tmp_path, lambda _: {"status": "ok"})
    incident, session = make_session()
    store = InMemoryCheckpointStore()
    first = FakeModel([
        {"tasks": [{"task_id": "load", "objective": "load", "tool_calls": [{"tool_name": "skill.load", "arguments": {"skill_id": "local/diagnosis"}}, {"tool_name": "skill.load", "arguments": {"skill_id": "local/runbook"}}]}]},
        ModelError("interrupt"),
    ])
    RuntimeEngine().run(incident, session, first, registry=registry, config=RuntimeConfig(), skill_runtime=runtime, checkpoint_store=store)
    from antisentinel.capabilities.loader import SkillLoader
    from antisentinel.capabilities.runtime_state import SkillRuntimeState
    from antisentinel.capabilities.tools import InMemorySkillStateStore, SkillRuntime
    resumed_runtime = SkillRuntime(SkillLoader(runtime.loader.package), SkillRuntimeState(run_id="resumed", release_id=runtime.state.release_id), InMemorySkillStateStore(), frozenset({"read_health"}), frozenset())
    resumed = FakeModel([{"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}}])
    result = RuntimeEngine().run(incident, session, resumed, registry=registry, config=RuntimeConfig(), skill_runtime=resumed_runtime, checkpoint_store=store, resume=True)
    assert result.status == "completed"
    assert resumed_runtime.state.active_skill_ids == ("local/diagnosis", "local/runbook")
    assert [message["skill_id"] for message in resumed.requests[0].messages if message["role"] == "skill"] == ["local/diagnosis", "local/runbook"]
