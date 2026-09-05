from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from scripts.run_hybrid_memory_evaluation import evaluate, evaluate_hybrid
from scripts.run_hybrid_memory_evaluation import evaluation_time


def test_evaluation_reports_retrieval_metrics_latency_and_bypass():
    cases = LongMemEvalLoader.from_records([{
        "question_id": "q-1", "question_type": "single-session-user", "question": "Where is the bicycle?",
        "answer": "garage", "question_date": "2026-01-01", "haystack_session_ids": ["s-1", "s-2"],
        "haystack_dates": ["2026-01-01", "2026-01-02"],
        "haystack_sessions": [[{"role": "user", "content": "unrelated"}], [{"role": "user", "content": "bicycle garage"}]],
        "answer_session_ids": ["s-2"],
    }])

    report = evaluate(cases, top_k=5)

    assert report["evaluated_cases"] == 1
    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["metrics"]["precision_at_5"] == 0.2
    assert report["metrics"]["mrr_at_5"] == 1.0
    assert report["metrics"]["recall_at_1"] == 1.0
    assert report["metrics"]["precision_at_10"] == 0.1
    assert report["metrics"]["mrr_at_10"] == 1.0
    assert report["channel_status"] == {"lexical": "active", "identifier": "bypass", "vector": "bypass"}
    assert report["latency_ms"]["p95"] >= 0


def test_hybrid_evaluation_uses_production_candidate_and_trust_path(tmp_path):
    cases = LongMemEvalLoader.from_records([{
        "question_id": "q-1", "question_type": "single-session-user", "question": "where is bicycle",
        "answer": "garage", "question_date": "2026-01-01", "haystack_session_ids": ["s-1", "s-2"],
        "haystack_dates": ["2026-01-01", "2026-01-02"],
        "haystack_sessions": [[{"role": "user", "content": "unrelated"}], [{"role": "user", "content": "bicycle garage"}]],
        "answer_session_ids": ["s-2"],
    }])

    report = evaluate_hybrid(cases, database_path=tmp_path / "hybrid.db", top_k=5)

    assert report["evaluated_cases"] == 1
    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["channel_status"]["lexical"] == "active"
    assert report["channel_status"]["vector"] == "bypass"


def test_evaluation_time_accepts_longmemeval_slash_date_format():
    assert evaluation_time("2023/05/30 (Tue) 23:40").isoformat() == "2023-05-30T23:40:00+00:00"
