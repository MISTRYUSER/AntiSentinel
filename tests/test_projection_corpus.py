from antisentinel.evaluation.longmemeval import LongMemEvalLoader
from antisentinel.evaluation.projection_corpus import build_projection_corpora
from antisentinel.evaluation.projection_corpus import evaluation_time


def test_projection_corpora_keep_session_and_turn_provenance():
    case = next(LongMemEvalLoader.from_records([{
        "question_id": "q", "question_type": "single", "question": "ERR_TIMEOUT_504", "answer": "x", "question_date": "2026-01-01",
        "haystack_session_ids": ["s"], "haystack_dates": ["2026-01-01"],
        "haystack_sessions": [[{"role": "user", "content": "ERR_TIMEOUT_504 cache:tenant:1"}, {"role": "assistant", "content": "先检查 upstream"}]], "answer_session_ids": ["s"],
    }]))

    corpora = build_projection_corpora(case)

    assert set(corpora) == {"raw", "session_summ", "keyphrase_userfact", "raw_plus_projection"}
    assert corpora["raw"][0]["session_id"] == "s"
    assert corpora["session_summ"][0]["turn_ids"] == ("s:0", "s:1")
    assert "ERR_TIMEOUT_504" in corpora["keyphrase_userfact"][0]["content"]
    assert len(corpora["raw_plus_projection"]) > len(corpora["raw"])


def test_projection_corpus_accepts_longmemeval_slash_dates():
    assert evaluation_time("2023/05/20 (Sat) 02:21").isoformat() == "2023-05-20T02:21:00+00:00"


def test_projection_corpus_uses_injected_projector():
    class Projector:
        def project(self, source):
            from antisentinel.memory.projection import MemoryProjection
            from antisentinel.memory.models import MemorySourceRef
            return (MemoryProjection("p", "diagnosis_fact", "extracted", (MemorySourceRef("turn", source.turns[0][0]),), "active"),)
    case = next(LongMemEvalLoader.from_records([{"question_id":"q","question_type":"x","question":"q","answer":"x","question_date":"2026-01-01","haystack_session_ids":["s"],"haystack_dates":["2026-01-01"],"haystack_sessions":[[{"role":"user","content":"source"}]],"answer_session_ids":["s"]}]))
    assert build_projection_corpora(case, projector=Projector())["keyphrase_userfact"][0]["content"] == "extracted"
