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
