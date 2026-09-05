import json

import httpx


def test_provider_usage_is_preserved_as_token_usage():
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.ports.model import ModelRequest

    response = httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps({"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}})}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "prompt_cache_hit_tokens": 80, "reasoning_tokens": 5},
    })
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1", api_key="server-secret", model="diagnosis-model",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: response)),
    )

    result = adapter.complete(ModelRequest("incident-1", "session-1", "turn-1", [{"role": "user", "content": "diagnose"}], []))

    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 30
    assert result.usage.cached_input_tokens == 80
    assert result.usage.reasoning_tokens == 5


def test_runtime_aggregates_token_usage_across_model_turns():
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.ports.model import TokenUsage
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeEngine

    model = FakeProviderModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": []}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}},
        {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}, "usage": {"prompt_tokens": 20, "completion_tokens": 3, "cached_tokens": 4}},
    ])
    incident = Incident.create(title="token test", source="operator")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])

    result = RuntimeEngine().run(incident, session, model, registry=ToolRegistry())

    assert result.status == "completed"
    assert result.token_usage == TokenUsage(input_tokens=30, output_tokens=5, cached_input_tokens=4)


def test_runtime_reports_token_usage_to_metrics():
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.tracing.metrics import MemoryMetrics
    from antisentinel.worker.runtime.engine import RuntimeEngine

    model = FakeProviderModel([{"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}, "usage": {"prompt_tokens": 12, "completion_tokens": 4}}])
    incident = Incident.create(title="token metrics", source="operator")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])
    metrics = MemoryMetrics()

    result = RuntimeEngine().run(incident, session, model, registry=ToolRegistry(), observability_metrics=metrics)

    assert result.status == "completed"
    assert 'model="unknown"' in metrics.render()
