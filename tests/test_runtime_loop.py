import pytest

from antisentinel.domain.evidence import Evidence
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.ports.model import ModelError, ModelTimeoutError
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine, RuntimeMetrics
from antisentinel.worker.runtime.checkpoint import InMemoryCheckpointStore
from antisentinel.worker.handler import WorkerHandler


def make_session():
    incident = Incident.create(title="API outage", source="monitor", summary="Requests fail")
    return incident, Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])


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


def registry_for(handler, *, approval=False):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_health",
            description="Read health",
            argument_schema={"type": "object", "properties": {"service": {"type": "string"}}, "required": ["service"]},
            handler=handler,
            requires_approval=approval,
        )
    )
    return registry


def test_model_returns_final_and_completes_session():
    incident, session = make_session()
    model = FakeModel([{"final": {"summary": "outage", "diagnosis": "backend down", "confidence": 0.8, "evidence_refs": []}}])

    result = RuntimeEngine().run(incident, session, model, registry=ToolRegistry())

    assert result.status == "completed"
    assert result.final.diagnosis == "backend down"
    assert session.status.value == "completed"
    assert any(event.type == "model.responded" for event in result.events)


def test_runtime_emits_context_built_budget_report():
    incident, session = make_session()
    model = FakeModel([{"final": {"summary": "ok", "diagnosis": "ok", "confidence": 0.9, "evidence_refs": []}}])
    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=ToolRegistry(auto_discover=False),
        config=RuntimeConfig(max_context_tokens=8192),
    )
    built = [event for event in result.events if event.type == "context.built"]
    assert built
    payload = built[0].payload
    assert payload["estimate_version"] == "utf8_ceil_div3_v1"
    assert payload["within_budget"] is True
    assert payload["total_estimated"] <= payload["max_context_tokens"]
    assert "blocks" in payload
    assert "truncated_slices" not in payload


def test_model_creates_multiple_tasks_and_task_executes_multiple_tool_calls():
    incident, session = make_session()
    calls = []
    model = FakeModel([{"tasks": [
        {"task_id": "task-1", "objective": "inspect", "tool_calls": [
            {"tool_name": "read_health", "arguments": {"service": "api"}},
            {"tool_name": "read_health", "arguments": {"service": "worker"}},
        ]},
        {"task_id": "task-2", "objective": "inspect again", "tool_calls": []},
    ]}, {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.7, "evidence_refs": []}}])
    registry = registry_for(lambda arguments: calls.append(arguments) or {"ok": True})

    result = RuntimeEngine().run(incident, session, model, registry=registry)

    assert result.status == "completed"
    assert len(calls) == 2
    assert len(session.turn_ids) == 2
    assert len(result.task_summaries) == 2
    assert "task.completed" in {event.type for event in result.events}


def test_tool_result_is_returned_to_model_as_summary_and_evidence_ref():
    incident, session = make_session()
    evidence = Evidence.create(kind="log", content_ref="vault://secret-log", source="api")
    model = FakeModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        {"final": {"summary": "confirmed", "diagnosis": "backend down", "confidence": 0.95, "evidence_refs": [{"evidence_id": evidence.evidence_id, "role": "supporting"}]}},
    ])
    registry = registry_for(lambda arguments: ToolExecutionResult(
        status="succeeded", result={"raw": "DO_NOT_LEAK"}, result_summary="health endpoint returned 503", evidence=evidence
    ))

    result = RuntimeEngine().run(incident, session, model, registry=registry)

    assert result.status == "completed"
    assert result.evidence_refs[0].evidence_id == evidence.evidence_id
    request_text = str(model.requests[1].messages)
    assert "health endpoint returned 503" in request_text
    assert str(evidence.evidence_id) in request_text
    assert "DO_NOT_LEAK" not in request_text
    assert "vault://secret-log" not in request_text


def test_invalid_model_output_does_not_execute_tool():
    incident, session = make_session()
    calls = []
    model = FakeModel([{"tasks": [{"task_id": "task-1", "objective": "bad", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": object()}}]}]}])

    result = RuntimeEngine().run(incident, session, model, registry=registry_for(lambda arguments: calls.append(arguments)))

    assert result.status == "failed"
    assert result.error["code"] == "invalid_model_output"
    assert calls == []


def test_tool_failure_is_returned_to_model():
    incident, session = make_session()
    model = FakeModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        {"final": {"summary": "failed check", "diagnosis": "unknown", "confidence": 0.2, "evidence_refs": []}},
    ])
    registry = registry_for(lambda arguments: (_ for _ in ()).throw(RuntimeError("backend unavailable")))

    result = RuntimeEngine().run(incident, session, model, registry=registry)

    assert result.status == "completed"
    assert "backend unavailable" in str(model.requests[1].messages)
    assert "task.failed" in {event.type for event in result.events}


def test_failed_model_keeps_previous_task_summary_and_evidence_refs():
    incident, session = make_session()
    evidence = Evidence.create(kind="log", content_ref="vault://log", source="api")
    model = FakeModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        ModelError("process interrupted"),
    ])
    registry = registry_for(lambda arguments: ToolExecutionResult(
        status="succeeded", result={"ok": True}, result_summary="health checked", evidence=evidence
    ))

    result = RuntimeEngine().run(incident, session, model, registry=registry)

    assert result.status == "failed"
    assert result.task_summaries[0]["summary"] == "health checked"
    assert result.evidence_refs[0].evidence_id == evidence.evidence_id


def test_approval_required_pauses_loop_without_calling_tool():
    incident, session = make_session()
    calls = []
    model = FakeModel([{"tasks": [{"task_id": "task-1", "objective": "change", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]}])

    result = RuntimeEngine().run(incident, session, model, registry=registry_for(lambda arguments: calls.append(arguments), approval=True))

    assert result.status == "waiting_approval"
    assert result.error["code"] == "approval_required"
    assert calls == []
    assert session.status.value == "waiting"


def test_max_turns_stops_session():
    incident, session = make_session()
    model = FakeModel([{"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": []}]}] * 2)

    result = RuntimeEngine().run(incident, session, model, registry=ToolRegistry(), config=RuntimeConfig(max_turns=2))

    assert result.status == "failed"
    assert result.error["code"] == "max_turns_exceeded"
    assert "model.failed" not in {event.type for event in result.events}


def test_repeated_cached_tool_calls_stop_as_non_convergent():
    incident, session = make_session()
    plan = {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]}
    model = FakeModel([plan, plan, plan])

    result = RuntimeEngine().run(
        incident, session, model,
        registry=registry_for(lambda arguments: {"ok": True}),
        config=RuntimeConfig(max_turns=3),
    )

    assert result.status == "failed"
    assert result.error["code"] == "model_non_convergent"


def test_duplicate_tool_call_is_returned_once_then_model_can_finalize():
    incident, session = make_session()
    calls = []
    plan = {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]}
    model = FakeModel([plan, plan, {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.8, "evidence_refs": []}}])
    result = RuntimeEngine().run(incident, session, model, registry=registry_for(lambda arguments: calls.append(arguments) or {"ok": True}), config=RuntimeConfig(max_turns=4))
    assert result.status == "completed"
    assert len(calls) == 1
    assert any(attempt.error and attempt.error["code"] == "duplicate_tool_call" for attempt in result.attempts)


def test_total_tool_call_budget_stops_distinct_followup_calls():
    incident, session = make_session()
    calls = []
    model = FakeModel([
        {"tasks": [{"task_id": "one", "objective": "first", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        {"tasks": [{"task_id": "two", "objective": "second", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "worker"}}]}]},
    ])
    result = RuntimeEngine().run(incident, session, model, registry=registry_for(lambda args: calls.append(args) or {"ok": True}), config=RuntimeConfig(max_total_tool_calls=1))
    assert result.status == "failed"
    assert result.error["code"] == "max_total_tool_calls_exceeded"
    assert len(calls) == 1


def test_model_timeout_fails_session():
    incident, session = make_session()
    model = FakeModel([ModelTimeoutError("timed out")])

    result = RuntimeEngine().run(incident, session, model, registry=ToolRegistry())

    assert result.status == "failed"
    assert result.error["code"] == "model_timeout"
    assert any(event.type == "model.failed" for event in result.events)


def test_runtime_metrics_collect_model_tool_turn_and_task_data():
    incident, session = make_session()
    metrics = RuntimeMetrics()
    model = FakeModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.8, "evidence_refs": []}},
    ])

    RuntimeEngine().run(incident, session, model, registry=registry_for(lambda arguments: {"ok": True}), metrics=metrics)

    assert metrics.counters == {
        "turns": 2,
        "model_calls": 2,
        "model_successes": 2,
        "tasks": 1,
        "tool_calls": 1,
        "tool_successes": 1,
    }
    assert len(metrics.durations_ms["model_latency_ms"]) == 2
    assert len(metrics.durations_ms["tool_latency_ms"]) == 1


def test_resume_does_not_execute_a_successful_tool_again():
    incident, session = make_session()
    calls = []
    tasks = {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]}
    store = InMemoryCheckpointStore()
    first_model = FakeModel([tasks, ModelError("process interrupted")])
    first = RuntimeEngine().run(
        incident, session, first_model,
        registry=registry_for(lambda arguments: calls.append(arguments) or {"ok": True}),
        checkpoint_store=store,
    )
    assert first.status == "failed"
    assert len(calls) == 1
    assert store.load(session.session_id) is not None

    second_model = FakeModel([tasks, {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.8, "evidence_refs": []}}])
    second = RuntimeEngine().run(
        incident, session, second_model,
        registry=registry_for(lambda arguments: calls.append(arguments) or {"ok": True}),
        checkpoint_store=store,
        resume=True,
    )

    assert second.status == "completed"
    assert len(calls) == 1
    assert second.turn_count == 3


def test_worker_handler_runs_full_model_tool_model_final_chain():
    incident, session = make_session()
    evidence = Evidence.create(kind="log", content_ref="vault://log", source="api")
    model = FakeModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": [{"evidence_id": evidence.evidence_id}]}},
    ])

    result = WorkerHandler().handle(
        incident,
        session,
        model,
        registry=registry_for(lambda arguments: ToolExecutionResult(status="succeeded", result={}, evidence=evidence)),
    )

    assert result.status == "completed"
    assert session.status.value == "completed"
    assert {event.type for event in result.events} >= {
        "turn.started", "model.called", "model.responded", "tool_call.requested",
        "tool_call.result_received", "task.completed", "session.completed",
    }


def test_runtime_events_have_all_available_correlation_ids():
    incident, session = make_session()
    evidence = Evidence.create(kind="log", content_ref="vault://log", source="api")
    model = FakeModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        {"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.8, "evidence_refs": [{"evidence_id": evidence.evidence_id}]}},
    ])
    result = RuntimeEngine().run(
        incident, session, model,
        registry=registry_for(lambda arguments: ToolExecutionResult(status="succeeded", result={}, evidence=evidence)),
    )

    runtime_events = [event for event in result.events if event.aggregate_type == "Runtime"]
    assert {event.type for event in runtime_events} >= {
        "turn.started", "model.called", "model.responded", "tool_call.result_received", "task.completed"
    }
    for event in runtime_events:
        assert event.related_ids["incident_id"] == str(incident.incident_id)
        assert event.related_ids["session_id"] == str(session.session_id)
        assert event.related_ids["turn_id"]


def test_multi_turn_model_requests_retain_earlier_tool_summaries():
    """PRD-002B §7.1: turn N still sees turn-1 tool result_summary (not last-turn-only)."""
    incident, session = make_session()
    registry = ToolRegistry(auto_discover=False)

    def make_tool(name: str, summary: str):
        registry.register(
            ToolDefinition(
                name=name,
                description=name,
                argument_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                handler=lambda arguments, summary=summary: ToolExecutionResult(
                    status="succeeded",
                    result={"raw": f"SECRET-{summary}"},
                    result_summary=summary,
                ),
            )
        )

    make_tool("read_logs", "error code E42 from turn1")
    make_tool("read_metrics", "cpu saturation turn2")
    make_tool("read_traces", "span drop turn3")

    model = FakeModel([
        {"tasks": [{"task_id": "t1", "objective": "logs", "tool_calls": [{"tool_name": "read_logs", "arguments": {"q": "1"}}]}]},
        {"tasks": [{"task_id": "t2", "objective": "metrics", "tool_calls": [{"tool_name": "read_metrics", "arguments": {"q": "2"}}]}]},
        {"tasks": [{"task_id": "t3", "objective": "traces", "tool_calls": [{"tool_name": "read_traces", "arguments": {"q": "3"}}]}]},
        {"final": {"summary": "done", "diagnosis": "E42 root cause", "confidence": 0.9, "evidence_refs": []}},
    ])

    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry,
        config=RuntimeConfig(max_turns=4, recent_turn_limit=3),
    )

    assert result.status == "completed"
    assert len(model.requests) == 4
    fourth = str(model.requests[3].messages)
    assert "error code E42 from turn1" in fourth
    assert "cpu saturation turn2" in fourth
    assert "span drop turn3" in fourth
    assert "SECRET-" not in fourth
    # Turn outcome must not be the hollow placeholder alone in prior history.
    assert "tool calls completed" not in fourth or "read_logs" in fourth


def test_working_set_off_only_keeps_last_turn_tool_summary():
    """Comparison baseline: enable_working_set=False restores last-turn-only behavior."""
    incident, session = make_session()
    registry = ToolRegistry(auto_discover=False)
    for name, summary in (
        ("read_logs", "error code E42 from turn1"),
        ("read_metrics", "cpu saturation turn2"),
        ("read_traces", "span drop turn3"),
    ):
        registry.register(
            ToolDefinition(
                name=name,
                description=name,
                argument_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                handler=lambda arguments, summary=summary: ToolExecutionResult(
                    status="succeeded", result={"raw": "x"}, result_summary=summary
                ),
            )
        )
    model = FakeModel([
        {"tasks": [{"task_id": "t1", "objective": "logs", "tool_calls": [{"tool_name": "read_logs", "arguments": {"q": "1"}}]}]},
        {"tasks": [{"task_id": "t2", "objective": "metrics", "tool_calls": [{"tool_name": "read_metrics", "arguments": {"q": "2"}}]}]},
        {"tasks": [{"task_id": "t3", "objective": "traces", "tool_calls": [{"tool_name": "read_traces", "arguments": {"q": "3"}}]}]},
        {"final": {"summary": "done", "diagnosis": "E42", "confidence": 0.9, "evidence_refs": []}},
    ])
    result = RuntimeEngine().run(
        incident, session, model, registry=registry,
        config=RuntimeConfig(max_turns=4, enable_working_set=False),
    )
    assert result.status == "completed"
    fourth = str(model.requests[3].messages)
    assert "span drop turn3" in fourth
    assert "error code E42 from turn1" not in fourth


def test_checkpoint_persists_working_set_for_resume_continuity():
    incident, session = make_session()
    store = InMemoryCheckpointStore()
    model = FakeModel([
        {"tasks": [{"task_id": "task-1", "objective": "inspect", "tool_calls": [{"tool_name": "read_health", "arguments": {"service": "api"}}]}]},
        ModelError("process interrupted"),
    ])
    RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry_for(
            lambda arguments: ToolExecutionResult(
                status="succeeded",
                result={"raw": "secret"},
                result_summary="health endpoint returned 503",
            )
        ),
        checkpoint_store=store,
    )
    snapshot = store.load(session.session_id)
    assert snapshot is not None
    assert snapshot.working_set is not None
    assert snapshot.working_set["recent_turns"]
    assert "503" in str(snapshot.working_set)
    assert "secret" not in str(snapshot.working_set)