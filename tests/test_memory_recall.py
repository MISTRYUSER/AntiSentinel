from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.turn import Turn
from antisentinel.memory import recall
from antisentinel.memory.session_tree import SessionTimelineTree
from antisentinel.worker.runtime.context import ContextBuilder


def test_recall_requires_session_scope_and_does_not_return_another_session():
    first = SessionTimelineTree("session-1")
    second = SessionTimelineTree("session-2")
    memory_recall = recall.MemoryRecall({"session-1": first, "session-2": second})

    own = memory_recall.recall(recall.MemoryScope(kind="session", scope_id="session-1"), query="current", token_budget=100)
    other = memory_recall.recall(recall.MemoryScope(kind="session", scope_id="missing"), query="current", token_budget=100)

    assert own.session_id == "session-1"
    assert other.session_id is None
    assert other.digest == ""


def test_context_builder_injects_digest_view_without_raw_tool_output():
    incident = Incident.create(title="bug", source="operator")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["operator-1"])
    turn = Turn.create(session_id=session.session_id)
    view = recall.MemoryRecall({}).empty_view(session_id=session.session_id)
    view = view.with_digest("upstream timeout", ["evidence-1"])

    request = ContextBuilder().build(
        incident, session, turn, prior_turns=[], task_results=[{"summary": "health checked", "raw_tool_output": "secret"}], tools=[], memory_context=view
    )
    serialized = str(request.messages)

    assert "upstream timeout" in serialized
    assert "evidence-1" in serialized
    assert "secret" not in serialized


def test_memory_recall_builds_context_from_real_records_with_budget_and_refs():
    records = [{
        "memory_id": "memory-1", "memory_type": "preference", "owner_id": "operator-1",
        "operator_id": "operator-1", "incident_id": None, "session_id": None,
        "status": "active", "confidence": 0.9, "valid_from": "2020-01-01T00:00:00+00:00",
        "valid_to": None, "content": "排查时先看日志", "source_refs": [{"type": "session", "id": "session-1"}],
    }]
    tree = SessionTimelineTree("session-1")
    memory_recall = recall.MemoryRecall({"session-1": tree}, records_provider=lambda _scope: records)

    view = memory_recall.recall(
        recall.MemoryScope(kind="session", scope_id="session-1"),
        query="排查顺序", token_budget=20, operator_id="operator-1", incident_id="incident-1",
    )

    assert "先看日志" in view.digest
    assert view.evidence_refs == ()
    assert view.memory_ids == ("memory-1",)


def test_memory_recall_rejects_legacy_source_ids_without_typed_provenance():
    records = [{
        "memory_id": "memory-legacy", "memory_type": "preference", "operator_id": "operator-1",
        "status": "active", "confidence": 0.9, "valid_from": "2020-01-01T00:00:00+00:00",
        "content": "排查时先看日志", "source_ids": ["evidence-legacy"],
    }]
    tree = SessionTimelineTree("session-1")
    view = recall.MemoryRecall({"session-1": tree}, records_provider=lambda _: records).recall(
        recall.MemoryScope(kind="session", scope_id="session-1"), query="看日志", token_budget=20,
        operator_id="operator-1", incident_id="incident-1",
    )

    assert view.evidence_refs == ()
    assert view.memory_ids == ()
