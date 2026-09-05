import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from antisentinel.adapters.llm.openai_compatible import FakeProviderModel, OpenAICompatibleModelAdapter
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.ports.model import ModelError, ModelRequest, ModelTimeoutError
from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService
from antisentinel.bootstrap import build_application


def request():
    return ModelRequest(
        incident_id="incident-1",
        session_id="session-1",
        turn_id="turn-1",
        messages=[{"role": "user", "content": "diagnose"}],
        tools=[{"name": "read_health", "argument_schema": {"type": "object"}}],
    )


def provider_response(payload):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}]},
    )


def wait_for_session(client, session_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/sessions/{session_id}")
        value = response.json()
        if value.get("status") != "running":
            return value
        time.sleep(0.01)
    raise AssertionError("session did not finish in time")


def test_real_adapter_maps_provider_request_and_structured_response():
    seen = {}

    def handler(http_request):
        seen["headers"] = dict(http_request.headers)
        seen["body"] = json.loads(http_request.content)
        return provider_response({"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}})

    adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1",
        api_key="server-secret",
        model="diagnosis-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = adapter.complete(request())

    assert response.final.diagnosis == "healthy"
    assert seen["headers"]["authorization"] == "Bearer server-secret"
    assert seen["body"]["model"] == "diagnosis-model"
    assert seen["body"]["messages"] == [{"role": "user", "content": "diagnose"}]
    assert seen["body"]["tools"] == [{
        "type": "function",
        "function": {
            "name": "read_health",
            "description": "",
            "parameters": {"type": "object"},
        },
    }]
    assert "response_format" not in seen["body"]


def test_real_adapter_maps_native_provider_tool_calls():
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1",
        api_key="server-secret",
        model="diagnosis-model",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_health", "arguments": '{"service":"api"}'},
            }]}}]
        }))),
    )

    response = adapter.complete(request())

    assert response.tasks[0].tool_calls[0].tool_name == "read_health"
    assert response.tasks[0].tool_calls[0].arguments == {"service": "api"}


def test_real_adapter_accepts_only_a_complete_fenced_json_response():
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1",
        api_key="server-secret",
        model="diagnosis-model",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: provider_response({
            "final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.8, "evidence_refs": []}
        }))),
    )
    original = adapter.client
    adapter.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200,
        json={"choices": [{"message": {"content": '```json\n{"final":{"summary":"done","diagnosis":"healthy","confidence":0.8,"evidence_refs":[]}}\n```'}}]},
    )))

    assert adapter.complete(request()).final.diagnosis == "healthy"
    original.close()


@pytest.mark.parametrize("status", [401, 429, 500])
def test_real_adapter_maps_provider_http_errors(status):
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1",
        api_key="server-secret",
        model="diagnosis-model",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text="failure"))),
    )

    with pytest.raises(ModelError, match=f"provider_http_{status}"):
        adapter.complete(request())


def test_real_adapter_maps_timeout_and_invalid_provider_output():
    timeout_adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1",
        api_key="server-secret",
        model="diagnosis-model",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))),
    )
    with pytest.raises(ModelTimeoutError):
        timeout_adapter.complete(request())

    invalid_adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1",
        api_key="server-secret",
        model="diagnosis-model",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))),
    )
    with pytest.raises(ModelError, match="invalid_provider_response"):
        invalid_adapter.complete(request())


def test_fake_provider_completes_without_credentials():
    provider = FakeProviderModel([
        {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.8, "evidence_refs": []}}
    ])

    response = provider.complete(request())

    assert response.final is not None
    assert response.final.diagnosis == "healthy"


def test_api_creates_incident_starts_session_and_returns_structured_runtime_result():
    client = TestClient(create_app(DiagnosisApplicationService.default_fake()))

    incident_response = client.post(
        "/api/incidents",
        json={"title": "checkout API returns 502", "summary": "intermittent failure", "source": "operator"},
    )
    assert incident_response.status_code == 201
    incident = incident_response.json()
    assert incident["status"] == "open"

    session_response = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "fake"},
    )
    assert session_response.status_code == 202
    started = session_response.json()
    result = wait_for_session(client, started["session_id"])
    assert result["status"] == "completed"
    assert result["final"]["diagnosis"]
    assert result["turns"]
    assert result["tasks"]
    assert result["tool_calls"]
    assert result["attempts"]

    query_response = client.get(f"/api/sessions/{result['session_id']}")
    assert query_response.status_code == 200
    assert query_response.json()["session_id"] == result["session_id"]


def test_api_maps_unknown_ids_invalid_model_and_duplicate_start():
    client = TestClient(create_app(DiagnosisApplicationService.default_fake()))
    assert client.post("/api/incidents/missing/sessions", json={"participant_ids": ["x"]}).status_code == 404
    assert client.get("/api/sessions/missing").status_code == 404

    response = client.post("/api/incidents", json={"title": "only title"})
    assert response.status_code == 422

    incident = client.post("/api/incidents", json={"title": "bug", "source": "operator"}).json()
    first = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "fake"},
    )
    assert first.status_code == 202
    wait_for_session(client, first.json()["session_id"])
    second = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "fake"},
    )
    assert second.status_code == 202
    assert second.json()["session_id"] != first.json()["session_id"]


def test_api_response_does_not_expose_secret_or_hidden_thoughts():
    client = TestClient(create_app(DiagnosisApplicationService.default_fake()))
    incident = client.post("/api/incidents", json={"title": "bug", "source": "operator"}).json()
    result = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "fake"},
    ).text

    assert "ANTISENTINEL_MODEL_API_KEY" not in result
    assert "hidden_thought" not in result
    assert "raw_tool_output" not in result


def test_codex_style_frontend_is_served_and_contains_runtime_surface():
    client = TestClient(create_app(DiagnosisApplicationService.default_fake()))

    page = client.get("/")
    script = client.get("/app.js")
    style = client.get("/styles.css")

    assert page.status_code == 200
    assert "What are we investigating?" in page.text
    assert "FAKE PROVIDER" in page.text
    assert "Session / Turn / Task" not in page.text  # status is rendered from API data, not hardcoded output
    assert script.status_code == 200
    assert "renderResult" in script.text
    assert "EventSource" in script.text
    assert "appendRuntimeEvent" in script.text
    assert style.status_code == 200
    assert "sidebar" in style.text
    assert "hidden_thought" not in page.text + script.text + style.text
    assert "API_KEY" not in page.text + script.text + style.text


def test_composition_root_builds_fake_application_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTISENTINEL_MODEL_MODE", raising=False)
    monkeypatch.delenv("ANTISENTINEL_MODEL_API_KEY", raising=False)
    client = TestClient(build_application())

    incident = client.post("/api/incidents", json={"title": "smoke", "source": "operator"}).json()
    started = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "fake"},
    ).json()
    result = wait_for_session(client, started["session_id"])

    assert result["status"] == "completed"


def test_runtime_config_reports_mode_without_exposing_credentials(monkeypatch):
    monkeypatch.setenv("ANTISENTINEL_MODEL_MODE", "real")
    monkeypatch.delenv("ANTISENTINEL_MODEL_API_KEY", raising=False)
    client = TestClient(create_app(DiagnosisApplicationService.from_environment()))

    response = client.get("/api/runtime/config")

    assert response.status_code == 200
    assert response.json() == {"model_mode": "real", "model_provider": "openai", "model_name": "gpt-4o-mini"}
    assert "API_KEY" not in response.text


def test_real_mode_without_api_key_returns_configuration_error(monkeypatch):
    monkeypatch.setenv("ANTISENTINEL_MODEL_MODE", "real")
    monkeypatch.delenv("ANTISENTINEL_MODEL_API_KEY", raising=False)
    client = TestClient(create_app(DiagnosisApplicationService.from_environment()))

    incident = client.post("/api/incidents", json={"title": "real config", "source": "operator"}).json()
    response = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "real"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "session_start_failed"


def test_session_sse_stream_emits_redacted_lifecycle_events():
    service = DiagnosisApplicationService.default_fake()
    client = TestClient(create_app(service))
    incident = client.post("/api/incidents", json={"title": "stream smoke", "source": "operator"}).json()
    started = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={"participant_ids": ["operator-1"], "model_mode": "fake"},
    ).json()

    with client.stream("GET", f"/api/sessions/{started['session_id']}/events") as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: session.started" in body
    assert "event: turn.started" in body
    assert "event: model.started" in body
    assert "event: tool.completed" in body
    assert "event: diagnosis.completed" in body
    assert "raw_tool_output" not in body
    assert '"service"' not in body
