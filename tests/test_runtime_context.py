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


def test_context_source_slices_respect_cap_and_token_budget():
    from antisentinel.code_map.source_context import SourceContextSlice
    from antisentinel.worker.runtime.budget import ContextBudget

    incident = Incident.create(title="Source", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    turn = Turn.create(session_id=session.session_id)
    slices = [
        SourceContextSlice(str(incident.incident_id), f"ev-{index}", "repo-a", "snap", "commit", f"a{index}.py", "x" * 8_000, "hash")
        for index in range(5)
    ]
    reports = []
    budget = ContextBudget(max_context_tokens=8192, strict=True)
    request = ContextBuilder().build(
        incident,
        session,
        turn,
        prior_turns=[],
        task_results=[],
        tools=[],
        source_context=slices,
        budget=budget,
        on_budget_report=reports.append,
    )

    source_messages = [message for message in request.messages if message["role"] == "source_context"]
    assert source_messages, "expected source_context message"
    packed = source_messages[0]["slices"]
    assert len(packed) <= 4
    assert reports and reports[0].within_budget
    assert any(item.reason == "slice_cap" for item in reports[0].dropped) or len(packed) < 5
    assert any(item.get("truncated") for item in packed) or reports[0].blocks["source"].truncated or reports[0].blocks["source"].dropped > 0


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
    # Ephemeral TTL=1: next turn still sees body once.
    source_msg = next(message for message in model.requests[1].messages if message["role"] == "source_context")
    assert source_msg["slices"][0]["evidence_id"] == str(evidence.evidence_id)
    assert "def f()" in (source_msg["slices"][0].get("content") or "")


def test_runtime_resume_rehydrates_checkpoint_source_references_before_model_call():
    from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
    from antisentinel.code_map.source_context import SourceContextSlice
    from antisentinel.tools.registry import ToolRegistry
    from antisentinel.worker.runtime.checkpoint import InMemoryCheckpointStore, RuntimeSnapshot
    from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine

    incident = Incident.create(title="resume", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    checkpoint = RuntimeSnapshot(session_id=str(session.session_id), incident=incident.to_dict(), session=session.to_dict(), turns=[], tasks=[], tool_calls=[], attempts=[], messages=[], turn_count=0, current_task_index=None, current_tool_call_index=None, successful_tool_call_ids=(), last_error=None, source_context_refs=[{"evidence_id": "evidence-1", "repository_id": "repo", "snapshot_id": "snap", "commit_sha": "commit", "path": "a.py", "content_hash": "a" * 64}])
    store = InMemoryCheckpointStore()
    store.save(checkpoint)
    model = FakeProviderModel([{"final": {"summary": "done", "diagnosis": "resumed", "confidence": 1.0, "evidence_refs": []}}])

    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=ToolRegistry(auto_discover=False),
        config=RuntimeConfig(max_turns=1),
        checkpoint_store=store,
        resume=True,
        source_context_rehydrator=lambda refs: [
            SourceContextSlice(str(incident.incident_id), "evidence-1", "repo", "snap", "commit", "a.py", "def f(): pass\n", "a" * 64)
        ],
    )

    assert result.status == "completed"
    # CodeMap-first ephemeral: resume packs pointer stub, not rehydrated body.
    source_msg = next(message for message in model.requests[0].messages if message["role"] == "source_context")
    assert source_msg["slices"][0]["evidence_id"] == "evidence-1"
    assert source_msg["slices"][0].get("pointer") is True
    assert not (source_msg["slices"][0].get("content") or "")


def test_context_includes_working_set_recent_and_older_digest():
    from antisentinel.worker.runtime.working_set import ToolEvent, TurnRecord, WorkingSet

    incident = Incident.create(title="WS", source="test", summary="s")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker-1"])
    turn = Turn.create(session_id=session.session_id)
    ws = WorkingSet(recent_turn_limit=2)
    ws.append_turn(
        TurnRecord(
            turn_index=1,
            plan_summary="early-plan",
            tool_events=[
                ToolEvent("read_logs", "fp1", "succeeded", "error code E42 in pod-a", ["ev-early"])
            ],
            outcome="read_logs:succeeded",
        )
    )
    ws.append_turn(
        TurnRecord(
            turn_index=2,
            plan_summary="mid-plan",
            tool_events=[ToolEvent("read_metrics", "fp2", "succeeded", "cpu high", ["ev-mid"])],
            outcome="read_metrics:succeeded",
        )
    )
    ws.append_turn(
        TurnRecord(
            turn_index=3,
            plan_summary="late-plan",
            tool_events=[ToolEvent("read_health", "fp3", "succeeded", "503", ["ev-late"])],
            outcome="read_health:succeeded",
        )
    )

    request = ContextBuilder().build(
        incident, session, turn, prior_turns=[], task_results=[], tools=[], working_set=ws
    )
    text = str(request.messages)
    assert "error code E42 in pod-a" in text or "turn=1" in text  # recent folded → digest or still present
    assert "ev-early" in text
    assert "503" in text
    assert "SECRET" not in text
    user = next(message for message in request.messages if message["role"] == "user")
    assert "working_set" in user
    assert user["working_set"]["older_digest"]
    # Tool history lives only inside working_set (no duplicate task_results message).
    assert not any(message.get("role") == "tool" for message in request.messages)
    recent_summaries = [
        event["result_summary"]
        for turn in user["working_set"]["recent_turns"]
        for event in turn["tool_events"]
    ]
    assert any("503" in (summary or "") for summary in recent_summaries)
    assert not any("error code E42" in (summary or "") for summary in recent_summaries)  # folded out of recent


def test_benchmark_and_production_builders_share_pack_module():
    from antisentinel.evaluation.skillsbench_session import make_benchmark_context_builder
    from antisentinel.worker.runtime.pack import pack_context

    assert ContextBuilder().build.__func__ is make_benchmark_context_builder().build.__func__
    assert pack_context is not None

