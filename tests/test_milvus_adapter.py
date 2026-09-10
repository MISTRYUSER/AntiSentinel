import pytest
from pymilvus import DataType

from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.models import CodeSearchScope
from antisentinel.retrieval.vector import EmbeddingTaskStore, VectorPoint
from antisentinel.retrieval.milvus_adapter import MilvusAdapter


def test_authenticated_connection_has_a_bounded_connect_timeout(monkeypatch):
    import antisentinel.retrieval.milvus_adapter as module
    connections = []
    client = object()
    def connect(**kwargs):
        connections.append(kwargs)
        return client
    monkeypatch.setattr(module, 'MilvusClient', connect)
    adapter = MilvusAdapter('http://localhost:29530', 'test', dimension=3,
        model_revision='m', template_revision='t', projection_revision='p',
        token='fixture-user:fixture-secret', rpc_timeout=5)
    assert connections == [{'uri': 'http://localhost:29530', 'timeout': 5, 'token': 'fixture-user:fixture-secret'}]
    assert adapter.client is client
    assert 'token' not in vars(adapter)


def test_reconciliation_and_id_reads_request_strong_consistency(monkeypatch):
    class RecordingClient:
        def __init__(self): self.calls = []
        def get(self, **kwargs): self.calls.append(kwargs); return []
        def query(self, **kwargs): self.calls.append(kwargs); return []
    client = RecordingClient()
    value = point()
    adapter = MilvusAdapter('http://localhost:29530', 'test', dimension=value.dimension,
        model_revision=value.model_revision, template_revision=value.template_revision,
        projection_revision=value.projection_revision, client=client)
    monkeypatch.setattr(adapter, 'ensure_collection', lambda: None)
    adapter.get([value.point_id])
    adapter.reconcile([value])
    assert len(client.calls) == 2
    assert all(call['consistency_level'] == 'Strong' for call in client.calls)


class FakeMilvusClient:
    def __init__(self):
        self.collections = {}
        self.upserts = []
        self.last_filter = None
        self.closed = False
        self.loaded = False

    def has_collection(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, **kwargs):
        self.collections[collection_name] = kwargs
        self.collections[collection_name]["rows"] = {}

    def describe_collection(self, collection_name, **kwargs):
        collection = self.collections[collection_name]
        if "bad_dimension" in collection:
            return {"auto_id": False, "fields": [
                {"name": "point_id", "type": DataType.VARCHAR, "params": {"max_length": 128}, "is_primary": True},
                {"name": "vector", "type": DataType.FLOAT_VECTOR, "params": {"dim": collection["bad_dimension"]}},
            ]}
        schema = collection["schema"]
        return {"auto_id": False, "fields": [
            {"name": field.name, "type": field.dtype, "params": dict(field.params), "is_primary": field.is_primary}
            for field in schema.fields
        ]}

    def list_indexes(self, collection_name, **kwargs):
        return ["vector"]

    def describe_index(self, collection_name, index_name, **kwargs):
        return {"index_type": "FLAT", "metric_type": "COSINE"}

    def upsert(self, collection_name, data, **kwargs):
        self.upserts.append((collection_name, data))
        self.collections.setdefault(collection_name, {}).setdefault("rows", {})
        self.collections[collection_name]["rows"].update({row["point_id"]: row for row in data})
        return {"upsert_count": len(data)}

    def get(self, collection_name, ids, output_fields=None, **kwargs):
        rows = self.collections[collection_name].get("rows", {})
        return [rows[point_id] for point_id in ids if point_id in rows]

    def search(self, collection_name, data, filter, limit, output_fields=None, **kwargs):
        self.last_filter = filter
        rows = list(self.collections[collection_name].get("rows", {}).values())[:limit]
        return [[{"id": row["point_id"], "distance": 0.99, "entity": row} for row in rows]]

    def close(self):
        self.closed = True

    def load_collection(self, collection_name, **kwargs):
        self.loaded = True


class ReadyChannelStore:
    def __init__(self, status="ready"):
        self.status = status

    def get_channel_status(self, *scope, **versions):
        return self.status

    def verify_vector_rows(self, rows, scope, **versions):
        # Adapter unit-test double; real SQLite authority is tested separately.
        self.verified_rows = rows


def point(point_id="point-a", *, snapshot_id="snapshot-a"):
    return VectorPoint(
        point_id=point_id,
        vector=(1.0, 0.0, 0.0),
        source_identity={
            "repository_id": "repo",
            "snapshot_id": snapshot_id,
            "published_generation": 1,
            "commit_sha": f"commit-{snapshot_id}",
            "node_id": "node-a",
            "chunk_id": "chunk-a",
            "path": "src/a.py",
            "source_hash": "source-a",
        },
        input_hash="input-a",
        model_revision="model-v1",
        dimension=3,
        template_revision="template-v1",
        projection_revision="projection-v1",
        document_id=f"document-{point_id}",
    )


def test_adapter_schema_upsert_is_idempotent_and_search_filters_full_scope(tmp_path):
    client = FakeMilvusClient()
    adapter = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", client=client)
    adapter.ensure_collection()
    assert client.loaded is True
    adapter.upsert([point(), point()])

    scope = CodeSearchScope("repo", "snapshot-a", 1, "commit-snapshot-a")
    hits = adapter.search(
        (1.0, 0.0, 0.0),
        scope,
        model_revision="model-v1",
        template_revision="template-v1",
        projection_revision="projection-v1",
        limit=5,
        channel_store=ReadyChannelStore(),
    )

    schema = client.collections[adapter.collection_name]["schema"]
    assert [field.name for field in schema.fields if field.is_primary] == ["point_id"]
    assert len(client.collections[adapter.collection_name]["rows"]) == 1
    assert len(hits) == 1
    assert hits[0].point_id == point().point_id
    assert hits[0].document_id == "document-point-a"
    assert hits[0].source_identity["snapshot_id"] == "snapshot-a"
    for required in ("repository_id", "snapshot_id", "published_generation", "commit_sha", "model_revision", "dimension", "template_revision", "projection_revision"):
        assert required in client.last_filter


def test_adapter_rejects_dimension_or_scope_mismatch(tmp_path):
    adapter = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", client=FakeMilvusClient())
    adapter.ensure_collection()
    with pytest.raises(ValueError, match="dimension"):
        adapter.upsert([VectorPoint(**{**point().__dict__, "dimension": 2})])
    hits = adapter.search((1.0, 0.0, 0.0), CodeSearchScope("repo", "snapshot-a", 1, "wrong"), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", channel_store=ReadyChannelStore())
    assert hits == ()
    assert 'commit_sha == "wrong"' in adapter.client.last_filter


def test_point_id_changes_when_embedding_identity_changes():
    base = point()
    changed_model = VectorPoint(**{**base.__dict__, "model_revision": "model-v2"})
    changed_template = VectorPoint(**{**base.__dict__, "template_revision": "template-v2"})

    assert base.point_id != changed_model.point_id
    assert base.point_id != changed_template.point_id


def test_existing_collection_schema_mismatch_is_rejected(tmp_path):
    client = FakeMilvusClient()
    adapter = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", client=client)
    client.collections[adapter.collection_name] = {"schema": None, "bad_dimension": 768, "rows": {}}

    with pytest.raises(ValueError, match="schema mismatch"):
        adapter.ensure_collection()


def test_collection_name_isolated_by_embedding_version(tmp_path):
    first = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", client=FakeMilvusClient())
    second = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v2", template_revision="template-v1", projection_revision="projection-v1", client=FakeMilvusClient())

    assert first.collection_name != second.collection_name


def test_embedding_task_and_channel_states_are_separate(tmp_path):
    tasks = EmbeddingTaskStore(SQLiteDatabase(tmp_path / "facts.db"))
    tasks.create_task("task-a", "point-a", "input-a", "model-v1", 3, "template-v1", "projection-v1")
    assert tasks.get_task_status("task-a") == "pending"
    tasks.set_task_status("task-a", "running")
    tasks.set_task_status("task-a", "failed", retryable=False)
    tasks.set_channel_status("repo", "snapshot-a", 1, "commit-snapshot-a", "blocked")
    assert tasks.get_task_status("task-a") == "failed"
    assert tasks.get_channel_status("repo", "snapshot-a", 1, "commit-snapshot-a") == "blocked"


def test_embedding_task_records_attempt_diagnostics_and_rejects_unknown_task(tmp_path):
    tasks = EmbeddingTaskStore(SQLiteDatabase(tmp_path / "facts.db"))
    tasks.create_task("task-a", "point-a", "input-a", "model-v1", 3, "template-v1", "projection-v1")
    tasks.set_task_status("task-a", "running", attempt=1, lease_owner="worker-a")
    tasks.set_task_status("task-a", "failed", retryable=False, error_code="auth", error_message="redacted", duration_ms=12.5)
    record = tasks.get_task("task-a")

    assert record["attempt"] == 1
    assert record["lease_owner"] == "worker-a"
    assert record["started_at"] is not None
    assert record["completed_at"] is not None
    assert record["duration_ms"] == 12.5
    assert record["error_code"] == "auth"
    assert record["error_message"] == "redacted"
    with pytest.raises(KeyError):
        tasks.set_task_status("missing", "failed")


def test_search_rejects_a_building_channel(tmp_path):
    client = FakeMilvusClient()
    adapter = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", client=client)
    adapter.ensure_collection()

    with pytest.raises(RuntimeError, match="channel_not_ready"):
        adapter.search((1.0, 0.0, 0.0), CodeSearchScope("repo", "snapshot-a", 1, "commit-snapshot-a"), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", channel_store=ReadyChannelStore("building"))
