from datetime import datetime, timezone

from antisentinel.memory.hybrid_ranker import RetrievalCandidate
from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord, MemorySourceRef
from antisentinel.persistence.local_vector_memory import LocalVectorMemory
from antisentinel.persistence.sqlite_database import SQLiteDatabase


class Embedder:
    model_name = "test-vector"; dimension = 2
    def embed_documents(self, texts): return [[1.0, 0.0] for _ in texts]
    def embed_query(self, texts): return [[1.0, 0.0] for _ in texts]
    def embed(self, texts): return self.embed_documents(texts)


def test_vector_candidates_respect_scope_and_version(tmp_path):
    db = SQLiteDatabase(tmp_path / "v.db"); db.initialize()
    record = MemoryRecord.create(memory_id="match", memory_type="episodic", operator_id="op", incident_id="inc", session_id="s", content="语义内容", source_refs=(MemorySourceRef("session", "s"),), extraction_confidence=.9, valid_from=datetime(2026, 9, 5, tzinfo=timezone.utc))
    memory = LocalVectorMemory(db, Embedder()); memory.upsert(record.to_dict())

    result = memory.vector_candidates("改写查询", AuthorizedMemoryScope("op", "inc", "s"), limit=5)

    assert result == (RetrievalCandidate("match", "vector", 1, 1.0),)
    assert memory.channel_status["vector"] == "active"


def test_vector_embedding_failure_returns_bypass(tmp_path):
    class Failing(Embedder):
        def embed_query(self, texts): raise RuntimeError("embedding_transport_error")
    db = SQLiteDatabase(tmp_path / "v.db"); db.initialize()
    memory = LocalVectorMemory(db, Failing())

    assert memory.vector_candidates("q", AuthorizedMemoryScope("op", "inc", "s"), limit=5) == ()
    assert memory.channel_status["vector"] == "bypass"
