from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.turn import Turn
from antisentinel.worker.runtime.context import ContextBuilder
from antisentinel.worker.runtime.messages import build_system_message, build_task_results_message


def test_context_contains_runtime_metadata_and_redacts_raw_results():
    incident = Incident.create(title="API outage", source="alert", summary="Requests failing")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    turn = Turn.create(session_id=session.session_id)
    request = ContextBuilder().build(
        incident,
        session,
        turn,
        prior_turns=[],
        task_results=[
            {
                "task_id": "task-1",
                "status": "succeeded",
                "summary": "Health endpoint returned 503",
                "result": {"raw_log": "SECRET_RAW_LOG"},
                "evidence_refs": [{"evidence_id": "ev-1", "role": "supporting"}],
            }
        ],
        tools=[{"name": "read_health", "argument_schema": {"type": "object"}}],
    )

    text = str(request.messages)
    assert str(incident.incident_id) in text
    assert "API outage" in text
    assert "Health endpoint returned 503" in text
    assert "ev-1" in text
    assert "SECRET_RAW_LOG" not in text
    assert request.tools[0]["name"] == "read_health"


def test_message_builders_have_stable_roles_and_only_summary_fields():
    message = build_system_message()
    assert message["role"] == "system"
    assert "简体中文" in message["content"]
    assert '"final"' in message["content"]
    assert '"tasks"' in message["content"]
    message = build_task_results_message(
        [{"task_id": "task-1", "status": "succeeded", "summary": "done", "result": "secret"}]
    )
    assert message["role"] == "tool"
    assert message["task_results"] == [
        {"task_id": "task-1", "status": "succeeded", "summary": "done", "evidence_refs": []}
    ]
    assert "secret" not in str(message)


def test_context_includes_prior_turn_summaries():
    incident = Incident.create(title="Queue lag", source="monitor")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    previous = Turn.create(session_id=session.session_id, context_summary="Initial context")
    current = Turn.create(session_id=session.session_id)

    request = ContextBuilder().build(
        incident,
        session,
        current,
        prior_turns=[previous],
        task_results=[],
        tools=[],
    )

    assert "Initial context" in str(request.messages)


def test_runtime_loop_injects_memory_context_from_provider():
    from antisentinel.domain.incident import Incident
    from antisentinel.domain.session import Session
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeEngine, RuntimeConfig
    from antisentinel.memory.models import MemoryContextView

    incident = Incident.create(title="memory runtime", source="operator")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])
    model = FakeProviderModel([{"final": {"summary": "收到", "diagnosis": "正常", "confidence": 0.9, "evidence_refs": []}}])
    captured = []

    def provider(incident_value, session_value, turn_value):
        return MemoryContextView(session_id=session_value.session_id, digest="先看日志", memory_ids=("memory-1",), evidence_refs=("evidence-1",))

    result = RuntimeEngine().run(
        incident, session, model, registry=ToolRegistry(auto_discover=False), config=RuntimeConfig(),
        memory_context_provider=lambda i, s, t: (captured.append(provider(i, s, t)) or captured[-1]),
    )

    assert result.status == "completed"
    assert captured[0].digest == "先看日志"
