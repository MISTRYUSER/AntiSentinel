"""Pack under ContextBudget: truncation markers and block quotas."""

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.turn import Turn
from antisentinel.memory.models import MemoryContextView
from antisentinel.worker.runtime.budget import ContextBudget, TRUNCATION_MARKER
from antisentinel.worker.runtime.pack import PackInput, pack_context


def _base_input(**kwargs):
    incident = Incident.create(title="pack", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["w1"])
    turn = Turn.create(session_id=session.session_id)
    base = dict(
        incident=incident,
        session=session,
        turn=turn,
        working_set=None,
        tools=[],
        prior_turns=[],
        task_results=[],
    )
    base.update(kwargs)
    return PackInput(**base)


def test_pack_memory_truncates_with_marker():
    # Tight global budget forces memory degrade (no hard per-channel ceiling).
    budget = ContextBudget(max_context_tokens=1_500, strict=True)
    view = MemoryContextView(session_id="s", digest="诊断摘要" * 800, memory_ids=("m1",), evidence_refs=("e1",))
    result = pack_context(_base_input(memory_context=view, budget=budget, system_override="sys"))
    memory = next(message for message in result.messages if message["role"] == "memory")
    assert TRUNCATION_MARKER.strip() in memory["session_digest"] or result.report.blocks["memory"].truncated
    assert result.report.within_budget
    assert result.report.degrade_steps  # memory should be degraded


def test_pack_skill_catalog_degrades_before_dropping_message():
    budget = ContextBudget(max_context_tokens=2_000, strict=True)
    catalog = {
        "available_skills": [
            {"skill_id": f"s{i}", "name": f"skill-{i}", "description": "详细说明" * 200}
            for i in range(8)
        ]
    }
    result = pack_context(_base_input(skill_context=catalog, budget=budget, system_override="sys"))
    assert any(message.get("role") == "skill_catalog" for message in result.messages)
    assert result.report.blocks["skill"].truncated or result.report.dropped or result.report.degrade_steps
    assert result.report.within_budget


def test_pack_tools_keeps_core_tools():
    budget = ContextBudget(max_context_tokens=12_000, tools_core_limit=2, tools_min_share=0.08)
    tools = [
        {"name": f"tool-{i}", "description": "desc-" + ("x" * 500), "argument_schema": {"type": "object"}}
        for i in range(6)
    ]
    result = pack_context(_base_input(tools=tools, budget=budget, system_override="sys"))
    names = [item["name"] for item in result.tools]
    assert names[:2] == ["tool-0", "tool-1"]
    assert result.report.within_budget


def test_pack_source_pointer_stub_is_cheap():
    from antisentinel.code_map.source_context import SourceContextSlice

    budget = ContextBudget(max_context_tokens=8_192, strict=True)
    incident = Incident.create(title="p", source="t")
    pointers = [
        SourceContextSlice(str(incident.incident_id), f"e{i}", "repo", "snap", "sha", f"f{i}.py", "", "h")
        for i in range(3)
    ]
    result = pack_context(_base_input(source_context=pointers, budget=budget, system_override="sys"))
    source = next(message for message in result.messages if message["role"] == "source_context")
    assert all(item.get("pointer") is True for item in source["slices"])
    assert result.report.blocks["source"].used < 800
    assert result.report.within_budget


def test_pack_source_pressure_keeps_tools_and_skill():
    """S2-style: many large files must not wipe tools/skill structure."""
    from antisentinel.code_map.source_context import SourceContextSlice

    incident = Incident.create(title="pack", source="test")
    sources = [
        SourceContextSlice(
            str(incident.incident_id),
            f"ev-{i}",
            "repo",
            "snap",
            "sha",
            f"f{i}.py",
            "x" * 25_000,
            f"h{i}",
        )
        for i in range(10)
    ]
    budget = ContextBudget(max_context_tokens=8_192, tools_core_limit=2, strict=True, target_fill_ratio=0.65)
    tools = [
        {"name": f"tool-{i}", "description": "core-desc-" + ("y" * 200), "argument_schema": {"type": "object"}}
        for i in range(4)
    ]
    skill = {"selected_skill": {"skill_id": "s1", "name": "diag", "instructions": "任务目标：定位空指针。详细步骤：" + ("z" * 2000)}}
    result = pack_context(
        _base_input(
            source_context=sources,
            tools=tools,
            skill_context=skill,
            budget=budget,
            system_override="sys",
        )
    )
    assert result.report.within_budget
    assert result.report.packing_mode == "floors_degrade"
    assert len(result.tools) >= 2
    assert any(message.get("role") == "skill" for message in result.messages)
    skill_msg = next(message for message in result.messages if message.get("role") == "skill")
    assert "任务目标" in str(skill_msg.get("instructions") or "")
    source_used = result.report.blocks["source"].used
    assert source_used <= budget.source_hard_max()
    assert result.report.blocks["tools"].used >= 1
    assert result.report.blocks["skill"].used >= 1


def test_pack_context_does_not_mutate_canonical_working_set():
    from antisentinel.worker.runtime.working_set import ToolEvent, TurnRecord, WorkingSet

    ws = WorkingSet(recent_turn_limit=4, max_older_digest_tokens=4096)
    for index in range(1, 6):
        ws.append_turn(
            TurnRecord(
                turn_index=index,
                plan_summary=f"plan-{index}-" + ("详细计划摘要" * 40),
                tool_events=[
                    ToolEvent(
                        f"tool_{index}",
                        f"fp{index}",
                        "succeeded",
                        f"result-summary-{index}-" + ("诊断细节" * 30),
                        [f"ev-{index}"],
                    )
                ],
                outcome=f"outcome-{index}-" + ("结果说明" * 20),
            )
        )
    before = ws.to_dict()
    budget = ContextBudget(max_context_tokens=1_800, min_recent_turns=1, recent_turn_limit=4, strict=True)
    pack_context(_base_input(working_set=ws, budget=budget, system_override="sys"))
    pack_context(_base_input(working_set=ws, budget=budget, system_override="sys"))
    assert ws.to_dict() == before


def test_pack_context_tool_history_not_duplicated_in_wire_payload():
    from antisentinel.worker.runtime.working_set import ToolEvent, TurnRecord, WorkingSet

    ws = WorkingSet(recent_turn_limit=3)
    summary = "UNIQUE_RESULT_SUMMARY_TOKEN_9f3a"
    evidence = "UNIQUE_EVIDENCE_REF_9f3a"
    ws.append_turn(
        TurnRecord(
            turn_index=1,
            plan_summary="inspect",
            tool_events=[ToolEvent("read_health", "fp1", "succeeded", summary, [evidence])],
            outcome="read_health:succeeded",
        )
    )
    result = pack_context(_base_input(working_set=ws, budget=ContextBudget(max_context_tokens=8_192), system_override="sys"))
    wire = str(result.messages)
    assert wire.count(summary) == 1
    assert wire.count(evidence) == 1
    assert not any(message.get("role") == "tool" for message in result.messages)
    user = next(message for message in result.messages if message["role"] == "user")
    events = user["working_set"]["recent_turns"][0]["tool_events"]
    assert events[0]["result_summary"] == summary
    assert evidence in events[0]["evidence_refs"]


def test_pack_source_preserves_rag_identity_fields():
    from antisentinel.code_map.source_context import SourceContextSlice

    incident = Incident.create(title="rag", source="test")
    slice_obj = SourceContextSlice(
        str(incident.incident_id),
        "ev-rag",
        "repo",
        "snap",
        "deadbeef",
        "svc/handler.py",
        "def handle():\n    return 1\n",
        "hash-rag",
        byte_start=10,
        byte_end=40,
        published_generation=2,
    )
    result = pack_context(
        _base_input(
            source_context=[slice_obj],
            budget=ContextBudget(max_context_tokens=8_192),
            system_override="sys",
        )
    )
    source = next(message for message in result.messages if message["role"] == "source_context")
    packed = source["slices"][0]
    assert packed["byte_start"] == 10
    assert packed["byte_end"] == 40
    assert packed["published_generation"] == 2
    assert "def handle" in packed["content"]
