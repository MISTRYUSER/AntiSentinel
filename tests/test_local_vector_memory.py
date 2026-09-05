from antisentinel.persistence.local_vector_memory import LocalVectorMemory
from antisentinel.persistence.sqlite_database import SQLiteDatabase


class FakeEmbedder:
    model_name = "fake-embedding"
    dimension = 3

    def embed(self, texts):
        return [[1.0, 0.0, 0.0] if "日志" in text else [0.0, 1.0, 0.0] for text in texts]

    def embed_query(self, texts):
        return self.embed(texts)

    def embed_documents(self, texts):
        return self.embed(texts)


def test_local_vector_memory_upserts_and_searches_with_scope(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    memory = LocalVectorMemory(database, FakeEmbedder())
    memory.upsert({"memory_id": "m-log", "operator_id": "op-1", "incident_id": "i-1", "content": "先看日志", "status": "active"})
    memory.upsert({"memory_id": "m-other", "operator_id": "op-2", "incident_id": "i-1", "content": "先看日志", "status": "active"})

    results = memory.search("日志", operator_id="op-1", incident_id="i-1", limit=5)

    assert [item["memory_id"] for item in results] == ["m-log"]
    assert results[0]["vector_score"] == 1.0


def test_local_vector_memory_uses_query_and_document_embedding_paths(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()

    class DirectionalEmbedder:
        model_name = "directional"; dimension = 2
        def __init__(self): self.calls = []
        def embed_query(self, texts): self.calls.append("query"); return [[1.0, 0.0] for _ in texts]
        def embed_documents(self, texts): self.calls.append("documents"); return [[1.0, 0.0] if "日志" in text else [0.0, 1.0] for text in texts]
        def embed(self, texts): return [[0.0, 1.0] for _ in texts]

    embedder = DirectionalEmbedder()
    memory = LocalVectorMemory(database, embedder)
    memory.upsert({"memory_id": "m-log", "operator_id": "op-1", "content": "日志"})
    memory.upsert({"memory_id": "m-other", "operator_id": "op-1", "content": "指标"})

    assert memory.search("日志", operator_id="op-1", limit=1)[0]["memory_id"] == "m-log"
    assert embedder.calls == ["documents", "documents", "query"]


def test_local_vector_memory_rejects_dimension_mismatch(tmp_path):
    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    memory = LocalVectorMemory(database, FakeEmbedder())

    try:
        memory.upsert({"memory_id": "m-1", "operator_id": "op-1", "content": "x", "embedding": [1.0, 0.0]})
    except ValueError as exc:
        assert "dimension" in str(exc)
    else:
        raise AssertionError("dimension mismatch was accepted")


def test_vector_search_excludes_stale_content_and_other_models(tmp_path):
    from antisentinel.persistence.sqlite_stores import SQLiteMemoryStore

    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    memory = LocalVectorMemory(database, FakeEmbedder())
    record = {"memory_id": "m-log", "operator_id": "op-1", "content": "日志", "content_version": 1}
    memory.upsert(record)
    SQLiteMemoryStore(database).replace({**record, "content": "指标", "content_version": 2})

    assert memory.search("日志", operator_id="op-1") == []

    memory.upsert({**record, "content_version": 2})
    with database.transaction() as connection:
        connection.execute("UPDATE memory_vectors SET embedding_model='old-model'")
    assert memory.search("日志", operator_id="op-1") == []
