from antisentinel.memory import candidates, jobs


def test_candidate_pipeline_extracts_and_classifies_completed_rollout():
    source = candidates.CandidateSource(
        operator_id="operator-1", session_id="session-1", user_input="先看日志，再看 metrics",
        tool_summaries=("nginx upstream timed out",), agent_conclusion="upstream timeout",
    )
    extractor = candidates.RuleCandidateExtractor()
    classifier = candidates.RuleMemoryClassifier()
    queue = jobs.InMemoryMemoryJobQueue()

    extracted = extractor.extract(source)
    queue.enqueue(jobs.MemoryJob(job_id="job-1", source=source, candidates=tuple(extracted)))
    job = queue.claim()
    classified = [classifier.classify(item) for item in job.candidates]

    assert {item.memory_type for item in classified} == {"preference", "episodic"}
    assert all(item.confidence > 0 for item in classified)
    queue.ack(job.job_id)
    assert queue.claim() is None


def test_failed_memory_job_is_retryable_and_does_not_block_main_result():
    source = candidates.CandidateSource(operator_id="operator-1", session_id="session-1", user_input="x")
    now = [100.0]
    queue = jobs.InMemoryMemoryJobQueue(clock=lambda: now[0])
    queue.enqueue(jobs.MemoryJob(job_id="job-2", source=source, candidates=()))
    job = queue.claim()
    queue.retry(job.job_id, "classifier timeout")

    assert queue.claim() is None
    now[0] = 101.0
    retry = queue.claim()
    assert retry.job_id == "job-2"
    assert retry.attempts == 1


def test_memory_recorder_accepts_an_llm_classifier_adapter(tmp_path):
    from antisentinel.memory.recorder import MemoryRecorder
    from antisentinel.memory.candidates import MemoryClassification
    from antisentinel.ports.model import FinalDiagnosis
    from antisentinel.worker.runtime.loop import RuntimeResult

    class Classifier:
        def classify(self, candidate):
            return MemoryClassification(candidate.candidate_id, True, "episodic", 0.91, "llm_test")

    recorder = MemoryRecorder(tmp_path, memory_classifier=Classifier())
    result = RuntimeResult(status="completed", incident_id="incident-1", session_id="session-1", turn_count=1, final=FinalDiagnosis(summary="fixed", diagnosis="timeout", confidence=.9, evidence_refs=()), evidence_refs=(), task_summaries=(), error=None, events=())
    recorder.record(result, operator_id="operator-1")
    recorder.process_one_memory_job()

    records = recorder.rollout_memory.durable.list_by_operator("operator-1")
    assert any(record["memory_type"] == "episodic" and record["content"] == "timeout" for record in records)
