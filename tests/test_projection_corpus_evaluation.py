from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from scripts.run_projection_corpus_evaluation import evaluate


def test_projection_corpus_evaluation_reports_all_variants():
    cases=LongMemEvalLoader.from_records([{"question_id":"q","question_type":"x","question":"ERR_X","answer":"x","question_date":"2026-01-01","haystack_session_ids":["s"],"haystack_dates":["2026-01-01"],"haystack_sessions":[[{"role":"user","content":"ERR_X"}]],"answer_session_ids":["s"]}])
    report=evaluate(cases)
    assert set(report)=={"raw","session_summ","keyphrase_userfact","raw_plus_projection"}
    assert report["raw"]["recall_at_5"]==1.0
