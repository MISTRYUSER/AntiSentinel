"""Local application lifespan Case: real SQLite/Milvus, deterministic embedding."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import sys
import os
import logging
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from fastapi.testclient import TestClient
from antisentinel.api.app import create_app
from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration
from antisentinel.code_map.python_parser import PythonAstParser
from antisentinel.code_map.source_context import SourceEvidenceService
from antisentinel.code_map.store import SQLiteCodeMapStore, SourceBlob, SourceFile, StagedMapRows
from antisentinel.domain.incident import Incident
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteApplicationStore, SQLiteEvidenceStore
from antisentinel.entry.application import DiagnosisApplicationService
from antisentinel.retrieval.coordinator import RetrievalCoordinator
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from antisentinel.tools.registry import ToolRegistry


class Encoder:
    model_name, dimension, max_retries = 'local-lifecycle-fixture', 3, 0
    def __init__(self):
        self.calls = self.queries = 0
        self.closed = False
    def embed_documents(self, texts):
        assert not self.closed
        self.calls += len(texts)
        return [[1., 0., 0.] for _ in texts]
    def embed_query(self, texts):
        assert not self.closed
        self.queries += len(texts)
        return [[1., 0., 0.] for _ in texts]
    def close(self):
        self.closed = True


class Model:
    def __init__(self):
        self.phase, self.slices = 0, []

    @staticmethod
    def _hit_identity(hit):
        if isinstance(hit.get('source_identity'), dict):
            return dict(hit['source_identity'])
        return {
            key: hit[key] for key in (
                'repository_id', 'snapshot_id', 'published_generation', 'commit_sha',
                'node_id', 'chunk_id', 'path', 'source_hash', 'byte_start', 'byte_end',
                'parent_source_hash',
            ) if key in hit
        }

    @staticmethod
    def _parse_hits(request):
        blobs = []
        for message in request.messages:
            if message.get('role') == 'tool':
                for item in message.get('task_results') or []:
                    raw = item.get('summary') or item.get('result_summary') or ''
                    if isinstance(raw, str):
                        blobs.append(raw)
            working = message.get('working_set')
            if isinstance(working, dict):
                for turn in working.get('recent_turns') or []:
                    for event in turn.get('tool_events') or []:
                        raw = event.get('result_summary') or ''
                        if isinstance(raw, str):
                            blobs.append(raw)
        for raw in blobs:
            if 'hits' not in raw:
                continue
            start = raw.find('{')
            if start < 0:
                continue
            try:
                payload = json.loads(raw[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get('hits'):
                return payload['hits']
        raise ValueError('search hits missing from working set / tool summaries')

    def complete(self, request):
        if self.phase == 0:
            name, arguments = 'code_retrieval.search', {'query': 'Service.run', 'mode': 'hybrid'}
        elif self.phase == 1:
            hits = self._parse_hits(request)
            name, arguments = 'code_retrieval.read_evidence', {'candidates': [self._hit_identity(hits[0])]}
        else:
            self.slices = next(m['slices'] for m in request.messages if m['role'] == 'source_context')
            return {'final': {'summary': 'verified', 'diagnosis': 'fixture source inspected', 'confidence': 1.,
                'evidence_refs': [{'evidence_id': item['evidence_id']} for item in self.slices]}}
        self.phase += 1
        return {'tasks': [{'task_id': str(self.phase), 'objective': 'inspect', 'tool_calls': [{'tool_name': name, 'arguments': arguments}]}]}


def wait_for(predicate, deadline):
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise TimeoutError('lifecycle Case deadline exceeded')


def run(output, timeout=120, *, shutdown_probe=False, milvus_uri=None, token=None, between_starts=None, collection_base='lifecycle'):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    deadline = start + timeout
    db = SQLiteDatabase(output / 'facts.sqlite')
    db.initialize()
    now = datetime.now(timezone.utc)
    store = SQLiteCodeMapStore(db, clock=lambda: now)
    store.register(RepositoryRegistration('repo', 'file:///fixture', 'local', 'main'))
    job = store.enqueue('repo', 'a' * 40, 'manual')
    lease = store.claim_job('fixture', now)
    data = b'class Service:\n    def run(self):\n        return 42\n'
    parser = PythonAstParser('python-ast-v1')
    parsed = parser.parse_file('app.py', data, 'snapshot')
    assert store.publish(lease, MapSnapshot('snapshot', 'repo', 'a' * 40, 'python-ast-v1', job.rules_digest, file_count=1),
        StagedMapRows(nodes=parsed.symbols, edges=parser.contains_edges((parsed,), 'snapshot'), chunks=parsed.chunks,
            blobs=(SourceBlob(parsed.file_hash, 'object', data, parsed.encoding),),
            files=(SourceFile('file', 'snapshot', 'app.py', 'object', parsed.file_hash, len(data)),))).ok
    incident = Incident.create(title='lifecycle', source='fixture')
    store.bind_incident(str(incident.incident_id), 'repo', 'snapshot')
    checks, states, calls = {}, [], []
    def make_coordinator(store, source, encoder):
        return RetrievalCoordinator(store, source, allowed_repositories=['repo'],
            embedder_factory=lambda: encoder,
            index_factory=lambda e: MilvusAdapter(milvus_uri or output / 'vectors.db', collection_base, dimension=e.dimension,
                model_revision=e.model_name, template_revision='path-symbol-source-v1',
                projection_revision='lifecycle-v1', rpc_timeout=5, token=token), poll_seconds=.02)
    probe_calls = 0
    if shutdown_probe:
        entered, release = Event(), Event()
        class BlockingEncoder(Encoder):
            def embed_documents(self, texts):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError('probe release deadline')
                return super().embed_documents(texts)
        encoder = BlockingEncoder()
        source = SourceEvidenceService(None, store, SQLiteEvidenceStore(db))
        probe = make_coordinator(store, source, encoder)
        probe.start()
        try:
            assert entered.wait(10), 'probe did not enter encoding'
            try:
                probe.stop(timeout=.01)
                checks['shutdown_timeout_reported'] = False
            except TimeoutError:
                checks['shutdown_timeout_reported'] = True
            checks['inflight_client_open'] = not encoder.closed and probe.health()['state'] == 'stopping'
        finally:
            release.set()
            probe.stop(timeout=10)
        probe_calls = encoder.calls
        checks['shutdown_stopped_after_inflight'] = encoder.closed and probe.health()['state'] == 'stopped'
        checks['shutdown_no_next_task'] = probe_calls == 1 and db.query("SELECT COUNT(*) FROM code_embedding_tasks WHERE status='ready'")[0][0] == 1
        checks['shutdown_errors_zero'] = probe.health()['projection_errors'] == 0
    for iteration in range(2):
        if iteration == 1 and between_starts is not None:
            between_starts()
        db = SQLiteDatabase(output / 'facts.sqlite')
        store = SQLiteCodeMapStore(db)
        source = SourceEvidenceService(None, store, SQLiteEvidenceStore(db))
        encoder = Encoder()
        coordinator = make_coordinator(store, source, encoder)
        app = DiagnosisApplicationService.default_fake()
        app.code_map_store, app.code_map_evidence_store = store, source.evidence_store
        app.application_store = SQLiteApplicationStore(db)
        app.retrieval_coordinator = app.retrieval_tools = coordinator
        model = Model()
        app._runtime_builder = lambda: (model, ToolRegistry(auto_discover=False))
        app.incidents[str(incident.incident_id)] = incident
        with TestClient(create_app(app)) as client:
            wait_for(lambda: client.get('/api/retrieval/status').json().get('runs') == {'ready': 1}, deadline)
            if iteration == 0:
                session = app.start_session(incident_id=str(incident.incident_id), participant_ids=['case'], model_mode='fake')['session_id']
                wait_for(lambda: session in app.results, deadline)
                business = time.monotonic()
                wait_for(lambda: app.application_store.load_result(session) is not None, deadline)
                persisted = time.monotonic()
                result = app.results[session]
                checks['session_completed'] = result.status == 'completed'
                checks['tool_errors_zero'] = all(a.status.value == 'succeeded' for a in result.attempts)
                checks['citation_identity'] = {str(r.evidence_id) for r in result.final.evidence_refs} == {s['evidence_id'] for s in model.slices} and len(model.slices) == 1
                references = [{k: v for k, v in item.items() if k != 'content'} for item in model.slices]
                (output / 'source-refs.json').write_text(json.dumps(references, indent=2))
            else:
                restored = source.rehydrate(json.loads((output / 'source-refs.json').read_text()))
                checks['evidence_rehydrated'] = len(restored) == 1 and all(hashlib.sha256(s.content.encode()).hexdigest() == s.content_hash for s in restored)
                response = coordinator.for_incident(str(incident.incident_id))[0].handler({'query': 'Service', 'mode': 'hybrid'})
                checks['reopened_search'] = len(response.result['hits']) == len(parsed.chunks) and not response.result['degraded']
            states.append(client.get('/api/retrieval/status').json())
        calls.append(encoder.calls)
        checks[f'closed_{iteration}'] = encoder.closed and not coordinator.health()['thread_alive'] and coordinator.health()['state'] == 'stopped'
    checks['no_reembedding'] = calls == [len(parsed.chunks) - probe_calls, 0]
    checks['tasks_unique'] = db.query('SELECT COUNT(*) FROM code_embedding_tasks')[0][0] == len(parsed.chunks)
    checks['evidence_count'] = db.query('SELECT COUNT(*) FROM evidence')[0][0] == 1
    checks['sqlite_integrity'] = db.query('PRAGMA integrity_check')[0][0] == 'ok'
    checks['background_errors_zero'] = all(s['projection_errors'] == 0 for s in states)
    report = dict(case_pass=all(checks.values()), checks=checks, input_files=1, input_bytes=len(data),
        input_chunks=len(parsed.chunks), output_count=len(restored), persisted_evidence=1,
        elapsed_ms=(time.monotonic()-start)*1000, business_completed_ms=(business-start)*1000,
        persistence_completed_ms=(persisted-start)*1000, persistence_lag_ms=(persisted-business)*1000,
        embedding_documents_per_start=calls, external_model_calls=0, background_exceptions=sum(s['projection_errors'] for s in states),
        health=states, retries=sum(max(0, row[0]-1) for row in db.query('SELECT attempt FROM code_embedding_tasks')),
        shutdown_probe=shutdown_probe, shutdown_probe_encoded=probe_calls,
        storage_kind='standalone' if milvus_uri else 'lite', server_restart_requested=between_starts is not None)
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--shutdown-probe', action='store_true')
    parser.add_argument('--milvus-uri', help='Explicit server URI for a dedicated acceptance instance')
    args = parser.parse_args()
    if args.milvus_uri:
        logging.disable(logging.CRITICAL)  # SDK error logging must not serialize credentials.
    report = run(args.output, shutdown_probe=args.shutdown_probe, milvus_uri=args.milvus_uri,
        token=os.getenv('ANTISENTINEL_CODE_RETRIEVAL_MILVUS_TOKEN'))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['case_pass'] else 1)
