"""Frozen local-corpus diagnostics; preflight is read-only, execution is explicit."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from antisentinel.evaluation.code_corpus import load_corpus, digest
from antisentinel.evaluation.code_retrieval import evaluate
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.keyword import KeywordRetriever
from antisentinel.retrieval.models import CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore


class DiagnosticHashFeatures:
    """Untrained feature hashing, only for storage/fusion diagnostics, never an LLM."""
    revision = 'diagnostic-token-sha256-64-v1'
    dimension = 64

    def encode(self, text):
        terms = re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text.casefold())
        vector = [0.0] * self.dimension
        for token in terms:
            value = hashlib.sha256(token.encode()).digest()
            vector[int.from_bytes(value[:4], 'big') % self.dimension] += 1.0 if value[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if not norm:
            vector[0], norm = 1.0, 1.0
        return tuple(v / norm for v in vector)


def run_benchmark(repository, manifest_path, output, *, diagnostic=False, diagnostic_vectors=False, qwen_flash=False, hybrid_graph=False, graph_selection='interleave', baseline_path=None, timeout_seconds=120):
    if diagnostic_vectors and qwen_flash:
        raise ValueError('select only one embedding encoder')
    if hybrid_graph and not (diagnostic_vectors or qwen_flash):
        raise ValueError('hybrid_graph requires an embedding encoder')
    if graph_selection not in {'interleave', 'replace_seed', 'replace_container'} or (not hybrid_graph and graph_selection != 'interleave'):
        raise ValueError('graph selection requires an enabled supported hybrid_graph policy')
    started = time.perf_counter()
    corpus = load_corpus(repository, manifest_path)
    inspection = corpus.inspect()
    if inspection['errors'] or not inspection['sample_sufficient']:
        raise ValueError('invalid or insufficient query set')
    if not diagnostic:
        raise ValueError('acceptance prerequisites incomplete; explicitly select --diagnostic to measure without claiming acceptance')
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    baseline = json.loads(Path(baseline_path).read_text()) if baseline_path else None
    output.mkdir(parents=True)
    (output / 'manifest.json').write_bytes(Path(manifest_path).read_bytes())
    (output / 'preflight.json').write_text(json.dumps(inspection, ensure_ascii=False, indent=2)+'\n')
    database = SQLiteDatabase(output / 'facts.sqlite')
    store = SQLiteCodeSearchStore(database)
    # Publish both scopes, including documents without a directly labeled query.
    scopes = {(d.repository_id, d.snapshot_id, d.published_generation, d.commit_sha):
              CodeSearchScope(d.repository_id, d.snapshot_id, d.published_generation, d.commit_sha) for d in corpus.documents}
    for scope in scopes.values():
        store.begin_manifest(scope, 'evaluation-projection-v1', [d for d in corpus.documents if (d.repository_id,d.snapshot_id,d.published_generation,d.commit_sha)==scope.key])
    store.upsert_documents(corpus.documents)
    for scope in scopes.values():
        store.publish_manifest(scope, 'evaluation-projection-v1')
    vector = tasks = None
    encoder = DiagnosticHashFeatures()
    modes = ['keyword']
    versions = {'python': platform.python_version(), 'platform': platform.platform()}
    points = []
    try:
        if qwen_flash:
            from antisentinel.evaluation.code_embedding import FlashCodeEncoder
            encoder = FlashCodeEncoder.from_env()
        if diagnostic_vectors or qwen_flash:
            from antisentinel.retrieval.milvus_adapter import MilvusAdapter
            from antisentinel.retrieval.vector import VectorPoint, EmbeddingTaskStore
            versions.update({name: importlib.metadata.version(name) for name in ('pymilvus', 'milvus-lite')})
            vector = MilvusAdapter(output / 'vectors.db', 'evaluation_vectors', dimension=encoder.dimension,
                    model_revision=encoder.revision, template_revision='path-symbol-source-v1', projection_revision='evaluation-projection-v1')
            tasks = EmbeddingTaskStore(database)
            texts = [f'{d.path}\n{d.symbol}\n{d.text}' for d in corpus.documents]
            encoded = encoder.encode_documents(texts) if qwen_flash else [encoder.encode(t) for t in texts]
            for d, values in zip(corpus.documents, encoded, strict=True):
                point = VectorPoint('derived', values, d.source_identity,
                        d.embedding_input_hash, encoder.revision, encoder.dimension, 'path-symbol-source-v1', d.projection_revision,
                        document_id=d.document_id)
                points.append(point)
                tasks.create_task(point.point_id, point.point_id, point.input_hash, point.model_revision, point.dimension,
                                  point.template_revision, point.projection_revision)
            vector.upsert(points)
            for scope in scopes.values():
                grouped = [p for p in points if tuple(p.source_identity[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha')) == scope.key]
                reconciliation = vector.reconcile(grouped)
                if not reconciliation.is_consistent:
                    raise RuntimeError('vector_projection_reconciliation_failed')
                for p in grouped:
                    tasks.set_task_status(p.point_id, 'ready')
                tasks.mark_channel_ready(scope, reconciliation)
            modes += ['vector', 'hybrid']
        graph = None
        if hybrid_graph:
            from antisentinel.evaluation.code_corpus import publish_graph_corpus
            from antisentinel.retrieval.graph import GraphExpander
            graph = GraphExpander(publish_graph_corpus(corpus, database))
            modes.append('hybrid_graph')
        service = CodeRetrievalService(KeywordRetriever(store), vector, channel_store=tasks,
                    model_revision=encoder.revision, template_revision='path-symbol-source-v1', projection_revision='evaluation-projection-v1', graph_expander=graph, graph_selection=graph_selection)

        def search(mode, query):
            query_vector = encoder.encode(query['query']) if mode != 'keyword' else None
            return service.search(query['query'], CodeSearchScope(**query['scope']), mode=mode,
                                  query_vector=query_vector, top_k=5, candidate_limit=30)

        execution_key = digest({'fingerprints': corpus.metadata, 'modes': modes, 'rounds': 5,
                'versions': versions, 'encoder': encoder.revision if vector else None,
                'dimension': encoder.dimension if vector else None, 'query_cache': False,
                'graph_selection': graph_selection if hybrid_graph else None,
                'projection': 'evaluation-projection-v1', 'candidate_limit': 30, 'rrf_k': 60,
                'fts_columns': ['document_id', 'path', 'symbol', 'identifiers', 'comments', 'code'],
                'bm25_weights': [1, 1, 4, 2, 1, 1]})
        report = evaluate(corpus, search, modes=modes, baseline=baseline, execution_key=execution_key,
                          timeout_seconds=max(.001, timeout_seconds - (time.perf_counter() - started)))
        report['graph_selection'] = graph_selection if hybrid_graph else None
        business_done = time.perf_counter()
        if vector:
            vector.close()
            vector = MilvusAdapter(output / 'vectors.db', 'evaluation_vectors', dimension=encoder.dimension,
                    model_revision=encoder.revision, template_revision='path-symbol-source-v1', projection_revision='evaluation-projection-v1')
            actual = vector.get([p.point_id for p in points])
            expected = {p.point_id: p for p in points}
            vectors_verified = len(actual) == len(expected) and {r['point_id'] for r in actual} == set(expected)
            for row in actual:
                point = expected[row['point_id']]
                fields = {**point.source_identity, 'document_id': point.document_id, 'input_hash': point.input_hash,
                          'model_revision': point.model_revision, 'dimension': point.dimension,
                          'template_revision': point.template_revision, 'projection_revision': point.projection_revision}
                vectors_verified = vectors_verified and all(row.get(k) == v for k, v in fields.items())
        else:
            actual, vectors_verified = [], None
        reopened = SQLiteDatabase(output / 'facts.sqlite')
        actual_docs = reopened.query('SELECT document_id,source_hash FROM code_search_documents')
        expected_docs = {d.document_id: d.source_hash for d in corpus.documents}
        documents_verified = {r['document_id']: r['source_hash'] for r in actual_docs} == expected_docs
        persisted_done = time.perf_counter()
        report.update({'diagnostic_only': True, 'embedding': {'kind': 'untrained_feature_hashing' if diagnostic_vectors else 'not_used',
                'revision': encoder.revision if diagnostic_vectors else None, 'external_calls': 0,
                'tokens': None, 'cost': None, 'reason': 'no external Embedding model or billed API; semantic quality not established'},
            'input_files': corpus.metadata['source_count'], 'input_bytes': corpus.input_bytes,
            'input_documents': len(corpus.documents), 'persisted_documents': len(actual_docs),
            'persisted_vectors': len(actual), 'documents_verified': documents_verified, 'vectors_verified': vectors_verified,
            'memory_records': reopened.query('SELECT COUNT(*) FROM memory_records')[0][0],
            'business_completed_ms': (business_done-started)*1000,
            'persistence_completed_ms': (persisted_done-started)*1000,
            'persistence_lag_ms': (persisted_done-business_done)*1000,
            'worker_count': 0, 'background_exceptions': None, 'retries': 0, 'versions': versions,
            'implementation_sha256': digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                     [ROOT/'src/antisentinel/retrieval/engine.py', ROOT/'src/antisentinel/retrieval/fusion.py', Path(__file__)]})})
        if qwen_flash:
            report['embedding'] = encoder.metadata()
            report['retries'] = None  # HTTP attempts counted; retry attribution is not exposed by the adapter.
        report['execution_pass'] = report['execution_pass'] and documents_verified and vectors_verified is not False
        report['elapsed_ms'] = (time.perf_counter()-started)*1000
        if report['elapsed_ms'] > timeout_seconds*1000:
            report['execution_pass'] = False
            report['acceptance_blockers'].append('deadline_exceeded')
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        return report
    except Exception as exc:
        failure = {'error_type': type(exc).__name__, 'case_pass': False}
        if qwen_flash and hasattr(encoder, 'metadata'):
            failure['embedding'] = encoder.metadata()
        (output/'failure.json').write_text(json.dumps(failure)+'\n')
        raise
    finally:
        if qwen_flash and hasattr(encoder, 'close'):
            encoder.close()
        if vector is not None:
            vector.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--manifest', type=Path, default=ROOT/'tests/fixtures/code_retrieval/v1/manifest.json')
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--hybrid-graph', action='store_true', help='Measure hybrid-seeded contains expansion against the same frozen corpus')
    parser.add_argument('--graph-selection', choices=('interleave', 'replace_seed', 'replace_container'), default='interleave')
    encoders = parser.add_mutually_exclusive_group()
    encoders.add_argument('--diagnostic-vectors', action='store_true')
    encoders.add_argument('--qwen-flash', action='store_true', help='Send corpus and queries to the configured Flash API; requires authorized data scope')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if args.preflight:
        report = load_corpus(args.repository, args.manifest).inspect()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report['errors'] else 1
    if args.output is None:
        parser.error('--output is required for execution')
    report = run_benchmark(args.repository, args.manifest, args.output, diagnostic=args.diagnostic,
                diagnostic_vectors=args.diagnostic_vectors, qwen_flash=args.qwen_flash, hybrid_graph=args.hybrid_graph, graph_selection=args.graph_selection, baseline_path=args.baseline, timeout_seconds=args.timeout)
    print(json.dumps({k: report[k] for k in ('execution_pass','quality_pass','case_pass','acceptance_blockers')}, indent=2))
    return 0 if report['execution_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
