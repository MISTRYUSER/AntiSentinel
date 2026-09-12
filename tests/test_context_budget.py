"""ContextBudget floors / soft caps / estimate / preflight (PRD-002B Phase B)."""

import pytest

from antisentinel.worker.runtime.budget import (
    ESTIMATE_VERSION,
    ContextBudget,
    apply_truncation_marker,
    calibrate_estimate_vs_usage,
    default_budget,
    estimate_json,
    estimate_tokens,
)
from antisentinel.worker.runtime.model_profile import resolve_max_context_tokens


def floor_share(max_tokens: int, share: float) -> int:
    from math import floor

    return floor(max_tokens * share)


def test_estimate_tokens_utf8_ceil_div3():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1  # 3 bytes
    assert estimate_tokens("abcd") == 2  # 4 bytes → ceil 4/3
    assert estimate_tokens("中") == 1  # 3 bytes UTF-8
    assert estimate_tokens("中文") == 2  # 6 bytes
    with pytest.raises(ValueError):
        estimate_tokens("x", version="other")


def test_wire_estimate_used_for_request_totals():
    from antisentinel.worker.runtime.budget import estimate_json, estimate_model_request

    messages = [{"role": "memory", "session_digest": "摘要" * 20, "evidence_refs": ["e1"]}]
    assert estimate_model_request(messages, []) > estimate_json(messages[0])


def test_estimate_json_stable():
    a = estimate_json({"b": 1, "a": 2})
    b = estimate_json({"a": 2, "b": 1})
    assert a == b == estimate_tokens('{"a":2,"b":1}')


def test_floors_and_soft_caps_not_hard_partitions():
    budget = ContextBudget(max_context_tokens=10_000, tools_min_share=0.08)
    floors = budget.floors()
    caps = budget.soft_caps()
    assert floors["tools"] == floor_share(10_000, 0.08)
    assert floors["system"] == floor_share(10_000, 0.05)
    assert floors["source"] == 0
    assert caps["source"] == floor_share(10_000, 0.25)
    assert caps["tools"] >= floors["tools"]
    # Soft caps are preferences; sum may exceed max (not exclusive slices).
    assert budget.allocate() == caps
    assert budget.source_hard_max() == 10_000 - sum(
        floors[name] for name in ("system", "working", "memory", "skill", "tools")
    )


def test_tools_floor_independent_of_soft_prefs():
    budget = ContextBudget(
        max_context_tokens=1000,
        shares={"system": 0.05, "working": 0.45, "memory": 0.15, "source": 0.40, "skill": 0.15, "tools": 0.05},
        tools_min_share=0.08,
    )
    floors = budget.floors()
    assert floors["tools"] >= 80
    assert floors["system"] == 50


def test_min_recent_turns_at_least_one():
    with pytest.raises(ValueError):
        ContextBudget(max_context_tokens=1000, min_recent_turns=0)
    budget = ContextBudget(max_context_tokens=1000, recent_turn_limit=3, min_recent_turns=1)
    assert budget.min_recent_turns == 1


def test_preflight_system_rejects_oversized_prompt():
    budget = ContextBudget(max_context_tokens=300)  # system floor = 15
    with pytest.raises(ValueError, match="system prompt exceeds"):
        budget.preflight_system("x" * 200)  # ~67 tokens > 15
    small = ContextBudget(max_context_tokens=10_000)
    small.preflight_system("short system")


def test_window_radius_by_suffix():
    budget = default_budget(max_context_tokens=8192, fake=True)
    assert budget.window_radius_for_path("a.py") == 40
    assert budget.window_radius_for_path("data.json") == 20
    assert budget.window_radius_for_path("README.md") == 30
    assert budget.window_radius_for_path("unknown.xyz") == 40


def test_truncation_marker_is_model_readable():
    assert apply_truncation_marker("hello") == "hello [truncated]"
    assert apply_truncation_marker("hello [truncated]") == "hello [truncated]"
    assert "[truncated]" in apply_truncation_marker("")


def test_budget_report_event_payload_has_no_body_fields():
    report = ContextBudget(max_context_tokens=8192).empty_report()
    report.total_estimated = 100
    report.within_budget = True
    payload = report.to_event_payload()
    assert payload["estimate_version"] == ESTIMATE_VERSION
    assert payload["packing_mode"] == "floors_degrade"
    assert "blocks" in payload
    assert "truncated_slices" not in payload
    assert payload["truncated_slice_count"] == 0


def test_calibrate_estimate_vs_usage():
    sample = calibrate_estimate_vs_usage(estimated=1200, input_tokens=1000)
    assert sample["error"] == 200
    assert sample["ratio"] == 1.2
    assert sample["estimate_version"] == ESTIMATE_VERSION


def test_resolve_max_context_tokens_from_model():
    assert resolve_max_context_tokens(override=12_000) == 12_000
    auto = resolve_max_context_tokens(model="deepseek-v4-flash")
    # 65536 * 0.85 - 4096
    assert auto == int(65536 * 0.85) - 4096
    gpt = resolve_max_context_tokens(model="gpt-4o-mini")
    assert gpt == int(128_000 * 0.85) - 4096
    fallback = resolve_max_context_tokens(model="unknown-model-xyz")
    assert fallback == int(32_000 * 0.85) - 4096
