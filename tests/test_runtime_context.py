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


def test_context_includes_at_most_four_hash_verified_source_slices_with_32k_budget():
    from antisentinel.code_map.source_context import SourceContextSlice

    incident = Incident.create(title="Source", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    turn = Turn.create(session_id=session.session_id)
    slices = [
        SourceContextSlice(str(incident.incident_id), f"ev-{index}", "repo-a", "snap", "commit", "a.py", "x" * 8_000, "hash")
        for index in range(5)
    ]

    request = ContextBuilder().build(incident, session, turn, prior_turns=[], task_results=[], tools=[], source_context=slices)

    source_message = next(message for message in request.messages if message["role"] == "source_context")
    assert len(source_message["slices"]) == 4
    assert sum(len(item["content"]) for item in source_message["slices"]) <= 32 * 1024


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


def test_runtime_loop_reinjects_verified_source_context_after_source_tool_call():
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.domain.evidence import Evidence
    from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    incident = Incident.create(title="source", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    evidence = Evidence.create(kind="source_code", content_ref="code-map://repo/snap/chunk", content_hash="a" * 64, metadata={})
    registry = ToolRegistry(auto_discover=False)
    registry.register(ToolDefinition(
        name="code_map.read_source", description="source", argument_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        handler=lambda _: ToolExecutionResult(status="succeeded", result={"source_context": {"incident_id": str(incident.incident_id), "evidence_id": str(evidence.evidence_id), "repository_id": "repo", "snapshot_id": "snap", "commit_sha": "commit", "path": "a.py", "content": "def f(): pass\n", "content_hash": "a" * 64}}, evidence=evidence),
    ))
    model = FakeProviderModel([
        {"tasks": [{"task_id": "source", "objective": "read", "tool_calls": [{"tool_name": "code_map.read_source", "arguments": {}}]}]},
        {"final": {"summary": "done", "diagnosis": "source read", "confidence": 1.0, "evidence_refs": [str(evidence.evidence_id)]}},
    ])

    result = RuntimeEngine().run(incident, session, model, registry=registry, config=RuntimeConfig(max_turns=2))

    assert result.status == "completed"
    assert any(message["role"] == "source_context" and message["slices"][0]["evidence_id"] == str(evidence.evidence_id) for message in model.requests[1].messages)


def test_runtime_resume_rehydrates_checkpoint_source_references_before_model_call():
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.code_map.source_context import SourceContextSlice
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.checkpoint import InMemoryCheckpointStore, RuntimeSnapshot
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    incident = Incident.create(title="resume", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    checkpoint = RuntimeSnapshot(session_id=str(session.session_id), incident=incident.to_dict(), session=session.to_dict(), turns=[], tasks=[], tool_calls=[], attempts=[], messages=[], turn_count=0, current_task_index=None, current_tool_call_index=None, successful_tool_call_ids=(), last_error=None, source_context_refs=[{"evidence_id": "evidence-1"}])
    store = InMemoryCheckpointStore()
    store.save(checkpoint)
    model = FakeProviderModel([{"final": {"summary": "done", "diagnosis": "resumed", "confidence": 1.0, "evidence_refs": []}}])

    result = RuntimeEngine().run(incident, session, model, registry=ToolRegistry(auto_discover=False), config=RuntimeConfig(max_turns=1), checkpoint_store=store, resume=True, source_context_rehydrator=lambda refs: [SourceContextSlice(str(incident.incident_id), "evidence-1", "repo", "snap", "commit", "a.py", "def f(): pass\n", "a" * 64)])

    assert result.status == "completed"
    assert any(message["role"] == "source_context" for message in model.requests[0].messages)
