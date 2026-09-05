from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from antisentinel.evaluation.memory_retrieval import (
    EvidenceRefResolver,
    ExplainableRanker,
    MemoryRecordProjector,
    RetrievalFilter,
)


def _case():
    return next(LongMemEvalLoader.from_records([{
        "question_id": "q-1", "question_type": "single-session-user", "question": "Where is the red bicycle?",
        "answer": "In the garage", "question_date": "2026-01-03",
        "haystack_session_ids": ["s-1", "s-2"], "haystack_dates": ["2026-01-01", "2026-01-02"],
        "haystack_sessions": [
            [{"role": "user", "content": "I bought a red bicycle"}],
            [{"role": "user", "content": "The red bicycle is in the garage", "has_answer": True}],
        ],
        "answer_session_ids": ["s-2"],
    }]))


def test_projector_creates_memory_records_without_answer_label_leakage():
    records = MemoryRecordProjector().project(_case())

    assert {record["memory_type"] for record in records} == {"session_digest", "turn_summary"}
    answer_record = next(record for record in records if record["memory_type"] == "turn_summary" and record["session_id"] == "s-2")
    assert answer_record["source_refs"] == [{"type": "session", "id": "s-2"}, {"type": "turn", "id": "s-2:0"}]


def test_filter_rejects_conflict_expired_and_wrong_scope_records():
    records = [
        {"memory_id": "active", "status": "active", "owner_id": "op-1", "incident_id": "i-1", "valid_from": "2026-01-01", "valid_to": None},
        {"memory_id": "expired", "status": "active", "owner_id": "op-1", "incident_id": "i-1", "valid_from": "2025-01-01", "valid_to": "2026-01-02"},
        {"memory_id": "conflict", "status": "conflict", "owner_id": "op-1", "incident_id": "i-1", "valid_from": "2026-01-01", "valid_to": None},
        {"memory_id": "other", "status": "active", "owner_id": "op-2", "incident_id": "i-1", "valid_from": "2026-01-01", "valid_to": None},
    ]

    filtered = RetrievalFilter().apply(records, owner_id="op-1", incident_id="i-1", query_time="2026-01-03")

    assert [item["memory_id"] for item in filtered.records] == ["active"]
    assert filtered.reasons == {"expired": "expired", "conflict": "status_conflict", "other": "owner_mismatch"}


def test_ranker_returns_score_breakdown_and_resolver_restores_ids():
    records = MemoryRecordProjector().project(_case())
    ranked = ExplainableRanker().rank(records, query="Where is the red bicycle?", session_id="s-2", incident_id="i-1", top_k=2)

    assert ranked[0]["source_refs"][0] == {"type": "session", "id": "s-2"}
    assert set(ranked[0]["score_breakdown"]) == {"lexical", "scope", "recency", "confidence", "evidence_support", "type"}
    resolved = EvidenceRefResolver().resolve(ranked)
    assert resolved.session_ids[0] == "s-2"
    assert "s-2:0" in resolved.turn_ids


def test_context_assembler_enforces_budget_and_keeps_evidence_supported_items():
    from antisentinel.evaluation.memory_retrieval import MemoryContextAssembler

    records = [
        {"memory_id": "high", "content": "重要证据", "score": 0.9, "source_refs": [{"type": "turn", "id": "t-1"}]},
        {"memory_id": "low", "content": "普通摘要", "score": 0.2, "source_refs": []},
    ]
    context = MemoryContextAssembler().assemble(records, token_budget=2)

    assert context.memory_ids == ("high",)
    assert context.evidence_refs == ({"type": "turn", "id": "t-1"},)
    assert context.estimated_tokens <= 2


def test_context_assembler_does_not_inject_two_active_values_for_same_entity():
    from antisentinel.evaluation.memory_retrieval import MemoryContextAssembler

    records = [
        {"memory_id": "new", "content": "偏好新值", "score": 0.9, "subject": "order", "predicate": "prefers", "object": "new"},
        {"memory_id": "old", "content": "偏好旧值", "score": 0.8, "subject": "order", "predicate": "prefers", "object": "old"},
    ]

    context = MemoryContextAssembler().assemble(records, token_budget=20)

    assert context.memory_ids == ("new",)


def test_context_assembler_truncates_oversized_top_record_instead_of_returning_empty():
    from antisentinel.evaluation.memory_retrieval import MemoryContextAssembler

    context = MemoryContextAssembler().assemble([
        {"memory_id": "large", "content": "这是一个很长的记忆摘要", "score": 1.0, "source_refs": []},
    ], token_budget=2)

    assert context.memory_ids == ("large",)
    assert context.estimated_tokens == 2
    assert context.records[0]["content"]
    assert len(context.records[0]["content"]) <= 4


def test_preference_record_exposes_searchable_content_without_losing_graph_fields():
    from antisentinel.memory.operator_graph import PreferenceRecord

    record = PreferenceRecord(
        memory_id="preference-1", operator_id="operator-1", subject="diagnosis_order",
        predicate="prefers", object="logs_before_metrics", source_ids=("evidence-1",), confidence=0.9,
    ).to_dict()

    assert "logs_before_metrics" in record["content"]
    assert record["subject"] == "diagnosis_order"
    assert record["predicate"] == "prefers"
