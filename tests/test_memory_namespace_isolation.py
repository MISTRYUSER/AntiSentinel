"""Fail-closed MemoryNamespace / SessionKey isolation regressions."""

from antisentinel.memory.models import (
    AuthorizedMemoryScope,
    MemoryNamespace,
    MissingMemoryScopeError,
    SessionKey,
    new_memory_id,
)
from antisentinel.memory.operator_graph import FilePreferenceStore, InMemoryMemoryCache, PreferenceCandidate, PreferenceGraph
from antisentinel.memory.recorder import MemoryRecorder
from antisentinel.persistence.memory_store import FileMemoryStore
from antisentinel.ports.model import FinalDiagnosis
from antisentinel.worker.runtime.loop import RuntimeResult
import pytest


def test_authorized_scope_rejects_unknown_placeholders():
    with pytest.raises(MissingMemoryScopeError):
        AuthorizedMemoryScope("unknown", "incident-1", "session-1")
    with pytest.raises(MissingMemoryScopeError):
        AuthorizedMemoryScope("operator-1", "unknown", "session-1")


def test_memory_recorder_requires_operator_id(tmp_path):
    result = RuntimeResult(
        status="completed", incident_id="incident-1", session_id="session-1", turn_count=1,
        final=FinalDiagnosis(summary="fixed", diagnosis="timeout", confidence=0.9, evidence_refs=()),
        evidence_refs=(), task_summaries=(), error=None, events=(),
    )
    recorder = MemoryRecorder(tmp_path)
    with pytest.raises(TypeError):
        recorder.record(result)  # type: ignore[call-arg]
    with pytest.raises(MissingMemoryScopeError):
        recorder.record(result, operator_id="unknown")


def test_preference_ids_do_not_collide_across_operators(tmp_path):
    graph = PreferenceGraph(FilePreferenceStore(tmp_path), InMemoryMemoryCache())
    first = graph.upsert(PreferenceCandidate(
        operator_id="operator-a", subject="diagnosis_order", predicate="prefers",
        object="logs_before_metrics", source_ids=("s1",), confidence=0.9,
    ))
    second = graph.upsert(PreferenceCandidate(
        operator_id="operator-b", subject="diagnosis_order", predicate="prefers",
        object="metrics_before_logs", source_ids=("s2",), confidence=0.9,
    ))
    assert first.memory_id != second.memory_id
    assert first.memory_id.startswith("mem_")
    assert second.memory_id.startswith("mem_")
    store = FileMemoryStore(tmp_path)
    assert len(store.list_by_operator("operator-a")) == 1
    assert len(store.list_by_operator("operator-b")) == 1


def test_list_by_scope_filters_at_storage_layer(tmp_path):
    store = FileMemoryStore(tmp_path)
    store.append({
        "memory_id": new_memory_id(), "memory_type": "episodic", "operator_id": "op-1",
        "incident_id": "inc-1", "session_id": "s-1", "tenant_id": "default", "agent_id": "diagnosis-agent",
        "content": "a", "status": "active", "confidence": 0.9,
    })
    store.append({
        "memory_id": new_memory_id(), "memory_type": "episodic", "operator_id": "op-1",
        "incident_id": "inc-2", "session_id": "s-2", "tenant_id": "default", "agent_id": "diagnosis-agent",
        "content": "b", "status": "active", "confidence": 0.9,
    })
    store.append({
        "memory_id": new_memory_id(), "memory_type": "preference", "operator_id": "op-1",
        "incident_id": None, "session_id": None, "tenant_id": "default", "agent_id": "diagnosis-agent",
        "content": "pref", "status": "active", "confidence": 0.9,
    })
    scoped = store.list_by_scope(MemoryNamespace(
        tenant_id="default", operator_id="op-1", agent_id="diagnosis-agent", incident_id="inc-1",
    ))
    assert {item["content"] for item in scoped} == {"a", "pref"}


def test_session_keys_isolate_same_session_id_across_operators():
    left = SessionKey("default", "op-a", "diagnosis-agent", "session-001")
    right = SessionKey("default", "op-b", "diagnosis-agent", "session-001")
    assert left != right
