"""Prove memory isolation at store, recall, and application full-chain layers."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService
from antisentinel.memory.models import MemoryNamespace, MemoryRecord, MemorySourceRef, MissingMemoryScopeError, new_memory_id
from antisentinel.memory.recorder import MemoryRecorder
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteMemoryStore
from antisentinel.ports.model import FinalDiagnosis
from antisentinel.worker.runtime.loop import RuntimeResult
from datetime import datetime, timezone


SECRET_A = "SECRET_MEM_ISOLATION_OPERATOR_A_7f3c"
SECRET_B = "SECRET_MEM_ISOLATION_OPERATOR_B_9a1e"


def _seed_runtime(session_id: str, incident_id: str, diagnosis: str) -> RuntimeResult:
    return RuntimeResult(
        status="completed",
        incident_id=incident_id,
        session_id=session_id,
        turn_count=1,
        final=FinalDiagnosis(summary="done", diagnosis=diagnosis, confidence=0.9, evidence_refs=()),
        evidence_refs=(),
        task_summaries=(),
        error=None,
        events=(),
    )


def test_sqlite_list_by_scope_never_returns_other_operator(tmp_path):
    db = SQLiteDatabase(tmp_path / "iso.db")
    db.initialize()
    store = SQLiteMemoryStore(db)
    now = datetime.now(timezone.utc)
    store.append_record(MemoryRecord.create(
        memory_id=new_memory_id(), memory_type="episodic", operator_id="op-a",
        incident_id="inc-a", session_id="s-a", content=SECRET_A,
        source_refs=(MemorySourceRef("session", "s-a"),), extraction_confidence=0.9, valid_from=now,
    ))
    store.append_record(MemoryRecord.create(
        memory_id=new_memory_id(), memory_type="episodic", operator_id="op-b",
        incident_id="inc-b", session_id="s-b", content=SECRET_B,
        source_refs=(MemorySourceRef("session", "s-b"),), extraction_confidence=0.9, valid_from=now,
    ))
    scoped = store.list_by_scope(MemoryNamespace(
        tenant_id="default", operator_id="op-a", agent_id="diagnosis-agent", incident_id="inc-a",
    ))
    body = " ".join(item["content"] for item in scoped)
    assert SECRET_A in body
    assert SECRET_B not in body
    with pytest.raises(MissingMemoryScopeError):
        store.search(SECRET_A, operator_id="unknown")


def test_recorder_recall_context_isolates_operators(tmp_path):
    recorder = MemoryRecorder(tmp_path)
    now = datetime.now(timezone.utc)
    for operator, secret, incident, session in (
        ("op-a", SECRET_A, "inc-a", "sess-a"),
        ("op-b", SECRET_B, "inc-b", "sess-b"),
    ):
        recorder.rollout_memory.durable.append_record(MemoryRecord.create(
            memory_id=new_memory_id(), memory_type="semantic", operator_id=operator,
            incident_id=None, session_id=None, content=secret,
            source_refs=(MemorySourceRef("session", session),), extraction_confidence=0.95, valid_from=now,
        ))
        recorder.record(_seed_runtime(session, incident, "timeout"), operator_id=operator)

    view_a = recorder.recall_context(
        session_id="sess-a", operator_id="op-a", incident_id="inc-a", query="secret", token_budget=400,
    )
    view_b = recorder.recall_context(
        session_id="sess-b", operator_id="op-b", incident_id="inc-b", query="secret", token_budget=400,
    )
    assert SECRET_A in view_a.digest
    assert SECRET_B not in view_a.digest
    assert SECRET_B in view_b.digest
    assert SECRET_A not in view_b.digest


def test_same_session_id_different_operators_use_separate_trees(tmp_path):
    recorder = MemoryRecorder(tmp_path)
    shared_session = "session-shared-001"
    recorder.record(_seed_runtime(shared_session, "inc-a", "a"), operator_id="op-a")
    recorder.record(_seed_runtime(shared_session, "inc-b", "b"), operator_id="op-b")
    assert len(recorder.trees) == 2


def _wait_session(client: TestClient, session_id: str, timeout_s: float = 120.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        payload = client.get(f"/api/sessions/{session_id}").json()
        if payload.get("status") in {"completed", "failed"}:
            return payload
        time.sleep(0.2)
    raise TimeoutError(session_id)


def test_application_fake_full_chain_memory_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTISENTINEL_PERSISTENCE_MODE", "sqlite")
    monkeypatch.setenv("ANTISENTINEL_SQLITE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("ANTISENTINEL_MODEL_MODE", "fake")
    monkeypatch.delenv("ANTISENTINEL_REDIS_URL", raising=False)
    monkeypatch.setenv("ANTISENTINEL_MEMORY_VECTOR_ENABLED", "0")

    service = DiagnosisApplicationService.from_environment()
    assert service.memory_recorder is not None
    client = TestClient(create_app(service))

    # Seed unique durable memories before sessions so recall_context can be checked after runs.
    now = datetime.now(timezone.utc)
    durable = service.memory_recorder.rollout_memory.durable
    durable.append_record(MemoryRecord.create(
        memory_id=new_memory_id(), memory_type="semantic", operator_id="operator-a",
        incident_id=None, session_id=None, content=SECRET_A,
        source_refs=(MemorySourceRef("session", "seed-a"),), extraction_confidence=0.95, valid_from=now,
    ))
    durable.append_record(MemoryRecord.create(
        memory_id=new_memory_id(), memory_type="semantic", operator_id="operator-b",
        incident_id=None, session_id=None, content=SECRET_B,
        source_refs=(MemorySourceRef("session", "seed-b"),), extraction_confidence=0.95, valid_from=now,
    ))

    for operator, title in (("operator-a", "A login 500"), ("operator-b", "B redis timeout")):
        incident = client.post("/api/incidents", json={"title": title, "summary": title, "source": "operator"}).json()
        started = client.post(
            f"/api/incidents/{incident['incident_id']}/sessions",
            json={"participant_ids": [operator], "model_mode": "fake"},
        ).json()
        result = _wait_session(client, started["session_id"])
        assert result["status"] == "completed"

        view = service.memory_recorder.recall_context(
            session_id=started["session_id"],
            operator_id=operator,
            incident_id=incident["incident_id"],
            query="preference secret",
            token_budget=400,
        )
        if operator == "operator-a":
            assert SECRET_A in view.digest
            assert SECRET_B not in view.digest
            assert SECRET_B not in str(result)
        else:
            assert SECRET_B in view.digest
            assert SECRET_A not in view.digest
            assert SECRET_A not in str(result)

    scoped_a = durable.list_by_scope(MemoryNamespace(
        tenant_id="default", operator_id="operator-a", agent_id="diagnosis-agent",
    ))
    assert any(SECRET_A in item.get("content", "") for item in scoped_a)
    assert all(SECRET_B not in item.get("content", "") for item in scoped_a)


def _load_dotenv_keys() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


@pytest.mark.integration
def test_application_real_full_chain_memory_isolation(tmp_path, monkeypatch):
    dotenv = _load_dotenv_keys()
    api_key = dotenv.get("ANTISENTINEL_MODEL_API_KEY") or os.getenv("ANTISENTINEL_MODEL_API_KEY", "")
    if not api_key:
        pytest.skip("ANTISENTINEL_MODEL_API_KEY required for real isolation smoke")

    # Host proxy often breaks localhost/testclient + outbound LLM unless bypassed.
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("ANTISENTINEL_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTISENTINEL_PERSISTENCE_MODE", "sqlite")
    monkeypatch.setenv("ANTISENTINEL_SQLITE_PATH", str(tmp_path / "real-iso.db"))
    monkeypatch.setenv("ANTISENTINEL_MODEL_MODE", "real")
    monkeypatch.setenv("ANTISENTINEL_MODEL_API_KEY", api_key)
    monkeypatch.setenv(
        "ANTISENTINEL_MODEL_BASE_URL",
        dotenv.get("ANTISENTINEL_MODEL_BASE_URL") or os.getenv("ANTISENTINEL_MODEL_BASE_URL", "https://api.deepseek.com"),
    )
    monkeypatch.setenv(
        "ANTISENTINEL_MODEL_NAME",
        dotenv.get("ANTISENTINEL_MODEL_NAME") or os.getenv("ANTISENTINEL_MODEL_NAME", "deepseek-v4-flash"),
    )
    monkeypatch.delenv("ANTISENTINEL_REDIS_URL", raising=False)
    monkeypatch.setenv("ANTISENTINEL_MEMORY_VECTOR_ENABLED", "0")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)

    service = DiagnosisApplicationService.from_environment()
    assert service.memory_recorder is not None
    client = TestClient(create_app(service))

    now = datetime.now(timezone.utc)
    durable = service.memory_recorder.rollout_memory.durable
    durable.append_record(MemoryRecord.create(
        memory_id=new_memory_id(), memory_type="semantic", operator_id="real-op-a",
        incident_id=None, session_id=None, content=SECRET_A,
        source_refs=(MemorySourceRef("session", "seed-a"),), extraction_confidence=0.95, valid_from=now,
    ))
    durable.append_record(MemoryRecord.create(
        memory_id=new_memory_id(), memory_type="semantic", operator_id="real-op-b",
        incident_id=None, session_id=None, content=SECRET_B,
        source_refs=(MemorySourceRef("session", "seed-b"),), extraction_confidence=0.95, valid_from=now,
    ))

    results = {}
    for operator, title in (("real-op-a", "intermittent login 500"), ("real-op-b", "redis pool timeout")):
        incident = client.post(
            "/api/incidents",
            json={"title": title, "summary": f"{title}; do not invent other operator secrets", "source": "operator"},
        ).json()
        started = client.post(
            f"/api/incidents/{incident['incident_id']}/sessions",
            json={"participant_ids": [operator], "model_mode": "real"},
        ).json()
        result = _wait_session(client, started["session_id"], timeout_s=180.0)
        assert result["status"] == "completed", result.get("error") or result
        results[operator] = (incident, started["session_id"], result)

        view = service.memory_recorder.recall_context(
            session_id=started["session_id"],
            operator_id=operator,
            incident_id=incident["incident_id"],
            query="secret isolation marker",
            token_budget=400,
        )
        serialized = str(result)
        if operator == "real-op-a":
            assert SECRET_A in view.digest
            assert SECRET_B not in view.digest
            assert SECRET_B not in serialized
        else:
            assert SECRET_B in view.digest
            assert SECRET_A not in view.digest
            assert SECRET_A not in serialized

    # Storage-layer proof after real runs
    for operator, secret, other in (
        ("real-op-a", SECRET_A, SECRET_B),
        ("real-op-b", SECRET_B, SECRET_A),
    ):
        rows = durable.list_by_scope(MemoryNamespace(
            tenant_id="default", operator_id=operator, agent_id="diagnosis-agent",
        ))
        blob = " ".join(item.get("content", "") for item in rows)
        assert secret in blob
        assert other not in blob
