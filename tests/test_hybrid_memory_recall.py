from datetime import datetime, timezone

from antisentinel.memory.recall import MemoryRecall
from antisentinel.memory.models import MemoryRecord, MemoryScope, MemorySourceRef
from antisentinel.memory.session_tree import SessionTimelineTree


class Candidate:
    def __init__(self, memory_id): self.memory_id = memory_id


class Retriever:
    def retrieve(self, query, scope, *, limit):
        return (Candidate("high"), Candidate("low"))


def test_memory_recall_uses_hybrid_candidate_order_before_trusted_filtering():
    now = datetime(2026, 9, 5, tzinfo=timezone.utc)
    def record(memory_id, content):
        return MemoryRecord.create(memory_id=memory_id, memory_type="episodic", operator_id="op", incident_id="incident", session_id="session", content=content, source_refs=(MemorySourceRef("session", "session"),), extraction_confidence=.9, valid_from=now).to_dict()
    recall = MemoryRecall({"session": SessionTimelineTree("session")}, records_provider=lambda _: [record("low", "低"), record("high", "高")], clock=lambda: now, candidate_retriever=Retriever())

    view = recall.recall(MemoryScope("session", "session"), query="查询", token_budget=100, operator_id="op", incident_id="incident")

    assert view.memory_ids == ("high", "low")
