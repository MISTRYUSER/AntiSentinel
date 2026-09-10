from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.retrieval.models import CodeSearchScope
from antisentinel.retrieval.vector import EmbeddingTaskStore, ReconciliationReport, VectorPoint
from pymilvus import DataType
import pytest


class FakeMilvusClient:
    def __init__(self):
        self.collections = {}

    def has_collection(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name, **kwargs):
        self.collections[collection_name] = {"rows": {}, **kwargs}

    def describe_collection(self, collection_name, **kwargs):
        schema = self.collections[collection_name]["schema"]
        return {"auto_id": False, "fields": [{"name": field.name, "type": field.dtype, "params": dict(field.params), "is_primary": field.is_primary} for field in schema.fields]}

    def list_indexes(self, collection_name, **kwargs):
        return ["vector"]

    def describe_index(self, collection_name, index_name, **kwargs):
        return {"index_type": "FLAT", "metric_type": "COSINE"}

    def load_collection(self, collection_name, **kwargs):
        return None

    def upsert(self, collection_name, data, **kwargs):
        self.collections[collection_name]["rows"].update({row["point_id"]: row for row in data})
        return {"upsert_count": len(data)}

    def get(self, collection_name, ids, output_fields=None, **kwargs):
        rows = self.collections[collection_name]["rows"]
        return [rows[point_id] for point_id in ids if point_id in rows]

    def query(self, collection_name, filter="", output_fields=None, **kwargs):
        return list(self.collections[collection_name]["rows"].values())


def point(point_id="point-a"):
    return VectorPoint(
        point_id=point_id, vector=(1.0, 0.0, 0.0),
        source_identity={"repository_id": "repo", "snapshot_id": "snapshot-a", "published_generation": 1, "commit_sha": "commit-snapshot-a", "node_id": "node-a", "chunk_id": "chunk-a", "path": "src/a.py", "source_hash": "source-a"},
        input_hash="input-a", model_revision="model-v1", dimension=3,
        template_revision="template-v1", projection_revision="projection-v1",
        document_id=f"document-{point_id}",
    )


def test_reconcile_reports_missing_extra_and_field_mismatch(tmp_path):
    client = FakeMilvusClient()
    adapter = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", client=client)
    adapter.ensure_collection()
    adapter.upsert([point("point-a")])
    adapter.upsert([point("point-extra")])

    expected_a, expected_missing = point("point-a"), point("point-missing")
    report = adapter.reconcile(
        [expected_a, expected_missing],
        expected_fields={expected_a.point_id: {"model_revision": "wrong-model"}, expected_missing.point_id: {}},
    )

    expected_a, expected_missing, actual_extra = point("point-a"), point("point-missing"), point("point-extra")
    assert report.expected_ids == frozenset({expected_a.point_id, expected_missing.point_id})
    assert report.actual_ids == frozenset({expected_a.point_id, actual_extra.point_id})
    assert report.missing_ids == frozenset({expected_missing.point_id})
    assert report.extra_ids == frozenset({actual_extra.point_id})
    assert report.field_mismatches == {expected_a.point_id: ("model_revision",)}
    assert report.is_consistent is False


def test_channel_ready_requires_empty_reconciliation_report(tmp_path):
    tasks = EmbeddingTaskStore(SQLiteDatabase(tmp_path / "facts.db"))
    scope = CodeSearchScope("repo", "snapshot-a", 1, "commit-snapshot-a")
    tasks.set_channel_status(*scope.key, "building")
    inconsistent = ReconciliationReport(
        expected_ids=frozenset({"point-a"}), actual_ids=frozenset(), missing_ids=frozenset({"point-a"}), extra_ids=frozenset(), field_mismatches={}
    )
    assert tasks.mark_channel_ready(scope, inconsistent) is False
    assert tasks.get_channel_status(*scope.key) == "building"

    consistent = ReconciliationReport(frozenset({"point-a"}), frozenset({"point-a"}), frozenset(), frozenset(), {})
    assert tasks.mark_channel_ready(scope, consistent) is False
    assert tasks.get_channel_status(*scope.key) == "building"


def test_reconcile_rejects_points_from_multiple_scopes(tmp_path):
    adapter = MilvusAdapter(tmp_path / "milvus.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", client=FakeMilvusClient())

    with pytest.raises(ValueError, match="single Scope"):
        adapter.reconcile([point("point-a"), VectorPoint(**{**point("point-b").__dict__, "source_identity": {**point("point-b").source_identity, "snapshot_id": "snapshot-b", "commit_sha": "commit-snapshot-b"}})])


@pytest.mark.parametrize('missing', ['source_hash', 'input_hash', 'document_id', 'projection_revision'])
def test_reconcile_rejects_absent_required_fields(tmp_path, missing):
    client = FakeMilvusClient()
    adapter = MilvusAdapter(tmp_path/'vectors.db', 'vectors', dimension=3, model_revision='model-v1', template_revision='template-v1', projection_revision='projection-v1', client=client)
    expected = point()
    adapter.upsert([expected])
    client.collections[adapter.collection_name]['rows'][expected.point_id].pop(missing)
    report = adapter.reconcile([expected])
    assert not report.is_consistent
    assert missing in report.field_mismatches[expected.point_id]
