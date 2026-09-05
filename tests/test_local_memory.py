from antisentinel.persistence.local_memory import LocalLongTermMemory
from antisentinel.persistence.sqlite_database import SQLiteDatabase


def test_local_long_term_memory_writes_and_reads_versioned_records(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    memory = LocalLongTermMemory(database)
    record = {
        "memory_id": "preference:op-1:order:v1", "memory_type": "preference", "operator_id": "op-1",
        "subject": "diagnosis_order", "predicate": "prefers", "object": "logs_before_metrics",
        "content": "排查时先看日志，再看指标", "status": "active", "confidence": 0.9,
        "content_version": 1, "source_refs": [{"type": "evidence", "id": "e-1", "role": "supporting"}],
    }

    stored = memory.remember(record)

    assert stored["memory_id"] == record["memory_id"]
    assert memory.get(record["memory_id"]) == stored
    assert memory.search("日志", operator_id="op-1")[0]["memory_id"] == record["memory_id"]


def test_local_long_term_memory_survives_new_facade_instance(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    LocalLongTermMemory(database).remember({
        "memory_id": "episodic:session-1", "memory_type": "episodic", "operator_id": "op-1",
        "content": "Redis 曾经因连接池耗尽失败", "status": "active", "confidence": 0.8,
        "content_version": 1,
    })

    restored = LocalLongTermMemory(database)

    assert restored.search("连接池", operator_id="op-1")[0]["memory_id"] == "episodic:session-1"
