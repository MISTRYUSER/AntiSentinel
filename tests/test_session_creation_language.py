import time

from fastapi.testclient import TestClient

from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService


def test_completed_incident_can_start_a_second_session():
    client = TestClient(create_app(DiagnosisApplicationService.default_fake()))
    incident = client.post("/api/incidents", json={"title": "session lifecycle", "source": "operator"}).json()
    first = client.post(f"/api/incidents/{incident['incident_id']}/sessions", json={"participant_ids": ["operator-1"], "model_mode": "fake"}).json()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if client.get(f"/api/sessions/{first['session_id']}").json().get("status") != "running":
            break
        time.sleep(0.01)

    second = client.post(f"/api/incidents/{incident['incident_id']}/sessions", json={"participant_ids": ["operator-1"], "model_mode": "fake"})

    assert second.status_code == 202
    assert second.json()["session_id"] != first["session_id"]


def test_system_prompt_requires_simplified_chinese_output():
    from antisentinel.worker.runtime.messages import build_system_message

    content = build_system_message()["content"]
    assert "简体中文" in content
    assert "summary" in content
