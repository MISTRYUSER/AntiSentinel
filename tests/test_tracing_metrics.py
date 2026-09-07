def test_trace_context_creates_root_and_child_spans_with_request_ids():
    from antisentinel.tracing.telemetry import Telemetry

    telemetry = Telemetry()
    with telemetry.span("incident.run", request_id="req-1") as root:
        with telemetry.span("model.complete", request_id="req-2", parent_request_id="req-1") as child:
            child.set_attribute("memory_type", "session")

    spans = telemetry.finished_spans
    assert [span.name for span in spans] == ["model.complete", "incident.run"]
    assert spans[0].attributes["request_id"] == "req-2"
    assert spans[0].attributes["parent_request_id"] == "req-1"
    assert spans[1].attributes["request_id"] == "req-1"
    assert root.name == "incident.run"


def test_memory_metrics_export_cache_and_worker_observations():
    from antisentinel.tracing.metrics import MemoryMetrics

    metrics = MemoryMetrics()
    metrics.cache_hit("preference", "redis")
    metrics.cache_miss("preference", "redis")
    metrics.worker_processed("preference")

    output = metrics.render()
    assert "antisentinel_memory_cache_hits_total" in output
    assert 'memory_type="preference"' in output
    assert "antisentinel_memory_worker_processed_total" in output


def test_memory_metrics_export_cache_bypass_separately_from_miss():
    from antisentinel.tracing.metrics import MemoryMetrics

    metrics = MemoryMetrics()
    metrics.cache_bypass("preference", "redis")

    output = metrics.render()
    assert "antisentinel_memory_cache_bypasses_total" in output
    assert 'memory_type="preference"' in output


def test_memory_metrics_export_token_usage():
    from antisentinel.ports.model import TokenUsage
    from antisentinel.tracing.metrics import MemoryMetrics

    metrics = MemoryMetrics()
    metrics.observe_tokens(TokenUsage(input_tokens=120, output_tokens=30, cached_input_tokens=80), model="deepseek-chat")

    output = metrics.render()
    assert "antisentinel_model_input_tokens_total" in output
    assert 'model="deepseek-chat"' in output


def test_model_adapter_emits_provider_shape_span():
    import httpx
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.tracing.telemetry import Telemetry
    from antisentinel.ports.model import ModelRequest

    telemetry = Telemetry()
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://llm.example/v1", api_key="server-secret", model="diagnosis-model", telemetry=telemetry,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"final":{"summary":"done","diagnosis":"healthy","confidence":0.8,"evidence_refs":[]}}'}}]
        }))),
    )
    adapter.complete(ModelRequest("incident-1", "session-1", "turn-1", [{"role": "user", "content": "diagnose"}], []))

    assert any(span.name == "model.complete" for span in telemetry.finished_spans)


def test_session_trace_keeps_trace_id_across_session_turn_and_request_spans():
    from antisentinel.tracing.telemetry import Telemetry, TraceContext

    telemetry = Telemetry()
    root = TraceContext.new(session_id="session-1")
    with telemetry.span("session.run", context=root) as session_span:
        turn = root.child(turn_id="turn-1", request_id="turn-1")
        with telemetry.span("turn", context=turn):
            request = turn.child(request_id="model-1")
            with telemetry.span("model.complete", context=request):
                pass

    spans = {span.name: span for span in telemetry.finished_spans}
    assert spans["session.run"].attributes["trace_id"] == root.trace_id
    assert spans["turn"].attributes["trace_id"] == root.trace_id
    assert spans["model.complete"].attributes["trace_id"] == root.trace_id
    assert spans["model.complete"].attributes["session_id"] == "session-1"
    assert spans["model.complete"].attributes["turn_id"] == "turn-1"
    assert spans["model.complete"].attributes["parent_request_id"] == "turn-1"


def test_runtime_trace_contains_tool_call_spans_linked_to_session_and_turn():
    from antisentinel.entry.application import DiagnosisApplicationService
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.tracing.telemetry import Telemetry
    from antisentinel.worker.runtime.engine import RuntimeEngine

    service = DiagnosisApplicationService.default_fake()
    model, registry = service._runtime_builder()
    incident = Incident.create(title="trace tool", source="operator")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])
    telemetry = Telemetry()

    result = RuntimeEngine(telemetry=telemetry).run(incident, session, model, registry=registry)

    tool_spans = [span for span in telemetry.finished_spans if span.name == "tool_call.execute"]
    assert result.status == "completed"
    assert len(tool_spans) >= 1
    assert len({span.attributes["request_id"] for span in tool_spans}) == len(tool_spans)
    assert len({span.attributes["trace_id"] for span in tool_spans}) == 1
    assert all(span.attributes["session_id"] == session.session_id for span in tool_spans)
    assert all(span.attributes["tool_name"] == "read_health" for span in tool_spans)


def test_trace_spans_are_persisted_as_safe_jsonl(tmp_path):
    import json
    from antisentinel.tracing.telemetry import Telemetry

    telemetry = Telemetry(trace_path=tmp_path / "traces.jsonl")
    with telemetry.span("session.run", request_id="req-1", session_id="session-1"):
        pass

    record = json.loads((tmp_path / "traces.jsonl").read_text().splitlines()[0])
    assert record["name"] == "session.run"
    assert record["attributes"]["request_id"] == "req-1"
    assert "api_key" not in record["attributes"]


def test_trace_spans_are_persisted_to_sqlite_without_secrets(tmp_path):
    import json
    from antisentinel.persistence.sqlite_database import SQLiteDatabase
    from antisentinel.tracing.telemetry import Telemetry, TraceContext

    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    telemetry = Telemetry(database=database)
    context = TraceContext(trace_id="trace-1", session_id="session-1", turn_id="turn-1", request_id="request-1")
    with telemetry.span("model.complete", context=context, api_key="must-not-persist", input_tokens=12):
        pass

    span = database.query("SELECT trace_id,session_id,turn_id,name,attributes_json FROM spans")[0]
    attributes = json.loads(span["attributes_json"])
    finished = next(item for item in telemetry.finished_spans if item.name == "model.complete")
    assert (span["trace_id"], span["session_id"], span["turn_id"], span["name"]) == (str(finished.context.trace_id), "session-1", "turn-1", "model.complete")
    assert attributes["input_tokens"] == 12
    assert "api_key" not in attributes
