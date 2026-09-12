import json

from antisentinel.worker.runtime.checkpoint import InMemoryCheckpointStore, RuntimeSnapshot


def make_snapshot():
    return RuntimeSnapshot(
        session_id="session-1",
        incident={"incident_id": "incident-1"},
        session={"session_id": "session-1"},
        turns=[{"turn_id": "turn-1"}],
        tasks=[{"task_id": "task-1"}],
        tool_calls=[{"tool_call_id": "call-1"}],
        attempts=[{"attempt_id": "attempt-1", "status": "succeeded"}],
        messages=[{"role": "user", "content": "context"}],
        turn_count=1,
        current_task_index=0,
        current_tool_call_index=1,
        successful_tool_call_ids=("call-1",),
        last_error={"code": "temporary"},
        pending_results=[{"task_id": "task-1", "summary": "done"}],
        completed_invocations={"read_health:{\"service\": \"api\"}": {"summary": "done"}},
    )


def test_snapshot_round_trip_is_json_serializable():
    snapshot = make_snapshot()

    encoded = snapshot.to_dict()
    restored = RuntimeSnapshot.from_dict(json.loads(json.dumps(encoded)))

    assert restored == snapshot
    assert restored.successful_tool_call_ids == ("call-1",)
    assert restored.pending_results == [{"task_id": "task-1", "summary": "done"}]


def test_in_memory_store_defensively_copies_and_clears():
    store = InMemoryCheckpointStore()
    snapshot = make_snapshot()
    store.save(snapshot)
    snapshot.messages[0]["content"] = "mutated"

    loaded = store.load("session-1")
    assert loaded is not None
    assert loaded.messages[0]["content"] == "context"

    loaded.messages[0]["content"] = "changed outside store"
    assert store.load("session-1").messages[0]["content"] == "context"

    store.clear("session-1")
    assert store.load("session-1") is None


def test_checkpoint_keeps_source_context_references_without_source_body():
    snapshot = make_snapshot()
    snapshot.source_context_refs = [{
        "evidence_id": "evidence-1", "repository_id": "repo-a", "snapshot_id": "snapshot-a",
        "commit_sha": "commit-a", "path": "app.py", "content_hash": "a" * 64,
    }]

    encoded = snapshot.to_dict()

    assert encoded["source_context_refs"][0]["evidence_id"] == "evidence-1"
    assert "content" not in encoded["source_context_refs"][0]


def test_checkpoint_optional_working_set_round_trip_and_rebuild():
    from antisentinel.worker.runtime.checkpoint import rebuild_working_set

    snapshot = make_snapshot()
    snapshot.working_set = {
        "recent_turn_limit": 3,
        "sticky": {"active_skill": None, "source_slice_refs": [], "open_questions": [], "active_hypotheses": []},
        "recent_turns": [
            {
                "turn_index": 1,
                "plan_summary": "inspect",
                "outcome": "read_health:succeeded",
                "tool_events": [
                    {
                        "tool_name": "read_health",
                        "args_fingerprint": "abc",
                        "status": "succeeded",
                        "result_summary": "503",
                        "evidence_refs": ["ev-1"],
                    }
                ],
            }
        ],
        "older_digest": "",
        "compacted_turns": 0,
    }

    restored = RuntimeSnapshot.from_dict(snapshot.to_dict())
    assert restored.working_set["recent_turns"][0]["tool_events"][0]["result_summary"] == "503"

    ws = rebuild_working_set(restored, recent_turn_limit=3)
    assert ws.recent_turns[0].tool_events[0].result_summary == "503"

    legacy = make_snapshot()
    legacy.working_set = None
    empty = rebuild_working_set(legacy, recent_turn_limit=2)
    assert empty.recent_turns == []
    assert empty.recent_turn_limit == 2

