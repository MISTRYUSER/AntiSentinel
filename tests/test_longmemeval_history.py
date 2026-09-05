from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from antisentinel.evaluation.longmemeval_history import LongMemEvalHistoryScanner


def test_history_scanner_emits_stable_turn_candidates_and_proxy_labels():
    case = next(LongMemEvalLoader.from_records([{
        "question_id": "q-1", "question_type": "single-session-user", "question": "Where?",
        "answer": "home", "question_date": "2026-01-01", "haystack_session_ids": ["s-1"],
        "haystack_dates": ["2025-01-01"], "haystack_sessions": [[
            {"role": "user", "content": "I am home"},
            {"role": "assistant", "content": "Okay", "has_answer": True},
        ]], "answer_session_ids": ["s-1"],
    }]))

    rows = list(LongMemEvalHistoryScanner().scan([case]))

    assert rows[0].candidate_id == "q-1:s-1:0"
    assert rows[0].proxy_evidence_label is False
    assert rows[1].candidate_id == "q-1:s-1:1"
    assert rows[1].proxy_evidence_label is True


def test_history_scanner_reports_duplicate_content_groups():
    case = next(LongMemEvalLoader.from_records([{
        "question_id": "q-1", "question_type": "multi-session", "question": "What?",
        "answer": "x", "question_date": "2026-01-01", "haystack_session_ids": ["s-1", "s-2"],
        "haystack_dates": ["2025-01-01", "2025-01-02"], "haystack_sessions": [
            [{"role": "user", "content": "same fact"}], [{"role": "user", "content": "same fact"}],
        ], "answer_session_ids": ["s-2"],
    }]))

    summary = LongMemEvalHistoryScanner().summarize([case])

    assert summary.total_turns == 2
    assert summary.proxy_positive_turns == 0
    assert summary.duplicate_content_groups == 1
    assert summary.duplicate_turns == 2
