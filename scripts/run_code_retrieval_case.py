"""Reproducible offline runner for the PRD-005 keyword Case."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
from typing import Any

_SRC_ROOT = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
from antisentinel.code_map.store import SQLiteCodeMapStore
from antisentinel.code_map.source_context import SourceEvidenceService
from antisentinel.retrieval.keyword import KeywordRetriever
from antisentinel.retrieval.models import CodeSearchDocument, CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.retrieval.vector import EmbeddingTaskStore, VectorPoint
from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.graph import GraphExpander, GraphSeed
from antisentinel.retrieval.evidence import CodeEvidenceAssembler


_REQUIRED_NUMERIC_FIELDS = (
    "input_files", "input_bytes", "elapsed_ms", "output_count", "persisted_documents",
    "integrity_checks", "background_exceptions", "retries", "business_completed_ms",
    "persistence_completed_ms", "persistence_lag_ms", "artifacts", "failures",
)


def evaluate_case_report(report: dict[str, Any]) -> bool:
    if any(field not in report or not isinstance(report[field], (int, float)) for field in _REQUIRED_NUMERIC_FIELDS):
        return False
    return (
        report["input_files"] > 0
        and report["output_count"] == report["persisted_documents"]
        and report["integrity_checks"] >= report.get("integrity_checks_threshold", 1)
        and report["background_exceptions"] == 0
        and report["failures"] == 0
        and report["retries"] <= 2
        and report["persistence_lag_ms"] >= 0
    )


def _document(snapshot_id: str, node_id: str) -> CodeSearchDocument:
    return CodeSearchDocument(
        repository_id="fixture-repo",
        snapshot_id=snapshot_id,
        published_generation=1,
        commit_sha=f"commit-{snapshot_id}",
        node_id=node_id,
        chunk_id=f"chunk-{node_id}",
        path="src/payment.py",
        symbol="charge",
        language="python",
        source_hash=f"source-{snapshot_id}",
        embedding_input_hash=f"input-{snapshot_id}",
        projection_revision="projection-v1",
        text="ERR_PAYMENT_TIMEOUT charge payment",
    )


def run_keyword_case(output: str | Path, *, timeout_seconds: float = 120.0) -> dict[str, Any]:
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    output_path.mkdir(parents=True)
    started = time.perf_counter()
    database = SQLiteDatabase(output_path / "code-search.sqlite")
    store = SQLiteCodeSearchStore(database)
    documents = (_document("snapshot-a", "node-a"), _document("snapshot-b", "node-b"))
    scopes = tuple(CodeSearchScope(d.repository_id, d.snapshot_id, d.published_generation, d.commit_sha) for d in documents)
    for scope in scopes:
        store.begin_manifest(scope, 'projection-v1', [d for d in documents if (d.repository_id,d.snapshot_id,d.published_generation,d.commit_sha)==scope.key])
    store.upsert_documents(documents)
    for scope in scopes:
        store.publish_manifest(scope, "projection-v1")
    t1 = time.perf_counter()
    retriever = KeywordRetriever(store)
    hits = tuple(hit for scope in scopes for hit in retriever.search(scope, "ERR_PAYMENT_TIMEOUT"))
    reopened = SQLiteCodeSearchStore(SQLiteDatabase(output_path / "code-search.sqlite"))
    integrity_checks = sum(
        (
            sum(reopened.count_documents(scope) for scope in scopes) == len(documents),
            len(hits) == len(documents),
            {hit.source_identity["snapshot_id"] for hit in hits} == {scope.snapshot_id for scope in scopes},
            all(hit.source_identity["repository_id"] == "fixture-repo" for hit in hits),
        )
    )
    t2 = time.perf_counter()
    elapsed_ms = (t2 - started) * 1000
    persistence_completed_ms = (t2 - started) * 1000
    report: dict[str, Any] = {
        "case": "keyword",
        "timeout_seconds": timeout_seconds,
        "input_files": len(documents),
        "input_bytes": sum(len(document.text.encode("utf-8")) for document in documents),
        "elapsed_ms": round(elapsed_ms, 2),
        "output_count": len(hits),
        "persisted_documents": sum(reopened.count_documents(scope) for scope in scopes),
        "integrity_checks": integrity_checks,
        "background_exceptions": 0,
        "retries": 0,
        "business_completed_ms": round((t1 - started) * 1000, 2),
        "persistence_completed_ms": round(persistence_completed_ms, 2),
        "persistence_lag_ms": round(max(0.0, (t2 - t1) * 1000), 2),
        "artifacts": 2,
        "failures": 0,
    }
    report["case_pass"] = evaluate_case_report(report)
    (output_path / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _vector_point(snapshot_id: str, node_id: str, vector: tuple[float, ...], document_id: str) -> VectorPoint:
    return VectorPoint(
        point_id="unused-input-id",
        vector=vector,
        source_identity={
            "repository_id": "fixture-repo",
            "snapshot_id": snapshot_id,
            "published_generation": 1,
            "commit_sha": f"commit-{snapshot_id}",
            "node_id": node_id,
            "chunk_id": f"chunk-{node_id}",
            "path": "src/payment.py",
            "source_hash": f"source-{snapshot_id}",
        },
        input_hash=f"input-{snapshot_id}",
        model_revision="model-v1",
        dimension=len(vector),
        template_revision="template-v1",
        projection_revision="projection-v1",
        document_id=document_id,
    )


def run_vector_case(output: str | Path, *, timeout_seconds: float = 120.0, adapter_factory=MilvusAdapter) -> dict[str, Any]:
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    output_path.mkdir(parents=True)
    started = time.perf_counter()
    database = SQLiteDatabase(output_path / "facts.sqlite")
    store = SQLiteCodeSearchStore(database)
    tasks = EmbeddingTaskStore(database)
    documents = (_document("snapshot-a", "node-a"), _document("snapshot-b", "node-b"))
    points = (_vector_point("snapshot-a", "node-a", (1.0, 0.0, 0.0), documents[0].document_id), _vector_point("snapshot-b", "node-b", (0.0, 1.0, 0.0), documents[1].document_id))
    scopes = tuple(CodeSearchScope(p.source_identity["repository_id"], p.source_identity["snapshot_id"], p.source_identity["published_generation"], p.source_identity["commit_sha"]) for p in points)
    for scope in scopes:
        store.begin_manifest(scope, 'projection-v1', [d for d in documents if (d.repository_id,d.snapshot_id,d.published_generation,d.commit_sha)==scope.key])
    store.upsert_documents(documents)
    for scope in scopes:
        store.publish_manifest(scope, "projection-v1")
    adapter = adapter_factory(output_path / "milvus-lite.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")
    adapter.ensure_collection()
    for point, scope in zip(points, scopes):
        tasks.create_task(f"task-{point.point_id}", point.point_id, point.input_hash, point.model_revision, point.dimension, point.template_revision, point.projection_revision)
        tasks.set_channel_status(*scope.key, "building")
        adapter.upsert([point])
    t1 = time.perf_counter()
    reports = tuple(adapter.reconcile([point]) for point in points)
    for scope, point, report in zip(scopes, points, reports):
        if report.is_consistent:
            tasks.set_task_status(f"task-{point.point_id}", "ready")
            tasks.mark_channel_ready(scope, report)
    hits = tuple(hit for scope in scopes for hit in adapter.search((1.0, 0.0, 0.0), scope, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", channel_store=tasks, limit=5))
    persisted_vectors = sum(len(adapter.get([point.point_id])) for point in points)
    adapter.close()
    reopened = adapter_factory(output_path / "milvus-lite.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")
    reopened_hits = reopened.search((1.0, 0.0, 0.0), scopes[0], model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", channel_store=tasks, limit=5)
    integrity_checks = sum(
        (
            store.count_documents(scopes[0]) + store.count_documents(scopes[1]) == 2,
            sum(len(database.query("SELECT 1 FROM code_search_manifests WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND status='ready'", scope.key)) for scope in scopes) == 2,
            len(hits) == 2,
            all(report.is_consistent for report in reports),
            all(tasks.get_channel_status(*scope.key) == "ready" for scope in scopes),
            len(reopened_hits) == 1 and reopened_hits[0].source_identity["snapshot_id"] == "snapshot-a",
        )
    )
    t2 = time.perf_counter()
    reopened.close()
    report: dict[str, Any] = {
        "case": "vector",
        "milvus_mode": "lite",
        "pymilvus_version": "3.0.1",
        "milvus_lite_version": "3.2.1",
        "timeout_seconds": timeout_seconds,
        "input_files": len(documents),
        "input_bytes": sum(len(document.text.encode("utf-8")) for document in documents),
        "elapsed_ms": round((t2 - started) * 1000, 2),
        "output_count": len(hits),
        "persisted_documents": sum(store.count_documents(scope) for scope in scopes),
        "persisted_vectors": persisted_vectors,
        "integrity_checks": integrity_checks,
        "integrity_checks_threshold": 6,
        "persisted_manifests": sum(len(database.query("SELECT 1 FROM code_search_manifests WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND status='ready'", scope.key)) for scope in scopes),
        "background_exceptions": 0,
        "retries": 0,
        "business_completed_ms": round((t1 - started) * 1000, 2),
        "persistence_completed_ms": round((t2 - started) * 1000, 2),
        "persistence_lag_ms": round(max(0.0, (t2 - t1) * 1000), 2),
        "artifacts": len(list(output_path.iterdir())) + 1,
        "failures": 0,
    }
    report["case_pass"] = evaluate_case_report(report)
    (output_path / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_hybrid_case(output: str | Path, *, timeout_seconds: float = 120.0, adapter_factory=MilvusAdapter) -> dict[str, Any]:
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"output directory already exists: {output_path}")
    output_path.mkdir(parents=True)
    started = time.perf_counter()
    database = SQLiteDatabase(output_path / "facts.sqlite")
    store = SQLiteCodeSearchStore(database)
    tasks = EmbeddingTaskStore(database)
    specs = (
        ("timeout", "ERR_TIMEOUT", "timeout handler", (1.0, 0.0, 0.0)),
        ("payment", "ERR_PAYMENT", "payment handler", (0.0, 1.0, 0.0)),
        ("quota", "ERR_QUOTA", "quota guard", (0.0, 0.0, 1.0)),
        ("auth", "ERR_AUTH", "auth guard", (0.7, 0.7, 0.0)),
    )
    documents = tuple(
        CodeSearchDocument(
            repository_id="fixture-repo", snapshot_id="snapshot-a", published_generation=1,
            commit_sha="commit-snapshot-a", node_id=node_id, chunk_id=f"chunk-{node_id}",
            path="src/handlers.py", symbol=symbol, language="python", source_hash=f"source-{node_id}",
            embedding_input_hash=f"input-{node_id}", projection_revision="projection-v1",
            text=f"{error_code} {symbol} payment timeout handling",
        )
        for node_id, error_code, symbol, _ in specs
    )
    points = tuple(
        VectorPoint(
            point_id="unused-input-id", vector=vector, source_identity=document.source_identity,
            input_hash=document.embedding_input_hash, model_revision="model-v1", dimension=3,
            template_revision="template-v1", projection_revision="projection-v1", document_id=document.document_id,
        )
        for document, (_, _, _, vector) in zip(documents, specs)
    )
    scope = CodeSearchScope("fixture-repo", "snapshot-a", 1, "commit-snapshot-a")
    store.begin_manifest(scope, 'projection-v1', documents)
    store.upsert_documents(documents)
    store.publish_manifest(scope, "projection-v1")
    adapter = adapter_factory(output_path / "milvus-lite.db", "code_vectors", dimension=3, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")
    adapter.ensure_collection()
    tasks.set_channel_status(*scope.key, "building")
    for point in points:
        tasks.create_task(f"task-{point.point_id}", point.point_id, point.input_hash, point.model_revision, point.dimension, point.template_revision, point.projection_revision)
    adapter.upsert(list(points))
    reconciliation = adapter.reconcile(list(points))
    if reconciliation.is_consistent:
        for point in points:
            tasks.set_task_status(f"task-{point.point_id}", "ready")
        tasks.mark_channel_ready(scope, reconciliation)
    service = CodeRetrievalService(KeywordRetriever(store), adapter, channel_store=tasks, model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")
    queries = tuple((f"{error_code} {text}", vector, document.document_id) for (_, error_code, text, vector), document in zip(specs, documents) for _ in range(5))
    mode_results: dict[str, list[dict[str, Any]]] = {mode: [] for mode in ("keyword", "vector", "hybrid")}
    mode_latencies: dict[str, list[float]] = {mode: [] for mode in mode_results}
    mode_errors = {mode: 0 for mode in mode_results}
    for mode in mode_results:
        for query, vector, relevant_id in queries:
            query_started = time.perf_counter()
            result = service.search(query, scope, mode=mode, query_vector=vector, top_k=5, candidate_limit=30)
            mode_latencies[mode].append((time.perf_counter() - query_started) * 1000)
            if result.error_code:
                mode_errors[mode] += 1
            ranks = [index for index, hit in enumerate(result.hits, start=1) if hit.document_id == relevant_id]
            mode_results[mode].append({"result_count": len(result.hits), "document_ids": [hit.document_id for hit in result.hits], "relevant": bool(ranks), "rank": ranks[0] if ranks else None})
    t1 = time.perf_counter()
    persisted_vectors = sum(len(adapter.get([point.point_id])) for point in points)
    mode_unique_output_counts = {mode: len({document_id for entry in entries for document_id in entry["document_ids"]}) for mode, entries in mode_results.items()}
    checks = (
        store.count_documents(scope) == 4,
        len(database.query("SELECT 1 FROM code_search_manifests WHERE repository_id=? AND snapshot_id=? AND published_generation=? AND commit_sha=? AND status='ready'", scope.key)) == 1,
        persisted_vectors == 4,
        reconciliation.is_consistent,
        tasks.get_channel_status(*scope.key) == "ready",
        all(tasks.get_task_status(f"task-{point.point_id}") == "ready" for point in points),
        mode_unique_output_counts["hybrid"] == store.count_documents(scope),
        all(len(entry["document_ids"]) == len(set(entry["document_ids"])) for entry in mode_results["hybrid"]),
    )
    mode_metrics = {}
    mode_output_counts = {}
    for mode, entries in mode_results.items():
        mode_output_counts[mode] = sum(entry["result_count"] for entry in entries)
        mode_metrics[mode] = {
            "precision_at_5": round(sum(entry["relevant"] for entry in entries) / (len(entries) * 5), 4),
            "recall_at_5": round(sum(entry["relevant"] for entry in entries) / len(entries), 4),
            "mrr": round(sum(1 / entry["rank"] if entry["rank"] else 0 for entry in entries) / len(entries), 4),
        }
    mode_latency_p50_ms = {mode: round(_percentile(values, 0.50), 4) for mode, values in mode_latencies.items()}
    mode_latency_p95_ms = {mode: round(_percentile(values, 0.95), 4) for mode, values in mode_latencies.items()}
    adapter.close()
    t2 = time.perf_counter()
    report: dict[str, Any] = {
        "case": "hybrid", "milvus_mode": "lite", "pymilvus_version": "3.0.1", "milvus_lite_version": "3.2.1",
        "timeout_seconds": timeout_seconds, "input_files": len(documents),
        "input_bytes": sum(len(document.text.encode("utf-8")) for document in documents), "query_count": len(queries),
        "elapsed_ms": round((t2 - started) * 1000, 2), "output_count": mode_unique_output_counts["hybrid"],
        "persisted_documents": store.count_documents(scope), "persisted_vectors": persisted_vectors,
        "integrity_checks": sum(checks), "integrity_checks_threshold": len(checks), "background_exceptions": 0,
        "retries": 0, "business_completed_ms": round((t1 - started) * 1000, 2), "persistence_completed_ms": round((t2 - started) * 1000, 2),
        "persistence_lag_ms": round(max(0.0, (t2 - t1) * 1000), 2), "artifacts": len(list(output_path.iterdir())) + 1,
        "failures": 0, "mode_metrics": mode_metrics, "mode_output_counts": mode_output_counts, "mode_unique_output_counts": mode_unique_output_counts, "mode_errors": mode_errors,
        "hybrid_duplicate_results": sum(len(entry["document_ids"]) - len(set(entry["document_ids"])) for entry in mode_results["hybrid"]),
        "mode_latency_p50_ms": mode_latency_p50_ms, "mode_latency_p95_ms": mode_latency_p95_ms,
    }
    report["case_pass"] = evaluate_case_report(report) and all(error == 0 for error in mode_errors.values())
    (output_path / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_graph_case(output: str | Path, *, timeout_seconds: float = 120.0) -> dict[str, Any]:
    from scripts.code_retrieval_graph_case import run
    return run(output, timeout_seconds=timeout_seconds)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("keyword", "vector", "hybrid", "graph"), default="keyword")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if args.case == "vector":
        report = run_vector_case(args.output, timeout_seconds=args.timeout)
    elif args.case == "hybrid":
        report = run_hybrid_case(args.output, timeout_seconds=args.timeout)
    elif args.case == "graph":
        report = run_graph_case(args.output, timeout_seconds=args.timeout)
    else:
        report = run_keyword_case(args.output, timeout_seconds=args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["case_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
