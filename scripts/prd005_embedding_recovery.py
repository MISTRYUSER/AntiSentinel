"""R2 runner support: one frozen document, one process exit, durable recovery."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from antisentinel.evaluation.code_corpus import load_corpus
from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.embedding_jobs import LeaseLost
from antisentinel.retrieval.embedding_worker import EmbeddingWorker
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.retrieval.models import CodeSearchScope
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.vector import EmbeddingTaskStore
from scripts.prd005_real_case_support import HttpUsageAudit, assert_report_safe


def _append(path, value):
    assert_report_safe(value)
    with path.open('a') as stream:
        stream.write(json.dumps(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


class RecoveryEncoder:
    def __init__(self, output, config, phase):
        self.output, self.config, self.phase = output, config, phase
        self.model_name, self.dimension = config['model_revision'], config['dimension']
        self.max_retries = 0
        self.audit = HttpUsageAudit('embedding')
        self.delegate = None
        if not config['local_embedding']:
            self.delegate = QwenFlashEmbedder.from_env()
            self.delegate.max_retries = 0
            if (self.delegate.model_name, self.delegate.dimension) != (self.model_name, self.dimension):
                self.delegate.close()
                raise ValueError('embedding version differs from frozen recovery config')
            self.delegate.client.event_hooks['request'].append(self._request)
            self.delegate.client.event_hooks['response'].append(self._response)

    def _request(self, request):
        self.audit.request(request)
        _append(self.output / 'http-audit.jsonl', {'phase': self.phase, **self.audit.snapshot()})

    def _response(self, response):
        self.audit.response(response)
        _append(self.output / 'http-audit.jsonl', {'phase': self.phase, **self.audit.snapshot()})

    def embed_documents(self, texts):
        _append(self.output / 'embedding-inputs.jsonl', {'phase': self.phase, 'inputs': len(texts)})
        if self.delegate is None:
            return [[1., 0., 0.] for _ in texts]
        return self.delegate.embed_documents(texts)

    def close(self):
        if self.delegate is not None:
            self.delegate.close()


def _index(config):
    return MilvusAdapter(config['milvus_uri'], config['collection_base'],
                         dimension=config['dimension'], model_revision=config['model_revision'],
                         template_revision=config['template_revision'],
                         projection_revision=config['projection_revision'],
                         rpc_timeout=5,
                         token=os.getenv('ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN') or None)


def recovery_child(output):
    from scripts.run_embedding_worker_case import CrashAfterFirstUpsert
    output = Path(output)
    config = json.loads((output / 'config.json').read_text())
    queue = EmbeddingTaskStore(SQLiteDatabase(output / 'facts.sqlite')).queue
    index, encoder = _index(config), RecoveryEncoder(output, config, 'child')
    try:
        worker = EmbeddingWorker(queue, CrashAfterFirstUpsert(index, output / 'upsert-complete.json'),
                                 encoder, owner='crash-child', lease_seconds=config['lease_seconds'])
        worker.run_once(config['run_id'])
        raise RuntimeError('post-upsert exit was not reached')
    finally:
        index.close()
        encoder.close()


def run_real_recovery(repository, manifest, output, milvus_uri, query_id, timeout=120, *, local_embedding=False):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be positive and finite')
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    corpus = load_corpus(Path(repository), Path(manifest))
    query = next((q for q in corpus.queries if q['id'] == query_id), None)
    if query is None or not query['relevant_ids']:
        raise ValueError('recovery query must have a labeled document')
    doc = next(d for d in corpus.documents if d.document_id == sorted(query['relevant_ids'])[0])
    config = {
        'model_revision': 'prd005-r2-local' if local_embedding else QwenFlashEmbedder.model_name,
        'dimension': 3 if local_embedding else int(os.getenv('ANTISENTINEL_EMBEDDING_DIMENSION', '1024')),
        'template_revision': 'path-symbol-source-v1', 'projection_revision': doc.projection_revision,
        'collection_base': 'prd005_r2_' + hashlib.sha256(str(output).encode()).hexdigest()[:20],
        'milvus_uri': str(milvus_uri), 'local_embedding': local_embedding,
        'lease_seconds': 2 if local_embedding else 40,
    }
    # URLs must not smuggle credentials into the public config or subprocess args.
    from urllib.parse import urlsplit
    uri = urlsplit(str(milvus_uri))
    if uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError('Milvus credentials must use the token environment variable')
    if not local_embedding and (not os.getenv('DASHSCOPE_API_KEY') or not os.getenv('ANTISENTINEL_EMBEDDING_BASE_URL')):
        raise ValueError('embedding credentials and endpoint required')
    output.mkdir(parents=True)
    start = time.monotonic()
    deadline = start + timeout
    index = encoder = None
    try:
        db = SQLiteDatabase(output / 'facts.sqlite')
        lexical = SQLiteCodeSearchStore(db)
        scope = CodeSearchScope(doc.repository_id, doc.snapshot_id, doc.published_generation, doc.commit_sha)
        lexical.begin_manifest(scope, doc.projection_revision, [doc])
        lexical.upsert_documents([doc])
        lexical.publish_manifest(scope, doc.projection_revision)
        queue = EmbeddingTaskStore(db).queue
        rid = queue.enqueue(scope, model_revision=config['model_revision'], dimension=config['dimension'],
                            template_revision=config['template_revision'], projection_revision=doc.projection_revision)
        config['run_id'] = rid
        assert_report_safe(config)
        (output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
        (output / 'config.json').chmod(0o600)
        script = Path(__file__).with_name('run_embedding_worker_case.py')
        command = [sys.executable, str(script), '--real-recovery', '--child', '--output', str(output)]
        # Keep child diagnostic output private; never echo SDK exception text.
        with (output / 'child.log').open('w') as log:
            (output / 'child.log').chmod(0o600)
            child = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=max(.001, deadline - time.monotonic()))
        if child.returncode != 86:
            raise RuntimeError('child did not reach the post-upsert crash point')
        stale = queue.task_rows(rid)[0]
        marker = json.loads((output / 'upsert-complete.json').read_text())
        index = _index(config)
        rows = index.get([stale['point_id']])
        expected_fields = {**doc.source_identity, 'document_id': doc.document_id,
                           'point_id': stale['point_id'], 'input_hash': doc.embedding_input_hash,
                           **{k: config[k] for k in ('model_revision', 'dimension', 'template_revision', 'projection_revision')}}
        checks = {
            'crash_after_write': marker == {'point_ids': [stale['point_id']], 'upserted': 1},
            'task_unconfirmed': stale['status'] == 'running' and stale['attempt'] == 1,
            'vector_cached': stale['vector_json'] is not None,
            'pre_recovery_identity': len(rows) == 1 and all(rows[0].get(k) == v for k, v in expected_fields.items()),
        }
        if not all(checks.values()):
            raise RuntimeError('crash checkpoint integrity failed')
        while time.time() <= stale['lease_expires_at']:
            if time.monotonic() >= deadline:
                raise TimeoutError('recovery deadline before lease expiry')
            time.sleep(min(.1, max(.001, deadline - time.monotonic())))
        encoder = RecoveryEncoder(output, config, 'recovery')
        worker = EmbeddingWorker(queue, index, encoder, owner='recovery-worker', lease_seconds=config['lease_seconds'])
        if worker.run_once(rid)['status'] != 'ready':
            raise RuntimeError('recovery worker did not publish ready')
        business = time.monotonic()
        task = queue.task_rows(rid)[0]
        attempts = [dict(row) for row in db.query('SELECT * FROM code_embedding_attempts ORDER BY attempt')]
        inputs = [json.loads(line) for line in (output / 'embedding-inputs.jsonl').read_text().splitlines()]
        per_process = [sum(item['inputs'] for item in inputs if item['phase'] == phase) for phase in ('child', 'recovery')]
        stale_rejections = 0
        for operation in (lambda: queue.complete(stale, now=time.time()),
                          lambda: queue.renew(stale, now=time.time()),
                          lambda: queue.cache_vector(stale, [1.] * config['dimension'], now=time.time()),
                          lambda: queue.fail(stale, 'stale', now=time.time())):
            try:
                operation()
            except LeaseLost:
                stale_rejections += 1
        # Re-open SQLite, read Strong from Milvus, and validate the persisted publication report.
        reopened = SQLiteDatabase(output / 'facts.sqlite')
        final_rows = index.get([task['point_id']])
        publication = json.loads(queue.run(rid)['report_json'])
        checks.update({
            'attempt_history': [row['status'] for row in attempts] == ['lease_expired', 'ready'],
            'cached_embedding_reused': per_process == [1, 0],
            'task_ready': task['status'] == 'ready' and task['attempt'] == 2,
            'new_lease_token': attempts[0]['lease_token'] != attempts[1]['lease_token'],
            'stale_lease_rejected': stale_rejections == 4,
            'final_identity': len(final_rows) == 1 and all(final_rows[0].get(k) == v for k, v in expected_fields.items()),
            'publication_consistent': publication['expected_ids'] == publication['actual_ids'] == [task['point_id']] and not publication['field_mismatches'],
            'sqlite_integrity': reopened.query('PRAGMA integrity_check')[0][0] == 'ok',
        })
        persisted = time.monotonic()
        audit = {}
        if (output / 'http-audit.jsonl').exists():
            for line in (output / 'http-audit.jsonl').read_text().splitlines():
                item = json.loads(line)
                audit[item['phase']] = item
        external_calls = sum(item['requests'] for item in audit.values())
        checks['model_request_count'] = external_calls == (0 if local_embedding else 1)
        index.close()
        index = None
        encoder.close()
        encoder = None
        elapsed = (time.monotonic() - start) * 1000
        report = {
            'case': 'prd005-r2-post-upsert-recovery', 'case_pass': all(checks.values()) and elapsed <= timeout * 1000,
            'execution_pass': all(checks.values()) and elapsed <= timeout * 1000, 'quality_pass': False,
            'checks': checks, 'input_files': 1, 'input_bytes': len(doc.text.encode()), 'input_documents': 1,
            'input_manifest_sha256': corpus.metadata['manifest_sha256'], 'query_id': query_id,
            'counts': {'tasks': reopened.query('SELECT COUNT(*) FROM code_embedding_tasks')[0][0],
                       'attempts': len(attempts), 'vectors': len(final_rows), 'evidence': 0,
                       'identity_checks': len(expected_fields) * 2, 'stale_rejections': stale_rejections},
            'point_identity': expected_fields, 'embedding_kind': 'local_fixture' if local_embedding else 'remote',
            'embedding_inputs_per_process': per_process, 'embedding_http': audit,
            'external_model_calls': external_calls, 'injected_process_exits': 1, 'child_exit_code': child.returncode,
            'unexpected_background_exceptions': 0, 'retries': task['attempt'] - 1,
            'lease_seconds': config['lease_seconds'], 'elapsed_ms': elapsed,
            'business_completed_ms': (business - start) * 1000,
            'persistence_completed_ms': (persisted - start) * 1000,
            'persistence_lag_ms': (persisted - business) * 1000,
            'timing_semantics': 'ready returned then reopened storage verified; lag is observation gap',
        }
        assert_report_safe(report)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        return report
    except Exception as error:
        (output / 'failure.json').write_text(json.dumps({'case_pass': False, 'error_type': type(error).__name__}) + '\n')
        raise
    finally:
        if index is not None:
            index.close()
        if encoder is not None:
            encoder.close()
