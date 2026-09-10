from datetime import datetime, timedelta, timezone

from antisentinel.domain.event import Event
from antisentinel.memory.session_tree import SessionTimelineTree


def event(event_id: str, event_type: str, aggregate_type: str, aggregate_id: str, related: dict[str, str], summary: str) -> Event:
    return Event(
        event_id=event_id,
        type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id="session-1",
        occurred_at=datetime(2026, 9, 4, tzinfo=timezone.utc) + timedelta(seconds=int(event_id[-1])),
        payload={"summary": summary, **related},
        related_ids=related,
    )


def test_session_tree_builds_turn_task_tool_attempt_parent_chain_and_parallel_branch():
    tree = SessionTimelineTree("session-1")
    session = event("event-0", "session.created", "Session", "session-1", {"session_id": "session-1"}, "session")
    turn = event("event-1", "turn.started", "Turn", "turn-1", {"session_id": "session-1", "turn_id": "turn-1"}, "turn")
    task_a = event("event-2", "task.created", "Task", "task-a", {"session_id": "session-1", "turn_id": "turn-1", "task_id": "task-a"}, "task a")
    task_b = event("event-3", "task.created", "Task", "task-b", {"session_id": "session-1", "turn_id": "turn-1", "task_id": "task-b"}, "task b")
    call = event("event-4", "tool_call.requested", "ToolCall", "call-1", {"session_id": "session-1", "task_id": "task-a", "tool_call_id": "call-1"}, "read log")
    attempt = event("event-5", "attempt.completed", "Attempt", "attempt-1", {"session_id": "session-1", "tool_call_id": "call-1", "attempt_id": "attempt-1"}, "log read")

    for item in (session, turn, task_a, task_b, call, attempt):
        tree.append_event(item)

    nodes = {node.node_id: node for node in tree.nodes()}
    assert nodes[turn.event_id].parent_node_id == session.event_id
    assert nodes[task_a.event_id].parent_node_id == turn.event_id
    assert nodes[task_b.event_id].parent_node_id == turn.event_id
    assert nodes[call.event_id].parent_node_id == task_a.event_id
    assert nodes[attempt.event_id].parent_node_id == call.event_id
    assert {node.node_id for node in tree.children(turn.event_id)} == {task_a.event_id, task_b.event_id}


def test_session_tree_rebuild_is_idempotent_and_digest_is_bounded():
    from antisentinel.worker.runtime.budget import estimate_tokens

    tree = SessionTimelineTree("session-1")
    events = [event(f"event-{index}", "turn.started", "Turn", f"turn-{index}", {"session_id": "session-1"}, "x" * 30) for index in range(6)]

    tree.rebuild(events)
    first = tree.digest(token_budget=20)
    tree.rebuild(events)

    assert len(tree.nodes()) == len(events)
    assert estimate_tokens(first["summary"]) <= 20
    assert first["node_count"] == len(events)
