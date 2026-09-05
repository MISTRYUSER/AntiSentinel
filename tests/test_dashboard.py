import time

from fastapi.testclient import TestClient

from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService


def test_dashboard_and_observability_tree_expose_session_hierarchy():
    client = TestClient(create_app(DiagnosisApplicationService.default_fake()))
    incident = client.post("/api/incidents", json={"title": "dashboard smoke", "source": "operator"}).json()
    started = client.post(f"/api/incidents/{incident['incident_id']}/sessions", json={"participant_ids": ["operator-1"], "model_mode": "fake"}).json()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if client.get(f"/api/sessions/{started['session_id']}").json().get("status") != "running":
            break
        time.sleep(0.01)
    assert client.get("/dashboard").status_code == 200
    tree = client.get("/api/observability/tree")
    detail = client.get(f"/api/observability/sessions/{started['session_id']}")
    assert tree.json()[0]["sessions"][0]["session_id"] == started["session_id"]
    assert detail.json()["token_usage"] is not None
    assert detail.json()["trace"]["spans"]
