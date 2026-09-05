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
