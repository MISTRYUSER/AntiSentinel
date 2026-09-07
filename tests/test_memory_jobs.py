from antisentinel.memory.candidates import CandidateSource, MemoryCandidate
from antisentinel.memory.jobs import InMemoryMemoryJobQueue, MemoryJob, RedisMemoryJobQueue
from antisentinel.memory.recorder import MemoryRecorder


def test_in_memory_queue_delays_retry_and_marks_third_failure_terminal():
    now = [100.0]
    queue = InMemoryMemoryJobQueue(clock=lambda: now[0])
    job = MemoryJob(
        job_id="job-1", source=CandidateSource(operator_id="operator-1", session_id="session-1"),
        candidates=(),
    )
    queue.enqueue(job)

    assert queue.claim() == job
    queue.retry("job-1", "RuntimeError")
    assert queue.claim() is None
    now[0] = 101.0
    assert queue.claim().attempts == 1
    queue.retry("job-1", "RuntimeError")
    now[0] = 103.0
    assert queue.claim().attempts == 2
    queue.fail("job-1", "RuntimeError")

    assert queue.claim() is None
    assert queue.get("job-1").status == "failed"
    assert queue.has_inflight() is False


def test_recorder_retries_classifier_failure_then_marks_job_failed(tmp_path):
    now = [100.0]

    class AlwaysFail:
        def classify(self, candidate):
            raise RuntimeError("classification failure")

    queue = InMemoryMemoryJobQueue(clock=lambda: now[0])
    recorder = MemoryRecorder(tmp_path, job_queue=queue, memory_classifier=AlwaysFail())
    candidate = MemoryCandidate.create(
        operator_id="operator-1", incident_id="incident-1", session_id="session-1",
        turn_id=None, text="timeout", source_kind="agent_conclusion", source_version=1,
    )
    queue.enqueue(MemoryJob("job-1", CandidateSource("operator-1", "session-1", "incident-1"), (candidate,)))

    assert recorder.process_one_memory_job() is None
    now[0] = 101.0
    assert recorder.process_one_memory_job() is None
    now[0] = 103.0
    assert recorder.process_one_memory_job() is None

    terminal = queue.get("job-1")
    assert terminal.status == "failed"
    assert terminal.attempts == 2
    assert queue.has_inflight() is False


def test_memory_job_round_trip_preserves_producer_trace_carrier():
    from antisentinel.tracing.telemetry import TraceContext

    trace_context = TraceContext.new(session_id="session-1").inject()
    job = MemoryJob(
        job_id="job-trace",
        source=CandidateSource(operator_id="operator-1", session_id="session-1"),
        candidates=(),
        trace_context=trace_context,
    )

    encoded = RedisMemoryJobQueue._encode(job)
    restored = RedisMemoryJobQueue._decode(encoded)

    assert restored.trace_context == trace_context
