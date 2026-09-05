import time

from fastapi.testclient import TestClient

from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService


def test_session_message_is_persisted_and_restored_after_service_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("ANTISENTINEL_REDIS_URL", raising=False)
    service = DiagnosisApplicationService.from_environment()
    client = TestClient(create_app(service))
    incident = client.post("/api/incidents", json={"title": "daily conversation", "source": "operator"}).json()
    started = client.post(f"/api/incidents/{incident['incident_id']}/sessions", json={"participant_ids": ["operator-1"], "model_mode": "fake"}).json()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if client.get(f"/api/sessions/{started['session_id']}").json().get("status") != "running":
            break
        time.sleep(0.01)

    response = client.post(f"/api/sessions/{started['session_id']}/messages", json={"content": "今天继续看这个问题"})
    reloaded = DiagnosisApplicationService.from_environment()
    restored = reloaded.get_messages(started["session_id"])

    assert response.status_code == 200
    assert [item["role"] for item in restored] == ["user", "assistant"]
    assert restored[0]["content"] == "今天继续看这个问题"
    assert restored[1]["content"]
