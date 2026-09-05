import time


def test_application_reloads_incident_session_and_result_from_disk(monkeypatch, tmp_path):
    from antisentinel.entry.application import DiagnosisApplicationService

    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("ANTISENTINEL_REDIS_URL", raising=False)
    service = DiagnosisApplicationService.from_environment()
    incident = service.create_incident(title="persistent project", summary="background", source="operator")
    started = service.start_session(incident_id=incident.incident_id, participant_ids=["operator-1"], model_mode="fake")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if service.get_session(started["session_id"]).get("status") != "running":
            break
        time.sleep(0.01)

    reloaded = DiagnosisApplicationService.from_environment()
    restored = reloaded.get_session(started["session_id"])

    assert str(incident.incident_id) in reloaded.incidents
    assert restored["session_id"] == started["session_id"]
    assert restored["status"] == "completed"
    assert restored["turns"]


def test_application_persists_and_reloads_skill_usage(monkeypatch, tmp_path):
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.entry.application import DiagnosisApplicationService
    from antisentinel.tools.manifest import ToolDefinition
    from antisentinel.tools.registry import ToolRegistry

    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("ANTISENTINEL_REDIS_URL", raising=False)
    service = DiagnosisApplicationService.from_environment()

    def build_runtime():
        model = FakeProviderModel([
            {"tasks": [{"task_id": "health", "objective": "读取健康状态", "tool_calls": [{"tool_name": "read_health", "arguments": {}}]}]},
            {"final": {"summary": "完成", "diagnosis": "健康", "confidence": 0.9, "evidence_refs": []}},
        ])
        registry = ToolRegistry(auto_discover=False)
        registry.register(ToolDefinition("read_health", "health", {"type": "object", "properties": {}}, lambda _: {"status": "healthy"}))
        return model, registry

    service._runtime_builder = build_runtime
    incident = service.create_incident(title="skill persistence", summary=None, source="test")
    started = service.start_session(incident_id=incident.incident_id, participant_ids=["operator"], model_mode="fake", skill_id="diagnosis/diagnosis", skill_version="1.0.0")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and service.get_session(started["session_id"]).get("status") == "running":
        time.sleep(0.01)

    restored = DiagnosisApplicationService.from_environment().get_session(started["session_id"])

    assert restored["status"] == "completed"
    assert restored["skill_usage"]["selected_skill_id"] == "diagnosis/diagnosis"
    assert restored["skill_usage"]["selected_skill_version"] == "1.0.0"
    assert len(restored["skill_usage"]["release_id"]) == 64
