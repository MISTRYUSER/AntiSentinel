import json

from antisentinel.evaluation.longmemeval import LongMemEvalLoader, KeywordMemoryRetriever, evaluate_retrieval


def test_loader_maps_haystack_sessions_and_answer_evidence(tmp_path):
    path = tmp_path / "longmemeval.json"
    path.write_text(json.dumps([{
        "question_id": "q-1", "question_type": "single-session-user", "question": "Where is the red bicycle?",
        "answer": "In the garage", "question_date": "2026-01-01",
        "haystack_session_ids": ["s-1", "s-2"],
        "haystack_dates": ["2025-01-01", "2025-01-02"],
        "haystack_sessions": [
            [{"role": "user", "content": "I bought a red bicycle"}, {"role": "assistant", "content": "Nice"}],
            [{"role": "user", "content": "The bicycle is in the garage"}],
        ],
        "answer_session_ids": ["s-2"],
    }], ensure_ascii=False), encoding="utf-8")

    case = next(LongMemEvalLoader(path))

    assert case.question_id == "q-1"
    assert [session.session_id for session in case.sessions] == ["s-1", "s-2"]
    assert case.answer_session_ids == ("s-2",)
    assert case.sessions[1].turns[0].turn_id == "s-2:0"


def test_keyword_retriever_returns_ranked_session_ids_and_recall_metrics():
    case = next(LongMemEvalLoader.from_records([{
        "question_id": "q-1", "question_type": "single-session-user", "question": "Where is the red bicycle?",
        "answer": "In the garage", "question_date": "2026-01-01",
        "haystack_session_ids": ["s-1", "s-2"], "haystack_dates": ["2025-01-01", "2025-01-02"],
        "haystack_sessions": [[{"role": "user", "content": "I bought a red bicycle"}], [{"role": "user", "content": "The bicycle is in the garage"}]],
        "answer_session_ids": ["s-2"],
    }]))

    result = KeywordMemoryRetriever().retrieve(case, top_k=1)

    assert result.session_ids == ("s-2",)
    assert result.recall_at_k == 1.0
    assert result.precision_at_k == 1.0
    assert result.reciprocal_rank == 1.0


def test_retriever_does_not_leak_answer_session_ids_into_ranking():
    case = next(LongMemEvalLoader.from_records([{
        "question_id": "q-1", "question_type": "multi-session", "question": "What was the plan?",
        "answer": "A plan", "question_date": "2026-01-01",
        "haystack_session_ids": ["s-1", "s-2"], "haystack_dates": ["2025-01-01", "2025-01-02"],
        "haystack_sessions": [[{"role": "user", "content": "unrelated"}], [{"role": "user", "content": "a plan"}]],
        "answer_session_ids": ["s-2"],
    }]))

    result = KeywordMemoryRetriever().retrieve(case, top_k=1)

    assert result.session_ids == ("s-2",)


def test_full_retrieval_evaluation_skips_abstention_and_aggregates_metrics():
    records = []
    for qid, answer in (("q-1", ["s-2"]), ("q-2_abs", [])):
        records.append({
            "question_id": qid, "question_type": "single-session-user", "question": "Where is the bicycle?",
            "answer": "garage", "question_date": "2026-01-01",
            "haystack_session_ids": ["s-1", "s-2"], "haystack_dates": ["2025-01-01", "2025-01-02"],
            "haystack_sessions": [[{"role": "user", "content": "bought a bicycle"}], [{"role": "user", "content": "bicycle in garage"}]],
            "answer_session_ids": answer,
        })

    summary = evaluate_retrieval(LongMemEvalLoader.from_records(records), KeywordMemoryRetriever(), top_ks=(1, 2))

    assert summary.evaluated_cases == 1
    assert summary.skipped_abstentions == 1
    assert summary.error_count == 0
    assert summary.metrics["recall@2"] == 1.0
