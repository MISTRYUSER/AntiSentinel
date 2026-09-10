"""Preregistered, paired local A/B: hybrid versus container-only replacement."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
from statistics import mean
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))

from scripts.evaluate_code_retrieval import DiagnosticHashFeatures
from antisentinel.evaluation.code_corpus import load_corpus, publish_graph_corpus, digest
from antisentinel.evaluation.code_retrieval import score_query, summarize, percentile
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.graph import GraphExpander
from antisentinel.retrieval.keyword import KeywordRetriever
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.retrieval.models import CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.vector import EmbeddingTaskStore, VectorPoint


def paired_schedule(query_ids, rounds=5, seed=42):
    if len(set(query_ids)) != len(query_ids) or not query_ids or rounds < 1:
        raise ValueError('invalid paired schedule')
    rng = random.Random(seed)
    schedule = []
    for run in range(rounds):
        indices = list(range(len(query_ids)))
        rng.shuffle(indices)
        for index in indices:
            schedule.append({'run': run + 1, 'query_id': query_ids[index],
                'order': ['A', 'B'] if (index + run) % 2 == 0 else ['B', 'A']})
    return schedule


def paired_statistics(rows, bootstrap_seed=42, replicates=20000):
    pairs = {}
    for row in rows:
        key = row['query_id'], row['run']
        if row['arm'] in pairs.setdefault(key, {}):
            raise ValueError('duplicate paired observation')
        pairs[key][row['arm']] = row
    if not pairs or any(set(pair) != {'A', 'B'} for pair in pairs.values()):
        raise ValueError('missing paired observation')
    grouped = {}
    for (query_id, _), pair in pairs.items():
        grouped.setdefault(query_id, []).append(pair)
    counts = {len(values) for values in grouped.values()}
    if len(counts) != 1:
        raise ValueError('unequal query repetitions')
    metrics = {}
    for metric in ('precision_at_5', 'recall_at_5', 'mrr', 'false_positive'):
        deltas = []
        for query_id, values in sorted(grouped.items()):
            if any((pair['A'][metric] is None) != (pair['B'][metric] is None) for pair in values):
                raise ValueError('inconsistent paired denominators')
            if values[0]['A'][metric] is None:
                continue
            deltas.append(mean(float(p['B'][metric]) - float(p['A'][metric]) for p in values))
        if not deltas:
            metrics[metric] = None
            continue
        rng = random.Random(bootstrap_seed)
        boot = [mean(rng.choices(deltas, k=len(deltas))) for _ in range(replicates)]
        metrics[metric] = {'delta_B_minus_A': mean(deltas), 'independent_queries': len(deltas),
            'ci95': [percentile(boot, .025), percentile(boot, .975)],
            'positive_queries': sum(d > 0 for d in deltas), 'negative_queries': sum(d < 0 for d in deltas)}
    return metrics


def run(output, repository=ROOT, manifest=None):
    started = time.perf_counter()
    deadline = started + 120
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = Path(manifest or ROOT / 'tests/fixtures/code_retrieval/v1/manifest.json')
    corpus = load_corpus(Path(repository), manifest)
    inspection = corpus.inspect()
    if inspection['errors'] or not inspection['sample_sufficient']:
        raise ValueError('invalid frozen corpus')
    schedule = paired_schedule([q['id'] for q in corpus.queries])
    encoder = DiagnosticHashFeatures()
    vectors = {q['id']: encoder.encode(q['query']) for q in corpus.queries}
    implementation = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), ROOT/'src/antisentinel/retrieval/engine.py', ROOT/'src/antisentinel/retrieval/graph.py',
                  ROOT/'src/antisentinel/retrieval/fusion.py', ROOT/'src/antisentinel/retrieval/keyword.py',
                  ROOT/'src/antisentinel/retrieval/milvus_adapter.py', ROOT/'src/antisentinel/evaluation/code_corpus.py']}
    plan = {'A': 'hybrid', 'B': 'hybrid_graph/replace_container', 'fingerprints': corpus.metadata,
        'encoder': encoder.revision, 'query_vectors_sha256': digest(vectors), 'held_out': False,
        'rounds': 5, 'top_k': 5, 'candidate_limit': 30, 'rrf_k': 60, 'warmup_pairs': len(corpus.queries),
        'schedule': schedule, 'bootstrap_seed': 42, 'bootstrap_replicates': 20000,
        'gates': {'recall_ci_lower_gt': 0, 'precision_ci_lower_min': 0, 'mrr_ci_lower_min': 0,
                  'false_positive_ci_upper_max': 0, 'p95_ratio_max': 1.20, 'errors_max': 0},
        'latency_scope': 'retrieval only; identical precomputed query vector, excludes encoding',
        'implementation_sha256': implementation,
        'versions': {name: importlib.metadata.version(name) for name in ('pymilvus', 'milvus-lite')}}
    # Freeze both schedule and gates before preparing or querying either arm.
    (output/'plan.json').write_text(json.dumps(plan, indent=2))
    (output/'manifest.json').write_bytes(manifest.read_bytes())
    (output/'query-vectors.json').write_text(json.dumps(vectors))
    plan_hash = hashlib.sha256((output/'plan.json').read_bytes()).hexdigest()
    database = SQLiteDatabase(output/'facts.sqlite')
    store = SQLiteCodeSearchStore(database)
    graph_store = publish_graph_corpus(corpus, database)
    scopes = {CodeSearchScope(d.repository_id, d.snapshot_id, d.published_generation, d.commit_sha) for d in corpus.documents}
    for scope in scopes:
        docs = [d for d in corpus.documents if tuple(d.source_identity[k] for k in vars(scope)) == scope.key]
        store.begin_manifest(scope, 'evaluation-projection-v1', docs)
        store.upsert_documents(docs)
        store.publish_manifest(scope, 'evaluation-projection-v1')
    tasks = EmbeddingTaskStore(database)
    points = [VectorPoint('derived', encoder.encode(f'{d.path}\n{d.symbol}\n{d.text}'), d.source_identity,
        d.embedding_input_hash, encoder.revision, encoder.dimension, 'path-symbol-source-v1', d.projection_revision, d.document_id)
        for d in corpus.documents]
    index = MilvusAdapter(output/'vectors.db', 'paired_ab', dimension=encoder.dimension,
        model_revision=encoder.revision, template_revision='path-symbol-source-v1', projection_revision='evaluation-projection-v1', rpc_timeout=10)
    try:
        for point in points:
            tasks.create_task(point.point_id, point.point_id, point.input_hash, point.model_revision, point.dimension, point.template_revision, point.projection_revision)
        index.upsert(points)
        for scope in scopes:
            scoped = [p for p in points if tuple(p.source_identity[k] for k in vars(scope)) == scope.key]
            for point in scoped:
                tasks.set_task_status(point.point_id, 'ready')
            tasks.mark_channel_ready(scope, index.reconcile(scoped))
        service = CodeRetrievalService(KeywordRetriever(store), index, channel_store=tasks,
            model_revision=encoder.revision, template_revision='path-symbol-source-v1', projection_revision='evaluation-projection-v1',
            graph_expander=GraphExpander(graph_store), graph_selection='replace_container')
        documents = {d.document_id: d for d in corpus.documents}
        queries = {q['id']: q for q in corpus.queries}
        def measure(arm, query_id, run):
            query = queries[query_id]
            begin = time.perf_counter()
            ids, identities, contributions, error = [], [], [], None
            try:
                if begin >= deadline:
                    raise TimeoutError('A/B deadline')
                result = service.search(query['query'], CodeSearchScope(**query['scope']),
                    mode='hybrid' if arm == 'A' else 'hybrid_graph', query_vector=vectors[query_id])
                ids = [hit.document_id for hit in result.hits]
                identities = [hit.source_identity for hit in result.hits]
                contributions = [list(hit.channels) for hit in result.hits]
                if result.degraded or result.error_code or len(ids) > 5 or len(set(ids)) != len(ids):
                    raise ValueError('invalid retrieval result')
                for hit in result.hits:
                    doc = documents.get(hit.document_id)
                    if doc is None or any(hit.source_identity.get(k) != v for k, v in doc.source_identity.items()) or any(hit.source_identity.get(k) != v for k, v in query['scope'].items()):
                        raise ValueError('source identity mismatch')
            except Exception as exc:
                error = type(exc).__name__
            return {'arm': arm, 'query_id': query_id, 'run': run, 'document_ids': ids,
                'source_identities': identities, 'contributions': contributions, 'error': error,
                'latency_ms': (time.perf_counter()-begin)*1000,
                **score_query(ids if error is None else [], query['relevant_ids'])}
        warmup = [measure(arm, q['id'], 0) for i, q in enumerate(corpus.queries)
                  for arm in (('A','B') if i % 2 == 0 else ('B','A'))]
        (output/'warmup.json').write_text(json.dumps(warmup))
        rows = []
        for pair in schedule:
            for arm in pair['order']:
                row = measure(arm, pair['query_id'], pair['run'])
                rows.append(row)
                with (output/'observations.jsonl').open('a') as stream:
                    stream.write(json.dumps(row)+'\n')
        business_done = time.perf_counter()
        reconciled = all(index.reconcile([p for p in points
            if tuple(p.source_identity[k] for k in vars(scope)) == scope.key]).is_consistent for scope in scopes)
        integrity = database.query('PRAGMA integrity_check')[0][0] == 'ok'
        persisted_docs = database.query('SELECT COUNT(*) FROM code_search_documents')[0][0]
        persisted_done = time.perf_counter()
    finally:
        index.close()
    statistics = paired_statistics(rows)
    arms = {arm: {'metrics': summarize([r for r in rows if r['arm']==arm]),
        'p95_ms': percentile([r['latency_ms'] for r in rows if r['arm']==arm], .95)} for arm in ('A','B')}
    errors = sum(r['error'] is not None for r in rows + warmup)
    gates = {'recall_superiority': statistics['recall_at_5']['ci95'][0] > 0,
        'precision_noninferiority': statistics['precision_at_5']['ci95'][0] >= 0,
        'mrr_noninferiority': statistics['mrr']['ci95'][0] >= 0,
        'no_answer_noninferiority': statistics['false_positive']['ci95'][1] <= 0,
        'p95_guardrail': arms['B']['p95_ms'] <= arms['A']['p95_ms']*1.20}
    checks = {'no_errors': errors==0, 'paired_count': len(rows)==len(corpus.queries)*5*2,
        'index_reconciled': reconciled, 'sqlite_integrity': integrity,
        'documents_complete': persisted_docs==len(corpus.documents),
        'plan_unchanged': plan_hash==hashlib.sha256((output/'plan.json').read_bytes()).hexdigest()}
    report = {'execution_pass': all(checks.values()), 'checks': checks, 'gates': gates,
        'statistical_gate_pass': all(gates.values()) and all(checks.values()), 'promote_default': False,
        'held_out': False, 'quality_pass': False, 'plan_sha256': plan_hash, 'arms': arms, 'paired_statistics': statistics,
        'input_queries':len(corpus.queries), 'input_documents':len(corpus.documents), 'input_bytes':corpus.input_bytes,
        'measured_calls':len(rows), 'warmup_calls':len(warmup), 'errors':errors,
        'persisted_documents':persisted_docs, 'persisted_vectors':len(points) if reconciled else None,
        'external_model_calls':0, 'background_exceptions':None, 'elapsed_ms':(time.perf_counter()-started)*1000,
        'business_completed_ms':(business_done-started)*1000, 'persistence_completed_ms':(persisted_done-started)*1000,
        'persistence_lag_ms':(persisted_done-business_done)*1000}
    (output/'warmup.json').write_text(json.dumps(warmup))
    (output/'report.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--manifest', type=Path, help='Frozen corpus/query manifest; provenance must be reviewed separately')
    args = parser.parse_args()
    result = run(args.output, repository=args.repository, manifest=args.manifest)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['execution_pass'] else 1)
