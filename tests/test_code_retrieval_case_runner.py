import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from antisentinel.retrieval.vector import ReconciliationReport, VectorSearchHit
from scripts.run_code_retrieval_case import evaluate_case_report, run_hybrid_case, run_keyword_case, run_vector_case


def test_runner_rejects_preexisting_output_directory(tmp_path):
    output = tmp_path / "case"
    output.mkdir()

    with pytest.raises(FileExistsError):
        run_keyword_case(output)


def test_runner_persists_two_commits_and_reopens_manifest_and_fts(tmp_path):
    report = run_keyword_case(tmp_path / "case")

    assert report["case_pass"] is True
    assert report["input_files"] == 2
    assert report["output_count"] == 2
    assert report["persisted_documents"] == 2
    assert report["integrity_checks"] >= 4
    assert report["background_exceptions"] == 0
    assert report["artifacts"] == len(list((tmp_path / "case").iterdir()))
    assert json.loads((tmp_path / "case" / "report.json").read_text())["case_pass"] is True


def test_case_report_without_numeric_field_cannot_pass():
    report = {
        "output_count": 1,
        "persisted_documents": 1,
        "integrity_checks": 1,
        "background_exceptions": 0,
        "failures": 0,
    }

    assert evaluate_case_report(report) is False


def test_cli_runner_bootstraps_src_path(tmp_path):
    output = tmp_path / "cli-case"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts/run_code_retrieval_case.py"),
         "--case", "keyword", "--output", str(output), "--timeout", "120"],
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads((output / "report.json").read_text())["case_pass"] is True


class FakeVectorAdapter:
    rows_by_uri = {}
    events = []

    def __init__(self, uri, collection_name, *, dimension, model_revision, template_revision, projection_revision):
        self.uri = str(uri)
        self.events.append("open")
        self.rows = self.rows_by_uri.setdefault(self.uri, {})

    def ensure_collection(self):
        return None

    def upsert(self, points):
        self.rows.update({point.point_id: point for point in points})
        return len(points)

    def reconcile(self, points):
        expected = frozenset(point.point_id for point in points)
        snapshot_id = next(iter(points)).source_identity["snapshot_id"]
        actual = frozenset(point.point_id for point in self.rows.values() if point.source_identity["snapshot_id"] == snapshot_id)
        return ReconciliationReport(expected, actual, expected - actual, actual - expected, {})

    def search(self, vector, scope, **kwargs):
        rows = [point for point in self.rows.values() if point.source_identity["snapshot_id"] == scope.snapshot_id]
        return tuple(VectorSearchHit(point.point_id, 1.0, dict(point.source_identity), document_id=point.document_id) for point in rows)

    def get(self, point_ids):
        return [point.__dict__ | {"point_id": point.point_id} for point in self.rows.values() if point.point_id in point_ids]

    def close(self):
        self.events.append("close")
        return None


def test_vector_runner_contract_without_external_server(tmp_path):
    FakeVectorAdapter.events.clear()
    report = run_vector_case(tmp_path / "vector-case", adapter_factory=FakeVectorAdapter)

    assert report["case_pass"] is True
    assert report["output_count"] == 2
    assert report["persisted_vectors"] == 2
    assert report["persisted_manifests"] == 2
    assert report["integrity_checks"] == 6
    assert report["background_exceptions"] == 0
    assert FakeVectorAdapter.events == ["open", "close", "open", "close"]


def test_hybrid_runner_records_three_modes_without_external_server(tmp_path):
    report = run_hybrid_case(tmp_path / "hybrid-case", adapter_factory=FakeVectorAdapter)

    assert report["case_pass"] is True
    assert report["query_count"] == 20
    assert report["persisted_documents"] == 4
    assert report["output_count"] == 4
    assert report["output_count"] == report["mode_unique_output_counts"]["hybrid"]
    assert set(report["mode_metrics"]) == {"keyword", "vector", "hybrid"}
    assert report["mode_errors"] == {"keyword": 0, "vector": 0, "hybrid": 0}
    assert report["mode_metrics"]["vector"]["recall_at_5"] == 1.0
    assert report["hybrid_duplicate_results"] == 0
    assert set(report["mode_latency_p95_ms"]) == {"keyword", "vector", "hybrid"}
    assert all(report["mode_latency_p95_ms"][mode] >= report["mode_latency_p50_ms"][mode] for mode in report["mode_latency_p95_ms"])
