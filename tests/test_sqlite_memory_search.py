from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteMemoryStore


def test_memory_store_fts_search_ranks_and_filters_records(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    store = SQLiteMemoryStore(database)
    store.append({
        "memory_id": "memory-log", "memory_type": "preference", "operator_id": "op-1",
        "incident_id": "incident-1", "subject": "排查顺序", "predicate": "偏好",
        "object": "日志优先", "content": "排查故障时先看日志，再看指标", "status": "active", "confidence": 0.9,
    })
    store.append({
        "memory_id": "memory-other", "memory_type": "preference", "operator_id": "op-2",
        "incident_id": "incident-1", "content": "日志相关但属于其他用户", "status": "active", "confidence": 0.9,
    })
    store.append({
        "memory_id": "memory-expired", "memory_type": "preference", "operator_id": "op-1",
        "incident_id": "incident-1", "content": "先看日志的旧偏好", "status": "expired", "confidence": 1.0,
    })

    results = store.search("日志", operator_id="op-1", incident_id="incident-1", limit=5)

    assert [item["memory_id"] for item in results] == ["memory-log"]
    assert results[0]["search_score"] >= 0


def test_memory_store_rebuilds_search_index_after_replace(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    store = SQLiteMemoryStore(database)
    store.append({"memory_id": "memory-1", "memory_type": "semantic", "operator_id": "op-1", "content": "旧内容"})
    store.replace({"memory_id": "memory-1", "memory_type": "semantic", "operator_id": "op-1", "content": "新内容"})

    assert store.search("旧内容", operator_id="op-1", limit=5) == []
    assert [item["memory_id"] for item in store.search("新内容", operator_id="op-1", limit=5)] == ["memory-1"]


def test_memory_store_appends_many_records_in_one_transaction(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    store = SQLiteMemoryStore(database)
    records = [{"memory_id": f"memory-{i}", "memory_type": "turn_summary", "operator_id": "op-1", "content": f"日志记录 {i}"} for i in range(20)]

    store.append_many(records)

    assert database.query("SELECT COUNT(*) AS n FROM memory_records")[0]["n"] == 20
    assert len(store.search("日志", operator_id="op-1", limit=20)) == 20
