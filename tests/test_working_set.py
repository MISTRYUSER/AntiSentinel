"""Working Set accumulation and compact behavior (PRD-002B Phase A)."""

from antisentinel.worker.runtime.messages import summarize_plan, summarize_tool_result, summarize_turn_outcome
from antisentinel.worker.runtime.working_set import ToolEvent, TurnRecord, WorkingSet, create_empty


def _event(name: str, summary: str, *, status: str = "succeeded", refs: list[str] | None = None) -> ToolEvent:
    return ToolEvent(
        tool_name=name,
        args_fingerprint="abcd1234efgh5678",
        status=status,
        result_summary=summary,
        evidence_refs=refs or [],
    )


def test_append_turn_keeps_recent_and_folds_older_into_digest():
    ws = create_empty(recent_turn_limit=2)
    for index in range(1, 5):
        ws.append_turn(
            TurnRecord(
                turn_index=index,
                plan_summary=f"plan-{index}",
                tool_events=[_event(f"tool_{index}", f"summary for turn {index}", refs=[f"ev-{index}"])],
                outcome=f"outcome-{index}",
            )
        )

    assert [turn.turn_index for turn in ws.recent_turns] == [3, 4]
    assert ws.compacted_turns == 2
    assert "turn=1" in ws.older_digest
    assert "summary for turn 1" in ws.older_digest
    assert "ev-1" in ws.older_digest
    assert "turn=2" in ws.older_digest
    assert "turn=3" not in ws.older_digest


def test_export_task_results_has_summaries_not_raw_payloads():
    ws = WorkingSet(recent_turn_limit=3)
    ws.append_turn(
        TurnRecord(
            turn_index=1,
            plan_summary="inspect",
            tool_events=[_event("read_health", "health endpoint returned 503", refs=["ev-9"])],
            outcome="read_health:succeeded",
        )
    )
    exported = ws.export_task_results()
    assert exported[0]["summary"] == "health endpoint returned 503"
    assert exported[0]["evidence_refs"][0]["evidence_id"] == "ev-9"
    assert "result" not in exported[0]
    assert "raw" not in str(exported)


def test_working_set_round_trip_dict():
    ws = create_empty(recent_turn_limit=3)
    ws.append_turn(
        TurnRecord(
            turn_index=1,
            plan_summary="p",
            tool_events=[_event("t", "s")],
            outcome="t:succeeded",
        )
    )
    ws.update_sticky(active_skill={"id": "skill-a", "version": "1"})
    restored = WorkingSet.from_dict(ws.to_dict())
    assert restored.recent_turns[0].plan_summary == "p"
    assert restored.sticky.active_skill == {"id": "skill-a", "version": "1"}
    assert restored.tool_event_count() == 1


def test_compact_older_truncates_digest_by_token_budget():
    ws = create_empty(recent_turn_limit=1, max_older_digest_tokens=10_000)
    ws.append_turn(TurnRecord(turn_index=1, plan_summary="a" * 200, tool_events=[], outcome="o1"))
    ws.append_turn(TurnRecord(turn_index=2, plan_summary="b", tool_events=[], outcome="o2"))
    assert ws.older_digest
    ws.compact_older(max_digest_tokens=5)
    assert ws.digest_token_estimate() <= 5 + 1  # ellipsis / boundary slack
    assert len(ws.older_digest.encode("utf-8")) <= 5 * 3 + 6


def test_working_set_stays_bounded_after_100_appends():
    """Extreme length guard before Phase B: recent<=K, digest soft-capped."""
    import time

    ws = create_empty(recent_turn_limit=3, max_older_digest_tokens=512)
    started = time.monotonic()
    for index in range(1, 101):
        ws.append_turn(
            TurnRecord(
                turn_index=index,
                plan_summary=f"plan-{index}-" + ("x" * 40),
                tool_events=[
                    ToolEvent(
                        tool_name=f"tool_{index % 7}",
                        args_fingerprint=f"fp-{index}",
                        status="succeeded",
                        result_summary=f"summary for turn {index} " + ("y" * 80),
                        evidence_refs=[f"ev-{index}"],
                    )
                ],
                outcome=f"outcome-{index}",
            )
        )
    elapsed_ms = (time.monotonic() - started) * 1000
    assert [turn.turn_index for turn in ws.recent_turns] == [98, 99, 100]
    assert ws.compacted_turns == 97
    assert ws.digest_token_estimate() <= 512
    assert "turn=100" not in ws.older_digest  # still in recent, not digest
    assert "turn=97" in ws.older_digest or "…" in ws.older_digest
    # Must remain snappy; unbounded string concat would still finish, but keep a soft SLA.
    assert elapsed_ms < 500
    # Round-trip stays finite.
    restored = WorkingSet.from_dict(ws.to_dict())
    assert len(restored.recent_turns) == 3
    assert restored.digest_token_estimate() <= 512


def test_working_set_50_appends_keeps_recent_and_capped_digest():
    ws = create_empty(recent_turn_limit=3, max_older_digest_tokens=256)
    for index in range(1, 51):
        ws.append_turn(
            TurnRecord(
                turn_index=index,
                plan_summary=f"p{index}",
                tool_events=[_event(f"t{index}", f"summary {index}", refs=[f"ev-{index}"])],
                outcome=f"o{index}",
            )
        )
    assert len(ws.recent_turns) == 3
    assert ws.recent_turns[-1].turn_index == 50
    assert ws.digest_token_estimate() <= 256
    assert ws.compacted_turns == 47


def test_summarize_helpers_reject_hollow_placeholders():
    assert "read_health" in summarize_tool_result(
        tool_name="read_health", status="succeeded", result_summary="tool calls completed"
    )
    assert "无详细摘要" in summarize_tool_result(
        tool_name="read_health", status="succeeded", result_summary=""
    )
    plan = summarize_plan(
        [{"task_id": "t1", "objective": "检查健康", "tool_calls": [{"tool_name": "read_health"}]}]
    )
    assert "t1" in plan and "read_health" in plan
    outcome = summarize_turn_outcome([_event("read_health", "503 from api")])
    assert "read_health" in outcome
    assert outcome != "tool calls completed"
