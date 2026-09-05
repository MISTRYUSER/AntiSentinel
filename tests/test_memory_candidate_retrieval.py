from datetime import datetime, timezone

from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord, MemorySourceRef
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteMemoryCandidateStore, SQLiteMemoryStore


def record(memory_id, content, *, operator="operator-1", incident="incident-1", status="active"):
    return MemoryRecord.create(memory_id=memory_id, memory_type="episodic", operator_id=operator, incident_id=incident, session_id="session-1", content=content, source_refs=(MemorySourceRef("session", "session-1"),), extraction_confidence=.9, valid_from=datetime(2026, 9, 5, tzinfo=timezone.utc), status=status)


def test_identifier_and_lexical_candidates_respect_scope_and_status(tmp_path):
    database = SQLiteDatabase(tmp_path / "memory.db"); database.initialize()
    records = SQLiteMemoryStore(database)
    records.append_record(record("match", "ERR_TIMEOUT_504 cache:tenant:1 upstream timeout"))
    records.append_record(record("other-operator", "ERR_TIMEOUT_504", operator="operator-2"))
    records.append_record(record("inactive", "ERR_TIMEOUT_504", status="superseded"))
    candidates = SQLiteMemoryCandidateStore(database)
    scope = AuthorizedMemoryScope("operator-1", "incident-1", "session-1")

    identifiers = candidates.identifier_candidates("why ERR_TIMEOUT_504 for cache:tenant:1?", scope, limit=5)
    lexical = candidates.lexical_candidates("upstream timeout", scope, limit=5)

    assert [item.memory_id for item in identifiers] == ["match"]
    assert identifiers[0].channel == "identifier"
    assert [item.memory_id for item in lexical] == ["match"]


def test_lexical_failure_returns_bypass_without_exposing_other_scope(tmp_path, monkeypatch):
    database = SQLiteDatabase(tmp_path / "memory.db"); database.initialize()
    candidates = SQLiteMemoryCandidateStore(database)
    monkeypatch.setattr(database, "query", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fts unavailable")))

    result = candidates.lexical_candidates("timeout", AuthorizedMemoryScope("operator-1", "incident-1", "session-1"), limit=5)

    assert result == ()
    assert candidates.channel_status["lexical"] == "bypass"
