"""Unit tests for Phase B stress metrics and SoftBudget baseline replay."""

from antisentinel.evaluation.prd002b_phase_b_stress import (
    base_ids,
    measure_pack,
    pack_phase_a_soft,
    pack_phase_b,
)
from antisentinel.worker.runtime.budget import ContextBudget
from antisentinel.worker.runtime.pack import PackInput, SoftBudget


def _large_sources(n=5, size=20_000):
    from antisentinel.code_map.source_context import SourceContextSlice

    incident, _, _ = base_ids()
    return [
        SourceContextSlice(
            str(incident.incident_id),
            f"ev-{i}",
            "repo",
            "snap",
            "sha",
            f"f{i}.py",
            "x" * size,
            f"h{i}",
        )
        for i in range(n)
    ]


def test_phase_a_soft_silently_breaks_oversized_source():
    incident, session, turn = base_ids()
    result = pack_phase_a_soft(
        PackInput(
            incident=incident,
            session=session,
            turn=turn,
            working_set=None,
            tools=[],
            source_context=_large_sources(5, 20_000),
            budget=ContextBudget(max_context_tokens=8192, strict=False),
            soft_budget=SoftBudget(max_source_slices=4, max_source_bytes=32 * 1024),
            system_override="sys",
        )
    )
    assert any(item.reason.startswith("silent_break") for item in result.report.dropped)
    assert result.report.truncated_slices == []


def test_phase_b_marks_or_drops_without_silent_break():
    incident, session, turn = base_ids()
    result = pack_phase_b(
        PackInput(
            incident=incident,
            session=session,
            turn=turn,
            working_set=None,
            tools=[],
            source_context=_large_sources(5, 20_000),
            budget=ContextBudget(max_context_tokens=8192, strict=True),
            system_override="sys",
        )
    )
    assert result.report.within_budget
    assert not any(item.reason.startswith("silent_break") for item in result.report.dropped)
    assert result.report.truncated_slices or result.report.dropped


def test_effective_payload_ratio_includes_source_and_tools():
    incident, session, turn = base_ids()
    result = pack_phase_b(
        PackInput(
            incident=incident,
            session=session,
            turn=turn,
            working_set=None,
            tools=[{"name": "tool-0", "description": "core", "argument_schema": {}}],
            source_context=_large_sources(3, 8_000),
            budget=ContextBudget(max_context_tokens=8192, strict=True),
            system_override="sys",
        )
    )
    metrics = measure_pack(
        label="B",
        result=result,
        early_marker="nope",
        recent_turn_limit=3,
        core_tool_names=["tool-0"],
    )
    # With source counted, ratio should be high (not the old ~0.02 artifact).
    assert metrics.effective_payload_ratio > 0.5
    assert metrics.within_budget


def test_wire_estimate_exceeds_structured_for_context_roles():
    from antisentinel.worker.runtime.budget import estimate_json, estimate_model_request

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "source_context", "slices": [{"path": "a.py", "content": "x" * 300}]},
    ]
    structured = sum(estimate_json(message) for message in messages)
    wire = estimate_model_request(messages, [])
    assert wire > structured


def test_source_refill_raises_budget_fill_under_pressure():
    incident, session, turn = base_ids()
    result = pack_phase_b(
        PackInput(
            incident=incident,
            session=session,
            turn=turn,
            working_set=None,
            tools=[],
            source_context=_large_sources(8, 12_000),
            budget=ContextBudget(max_context_tokens=32_000, strict=True, target_fill_ratio=0.75),
            system_override="sys",
        )
    )
    metrics = measure_pack(
        label="B",
        result=result,
        early_marker="nope",
        recent_turn_limit=3,
        core_tool_names=[],
    )
    assert result.report.within_budget
    assert metrics.budget_fill >= 0.50
    assert metrics.useful_in_budget > 1000


def test_measure_pack_bill_coverage_and_budget_fill():
    incident, session, turn = base_ids()
    result = pack_phase_b(
        PackInput(
            incident=incident,
            session=session,
            turn=turn,
            working_set=None,
            tools=[{"name": "tool-0", "description": "core", "argument_schema": {}}],
            budget=ContextBudget(max_context_tokens=8192),
            system_override="sys",
        )
    )
    metrics = measure_pack(
        label="B",
        result=result,
        early_marker="nope",
        recent_turn_limit=3,
        core_tool_names=["tool-0"],
    )
    assert metrics.bill_fields_present is True
    assert metrics.tools_core_intact is True
    assert metrics.within_budget is True
    assert 0 < metrics.budget_fill <= 1.0
    assert metrics.useful_in_budget == metrics.useful_tokens
