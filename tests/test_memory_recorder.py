from antisentinel.domain.event import Event
from antisentinel.memory.recorder import MemoryRecorder
from antisentinel.ports.model import FinalDiagnosis
from antisentinel.worker.runtime.loop import RuntimeResult


def test_memory_recorder_automatically_persists_completed_rollout(tmp_path):
    result = RuntimeResult(
        status="completed",
        incident_id="incident-1",
        session_id="session-1",
        turn_count=1,
        final=FinalDiagnosis(summary="fixed", diagnosis="timeout", confidence=0.9, evidence_refs=()),
        evidence_refs=(),
        task_summaries=(),
        error=None,
        events=(Event.create(type="session.completed", aggregate_type="Session", aggregate_id="session-1", correlation_id="session-1", occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), payload={}),),
    )

    recorder = MemoryRecorder(tmp_path)
    recorder.record(result, operator_id="operator-1")

    rollout = recorder.rollout_memory.get("session-1")
    assert rollout is not None
    assert rollout["diagnosis"] == "timeout"


def test_memory_recorder_worker_persists_classified_preference(tmp_path):
    result = RuntimeResult(
        status="completed", incident_id="incident-2", session_id="session-2", turn_count=1,
        final=FinalDiagnosis(summary="fixed", diagnosis="logs_before_metrics", confidence=0.9, evidence_refs=()),
        evidence_refs=(), task_summaries=(), error=None, events=(),
    )
    recorder = MemoryRecorder(tmp_path)
    recorder.record(result, operator_id="operator-1", user_input="先看日志，再看 metrics")

    processed = recorder.process_one_memory_job()

    assert processed is not None
    assert processed[1][0].memory_type == "preference"
    assert recorder.preference_graph.list_active("operator-1")[0].object == "logs_before_metrics"


def test_memory_recorder_worker_persists_episodic_classification(tmp_path):
    result = RuntimeResult(
        status="completed", incident_id="incident-3", session_id="session-3", turn_count=1,
        final=FinalDiagnosis(summary="fixed", diagnosis="upstream timeout", confidence=0.9, evidence_refs=()),
        evidence_refs=(), task_summaries=(), error=None, events=(),
    )
    recorder = MemoryRecorder(tmp_path)
    recorder.record(result, operator_id="operator-1")

    processed = recorder.process_one_memory_job()
    records = recorder.rollout_memory.durable._read()

    assert processed is not None
    assert any(item.get("memory_type") == "episodic" and item.get("content") == "upstream timeout" for item in records)


def test_two_sessions_with_the_same_diagnosis_keep_distinct_typed_memories(tmp_path):
    def result(session_id):
        return RuntimeResult(
            status="completed", incident_id="incident-1", session_id=session_id, turn_count=1,
            final=FinalDiagnosis(summary="fixed", diagnosis="upstream timeout", confidence=0.9, evidence_refs=()),
            evidence_refs=(), task_summaries=(), error=None, events=(),
        )

    recorder = MemoryRecorder(tmp_path)
    recorder.record(result("session-1"), operator_id="operator-1")
    recorder.record(result("session-2"), operator_id="operator-1")
    recorder.process_one_memory_job()
    recorder.process_one_memory_job()

    records = [item for item in recorder.rollout_memory.durable._read() if item.get("memory_type") == "episodic"]
    assert len(records) == 2
    assert len({item["memory_id"] for item in records}) == 2
    typed = [recorder.rollout_memory.durable.get_record(item["memory_id"]) for item in records]
    assert {record.source_refs[0].ref_type for record in typed} == {"session"}


def test_preference_persists_a_typed_session_source(tmp_path):
    result = RuntimeResult(
        status="completed", incident_id="incident-1", session_id="session-1", turn_count=1,
        final=FinalDiagnosis(summary="fixed", diagnosis="timeout", confidence=0.9, evidence_refs=()),
        evidence_refs=(), task_summaries=(), error=None, events=(),
    )
    recorder = MemoryRecorder(tmp_path)
    recorder.record(result, operator_id="operator-1", user_input="先看日志")
    recorder.process_one_memory_job()

    raw = recorder.preference_graph.durable.store.list_by_operator("operator-1")[0]
    assert raw["source_refs"] == [{"type": "session", "id": "session-1"}]
