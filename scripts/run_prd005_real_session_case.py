"""PRD-005 R1: normal application retrieval, Session, Evidence, and reopen Case."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import threading
from typing import Callable
from collections import Counter
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from fastapi.testclient import TestClient

from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
from antisentinel.api.app import create_app
from antisentinel.code_map.source_context import SourceEvidenceService
from antisentinel.entry.application import DiagnosisApplicationService
from antisentinel.evaluation.code_corpus import publish_graph_corpus
from antisentinel.persistence.local_vector_memory import QwenFlashEmbedder
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.retrieval.slicing import SLICE_REVISION
from scripts.prd005_real_case_support import (
    HttpUsageAudit,
    assert_report_safe,
    case_environment,
    freeze_case_input,
)


@dataclass
class CaseFactories:
    embedder_factory: Callable[[], object]
    model_factory: Callable[[], object]
    configure_queries: Callable[[str, str], None]
    document_inputs_per_start: Callable[[], list[int]]
    embedding_audit: HttpUsageAudit
    model_audit: HttpUsageAudit
    close: Callable[[], None]
    poll_seconds: float = 5.0
    remote: bool = False
    disclosures: dict = field(default_factory=dict)
    model_calls: dict = field(default_factory=dict)
    unanswered_embedding_failures: Callable[[], int] = lambda: 0


class LocalEncoder:
    model_name = 'prd005-r1-local'
    dimension = 3
    max_retries = 0

    def __init__(self):
        self.document_inputs = 0
        self.query_inputs = 0
        self.closed = False

    def embed_documents(self, texts):
        if self.closed:
            raise RuntimeError('encoder closed')
        self.document_inputs += len(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, texts):
        if self.closed:
            raise RuntimeError('encoder closed')
        self.query_inputs += len(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]

    def close(self):
        self.closed = True


class AnswerableModel:
    def __init__(self, query):
        self.query = query
        self.phase = 0
        self.slices = []

    def complete(self, request):
        if self.phase == 0:
            self.phase += 1
            return {'tasks': [{'task_id': 'search', 'objective': '检索固定版本源码', 'tool_calls': [{
                'tool_name': 'code_retrieval.search', 'arguments': {'query': self.query, 'mode': 'hybrid'},
            }]}]}
        if self.phase == 1:
            self.phase += 1
            results = next(message['task_results'] for message in request.messages if message['role'] == 'tool')
            hits = json.loads(results[0]['summary'])['hits']
            if not hits:
                raise RuntimeError('answerable fixture returned no retrieval hit')
            return {'tasks': [{'task_id': 'evidence', 'objective': '回读检索来源', 'tool_calls': [{
                'tool_name': 'code_retrieval.read_evidence', 'arguments': {'candidates': [hits[0]['source_identity']]},
            }]}]}
        self.slices = next(message['slices'] for message in request.messages if message['role'] == 'source_context')
        return {'final': {
            'summary': '已依据固定版本源码完成核对。',
            'diagnosis': 'fixture source inspected',
            'confidence': 1.0,
            'evidence_refs': [{'evidence_id': item['evidence_id']} for item in self.slices],
        }}


class NoAnswerModel:
    def __init__(self, query):
        self.query = query
        self.phase = 0

    def complete(self, _request):
        if self.phase == 0:
            self.phase += 1
            return {'tasks': [{'task_id': 'search', 'objective': '检索固定版本源码', 'tool_calls': [{
                'tool_name': 'code_retrieval.search', 'arguments': {'query': self.query, 'mode': 'hybrid'},
            }]}]}
        return {'final': {
            'summary': '当前固定范围内证据不足，不能确认。',
            'diagnosis': 'insufficient_evidence',
            'confidence': 0.0,
            'evidence_refs': [],
        }}


def local_case_factories():
    encoders = []
    models = []
    embedding_audit = HttpUsageAudit('embedding')
    model_audit = HttpUsageAudit('model')

    def configure_queries(answerable, no_answer):
        models[:] = [AnswerableModel(answerable), NoAnswerModel(no_answer)]

    def embedder_factory():
        encoder = LocalEncoder()
        encoders.append(encoder)
        return encoder

    def model_factory():
        if not models:
            raise RuntimeError('no scripted model remaining')
        return models.pop(0)

    return CaseFactories(
        embedder_factory=embedder_factory,
        model_factory=model_factory,
        configure_queries=configure_queries,
        document_inputs_per_start=lambda: [encoder.document_inputs for encoder in encoders],
        embedding_audit=embedding_audit,
        model_audit=model_audit,
        close=lambda: None,
        poll_seconds=0.02,
    )


def real_case_factories():
    embedding_audit = HttpUsageAudit('embedding')
    model_audit = HttpUsageAudit('model')
    model_clients = []
    encoders = []

    async def on_request(request):
        embedding_audit.request(request)

    async def on_response(response):
        await response.aread()
        embedding_audit.response(response)

    def configure_queries(_answerable, _no_answer):
        return None

    def async_embedding_client(**kwargs):
        return httpx.AsyncClient(
            **kwargs,
            event_hooks={'request': [on_request], 'response': [on_response]},
        )

    def embedder_factory():
        embedder = QwenFlashEmbedder(
            base_url=os.environ['ANTISENTINEL_EMBEDDING_BASE_URL'],
            api_key=os.environ['DASHSCOPE_API_KEY'],
            dimension=int(os.getenv('ANTISENTINEL_EMBEDDING_DIMENSION', '1024')),
            max_retries=0,
            async_client_factory=async_embedding_client,
        )
        embedder.client.event_hooks['request'].append(embedding_audit.request)
        embedder.client.event_hooks['response'].append(embedding_audit.response)
        counted = CountingEncoder(embedder, embedding_audit)
        encoders.append(counted)
        return counted

    def model_factory():
        client = httpx.Client(event_hooks={
            'request': [model_audit.request], 'response': [model_audit.response],
        })
        model_clients.append(client)
        return OpenAICompatibleModelAdapter(
            base_url=os.environ['ANTISENTINEL_MODEL_BASE_URL'],
            api_key=os.environ['ANTISENTINEL_MODEL_API_KEY'],
            model=os.environ['ANTISENTINEL_MODEL_NAME'],
            timeout=float(os.getenv('ANTISENTINEL_MODEL_TIMEOUT_SECONDS', '30')),
            client=client,
        )

    def close():
        for client in model_clients:
            client.close()

    return CaseFactories(
        embedder_factory=embedder_factory,
        model_factory=model_factory,
        configure_queries=configure_queries,
        document_inputs_per_start=lambda: [encoder.document_inputs for encoder in encoders],
        embedding_audit=embedding_audit,
        model_audit=model_audit,
        close=close,
        remote=True,
        unanswered_embedding_failures=lambda: sum(encoder.unanswered_failures for encoder in encoders),
    )


class CountingEncoder:
    def __init__(self, delegate, audit=None):
        self.delegate = delegate
        self.document_inputs = self.query_inputs = 0
        self.audit = audit
        self.unanswered_failures = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def embed_documents(self, texts):
        self.document_inputs += len(texts)
        return self._call(self.delegate.embed_documents, texts)

    def embed_query(self, texts):
        self.query_inputs += len(texts)
        return self._call(self.delegate.embed_query, texts)

    def _call(self, method, texts):
        before = self.audit.snapshot() if self.audit else None
        try:
            return method(texts)
        except Exception:
            if before is not None:
                after = self.audit.snapshot()
                self.unanswered_failures += max(0, (after['requests'] - before['requests'])
                    - (after['responses'] - before['responses']))
            raise

    def close(self):
        self.delegate.close()


def _wait_for(predicate, deadline, message):
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise TimeoutError(message)


def _start_service(factories):
    service = DiagnosisApplicationService.from_environment()
    if service.retrieval_coordinator is None:
        raise RuntimeError('retrieval coordinator was not configured')
    service.retrieval_coordinator.embedder_factory = factories.embedder_factory
    service.retrieval_coordinator.poll_seconds = factories.poll_seconds
    def observed_model():
        delegate = factories.model_factory()
        class ModelObserver:
            def complete(self, request):
                sid = str(request.session_id)
                factories.model_calls[sid] = factories.model_calls.get(sid, 0) + 1
                disclosed = factories.disclosures.setdefault(sid, set())
                for message in request.messages:
                    if message.get('role') == 'source_context':
                        disclosed.update(str(item['evidence_id']) for item in message['slices'])
                return delegate.complete(request)
        return ModelObserver()
    service.model_factories['real'] = observed_model
    return service


def _wait_ready(client, scope_count, deadline):
    def ready():
        health = client.get('/api/retrieval/status').json()
        if health['projection_errors']:
            raise RuntimeError(f"retrieval projection failed: {health}")
        if health['state'] == 'failed' or any(health['runs'].get(state, 0) for state in ('blocked', 'degraded', 'failed')):
            raise RuntimeError('retrieval run blocked, degraded or failed')
        return health if health['runs'] == {'ready': scope_count} else None

    return _wait_for(ready, deadline, 'retrieval projection did not become ready')


def _run_session(client, service, query, deadline):
    incident_response = client.post('/api/incidents', json={
        'title': query['query'],
        'summary': '请使用code_retrieval.search的hybrid模式检索，并用read_evidence核对固定版本源码后给出结论；证据不足时明确拒答。',
        'source': 'prd005-r1-case',
    })
    if incident_response.status_code != 201:
        raise RuntimeError(f'incident creation failed: {incident_response.text}')
    incident = incident_response.json()
    scope = query['scope']
    service.code_map_store.bind_incident(
        incident['incident_id'], scope['repository_id'], scope['snapshot_id']
    )
    session_response = client.post(
        f"/api/incidents/{incident['incident_id']}/sessions",
        json={'participant_ids': ['prd005-case'], 'model_mode': 'real'},
    )
    if session_response.status_code != 202:
        raise RuntimeError(f'session start failed: {session_response.text}')
    session_id = session_response.json()['session_id']

    def terminal():
        value = client.get(f'/api/sessions/{session_id}').json()
        if value.get('session_status') == 'failed' or value.get('status') == 'failed':
            raise RuntimeError('session background or runtime failure')
        return value if value.get('status') != 'running' else None

    result = _wait_for(terminal, deadline, 'session did not reach terminal state')
    business_seen = time.monotonic()
    persisted = _wait_for(lambda: service.application_store.load_result(session_id), deadline,
                          'terminal session result was not persisted')
    return incident, session_id, result, persisted, business_seen, time.monotonic()


def verified_hybrid_search(result, scope):
    searches = {call['tool_call_id']: call for call in result['tool_calls']
                if call['tool_name'] == 'code_retrieval.search'}
    if not searches or any(call['arguments'].get('mode', 'hybrid') != 'hybrid' for call in searches.values()):
        return False
    seen = set()
    for attempt in result['attempts']:
        cid = attempt['tool_call_id']
        if cid not in searches:
            continue
        try:
            summary = json.loads(attempt['result_summary'])
            valid = (attempt['status'] == 'succeeded' and summary['degraded'] is False
                     and summary['incomplete'] is False and summary['error_code'] is None
                     and summary['channel_statuses'] == {'keyword': 'ready', 'vector': 'ready'}
                     and all(all(hit['source_identity'].get(k) == v for k, v in scope.items())
                             for hit in summary['hits']))
        except (KeyError, TypeError, ValueError):
            return False
        if not valid:
            return False
        seen.add(cid)
    return seen == set(searches)


def restored_result_matches(original, reopened):
    # The HTTP view adds conversation messages. Every persisted result field must survive.
    return (reopened.get('status') == 'completed'
            and all(key in reopened and reopened[key] == value for key, value in original.items()))


def _evidence_document_matches(service, references):
    rows = [dict(row) for row in service.sqlite_database.query('SELECT * FROM code_search_documents')]
    matched = 0
    vector_matches = 0
    for reference in references:
        evidence = service.code_map_evidence_store.get(reference['evidence_id'])
        if evidence is None:
            continue
        metadata = evidence.metadata
        candidates = [row for row in rows if (
            row['repository_id'] == metadata['repository_id']
            and row['snapshot_id'] == metadata['snapshot_id']
            and row['published_generation'] == metadata['generation']
            and row['commit_sha'] == metadata['commit_sha']
            and row['node_id'] == metadata['node_id']
            and row['chunk_id'] == metadata['chunk_id']
            and row['path'] == metadata['path']
            and row['source_hash'] == evidence.content_hash
            and row['byte_start'] == metadata['byte_start']
            and row['byte_end'] == metadata['byte_end']
        )]
        if len(candidates) != 1:
            continue
        matched += 1
        task = service.sqlite_database.query(
            'SELECT * FROM code_embedding_tasks WHERE document_id=? AND status=?',
            (candidates[0]['document_id'], 'ready'),
        )
        if len(task) != 1:
            continue
        point = service.retrieval_coordinator.index.get([task[0]['point_id']])
        if len(point) == 1 and all(point[0].get(key) == task[0][key] for key in (
            'document_id', 'input_hash', 'model_revision', 'dimension',
            'template_revision', 'projection_revision',
        )):
            vector_matches += 1
    return matched, vector_matches


def run(repository, manifest, output, milvus_uri, answerable_query_id, no_answer_query_id,
        timeout=120, *, factories=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be positive and finite')
    factories = factories or real_case_factories()
    frozen = freeze_case_input(repository, manifest, answerable_query_id, no_answer_query_id)
    factories.configure_queries(frozen.answerable_query['query'], frozen.no_answer_query['query'])
    output.mkdir(parents=True)
    started = time.monotonic()
    deadline = started + timeout
    database = SQLiteDatabase(output / 'facts.sqlite')
    store = publish_graph_corpus(frozen.corpus, database)
    scope_count = len({
        (document.repository_id, document.snapshot_id, document.published_generation, document.commit_sha)
        for document in frozen.corpus.documents
    })
    collection_base = 'prd005_case_' + hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:20]
    values = {
        'ANTISENTINEL_STORAGE_ROOT': str(output / 'storage'),
        'ANTISENTINEL_SQLITE_PATH': str(output / 'facts.sqlite'),
        'ANTISENTINEL_PERSISTENCE_MODE': 'sqlite',
        'ANTISENTINEL_MEMORY_VECTOR_ENABLED': '0',
        'ANTISENTINEL_MODEL_MODE': 'real' if factories.remote else 'fake',
        'ANTISENTINEL_REDIS_URL': '',
        'ANTISENTINEL_CODE_RETRIEVAL_ENABLED': 'true',
        'ANTISENTINEL_CODE_RETRIEVAL_REPOSITORIES': frozen.answerable_query['scope']['repository_id'],
        'ANTISENTINEL_CODE_RETRIEVAL_MILVUS_URI': str(milvus_uri),
        'ANTISENTINEL_CODE_RETRIEVAL_MILVUS_COLLECTION': collection_base,
        'ANTISENTINEL_CODE_RETRIEVAL_PROJECTION': SLICE_REVISION,
        'ANTISENTINEL_CODE_RETRIEVAL_QUERY_TIMEOUT': '10',
    }
    checks = {}
    health = []
    services = []
    session_records = []
    business_completed = persistence_completed = verified_completed = None
    try:
        with case_environment(values):
            first = _start_service(factories)
            services.append(first)
            with TestClient(create_app(first)) as client:
                health.append(_wait_ready(client, scope_count, deadline))
                for query in (frozen.answerable_query, frozen.no_answer_query):
                    session_records.append(_run_session(client, first, query, deadline))
                business_completed = max(record[4] for record in session_records)
                persistence_completed = max(record[5] for record in session_records)
                checks['normal_http_entry'] = True
                checks['sessions_completed'] = all(record[2]['status'] == 'completed' for record in session_records)
                answerable = session_records[0][3]
                answerable_refs = [{'evidence_id': value} for value in sorted({str(item['evidence_id']) for item in answerable['final']['evidence_refs']})]
                checks['answerable_has_evidence'] = bool(answerable_refs)
                checks['model_calls_observed'] = all(factories.model_calls.get(record[1], 0) > 0 for record in session_records)
                checks['citations_disclosed'] = all(
                    {str(ref['evidence_id']) for ref in record[3]['final']['evidence_refs']}
                    <= factories.disclosures.get(record[1], set()) for record in session_records)
                names = {call['tool_name'] for call in answerable['tool_calls']}
                checks['retrieval_tools_executed'] = {'code_retrieval.search', 'code_retrieval.read_evidence'} <= names
                checks['tool_attempts_succeeded'] = all(attempt['status'] == 'succeeded' for record in session_records for attempt in record[3]['attempts'])
                checks['hybrid_channels_and_scope'] = all(verified_hybrid_search(record[3], query['scope'])
                    for record, query in zip(session_records, (frozen.answerable_query, frozen.no_answer_query)))
                all_refs = [{'evidence_id': value} for value in sorted(set().union(*factories.disclosures.values()))]

            second = _start_service(factories)
            services.append(second)
            with TestClient(create_app(second)) as client:
                health.append(_wait_ready(client, scope_count, deadline))
                reopened = [client.get(f'/api/sessions/{record[1]}').json() for record in session_records]
                checks['reopened_results'] = all(restored_result_matches(record[3], item)
                    for record, item in zip(session_records, reopened))
                restored = SourceEvidenceService(
                    None, second.code_map_store, second.code_map_evidence_store
                ).rehydrate(all_refs)
                checks['evidence_rehydrated'] = len(restored) == len(all_refs) and all(hashlib.sha256(item.content.encode()).hexdigest() == item.content_hash for item in restored)
                matched, vector_matches = _evidence_document_matches(second, all_refs)
                checks['evidence_to_active_document'] = matched == len(all_refs)
                checks['evidence_to_vector_version'] = vector_matches == len(all_refs)
                runs = second.sqlite_database.query('SELECT report_json FROM code_embedding_projection_runs')
                reports = [json.loads(row['report_json']) for row in runs]
                checks['all_vectors_reconciled'] = len(reports) == scope_count and all(
                    report['expected_ids'] == report['actual_ids'] and not report['field_mismatches']
                    and not report['missing_ids'] and not report['extra_ids'] for report in reports)
                rows = second.sqlite_database.query('SELECT projection_revision FROM code_search_documents')
                task_rows = second.sqlite_database.query('SELECT projection_revision FROM code_embedding_tasks')
                checks['projection_revision_matches'] = bool(rows) and all(
                    row['projection_revision'] == SLICE_REVISION for row in rows
                ) and all(row['projection_revision'] == SLICE_REVISION for row in task_rows)
                checks['sqlite_integrity'] = second.sqlite_database.query('PRAGMA integrity_check')[0][0] == 'ok'
                verified_completed = time.monotonic()
                database = second.sqlite_database

        document_inputs = factories.document_inputs_per_start()
        health.extend(service.retrieval_coordinator.health() for service in services)
        checks['coordinators_stopped'] = all(service.retrieval_coordinator.health()['state'] == 'stopped' and not service.retrieval_coordinator.health()['thread_alive'] for service in services)
        checks['audit_writes_succeeded'] = all(not service.audit_degraded for service in services)
        checks['session_threads_stopped'] = not any(thread.name in {'antisentinel-session-' + record[1][:8] for record in session_records} for thread in threading.enumerate())
        checks['no_document_reembedding'] = len(document_inputs) >= 2 and document_inputs[1] == 0
        checks['background_errors_zero'] = all(item['projection_errors'] == 0 for item in health)
        references_count = len(all_refs)
        counts = {
            'sessions': len(session_records),
            'persisted_results': database.query('SELECT COUNT(*) FROM sessions WHERE result_json IS NOT NULL')[0][0],
            'rehydrated_evidence': len(restored),
            'documents': database.query('SELECT COUNT(*) FROM code_search_documents')[0][0],
            'tasks': database.query('SELECT COUNT(*) FROM code_embedding_tasks')[0][0],
            'ready_tasks': database.query("SELECT COUNT(*) FROM code_embedding_tasks WHERE status='ready'")[0][0],
            'evidence': database.query('SELECT COUNT(*) FROM evidence')[0][0],
            'evidence_references': references_count,
            'identity_matches': vector_matches,
            'vectors': sum(len(report['actual_ids']) for report in reports),
            'embedding_attempts': database.query('SELECT COUNT(*) FROM code_embedding_attempts')[0][0],
        }
        checks['all_records_present'] = counts['documents'] == counts['tasks'] == counts['ready_tasks'] == counts['vectors'] and counts['documents'] > 0
        checks['all_results_persisted'] = counts['persisted_results'] == 2
        embedding_http, model_http = factories.embedding_audit.snapshot(), factories.model_audit.snapshot()
        checks['remote_calls_observed'] = (not factories.remote or (
            embedding_http['requests'] > 0 and model_http['requests'] > 0
            and embedding_http['responses'] + factories.unanswered_embedding_failures() == embedding_http['requests']
            and model_http['responses'] == model_http['requests']))
        factories.close()
        retries = sum(max(0, row[0] - 1) for row in database.query('SELECT attempt FROM code_embedding_tasks'))
        unexpected_background_exceptions = sum(item['projection_errors'] for item in health)
        elapsed_ms = (time.monotonic() - started) * 1000
        report = {
            'case': 'prd005-r1-real-application-session',
            'case_pass': all(checks.values()) and unexpected_background_exceptions == 0 and elapsed_ms <= timeout * 1000,
            'execution_pass': all(checks.values()) and unexpected_background_exceptions == 0 and elapsed_ms <= timeout * 1000,
            'quality_pass': False,
            'quality_reason': 'two-query integration pilot is not a reviewed quality set',
            'checks': checks,
            'input': {
                'manifest_sha256': frozen.manifest_sha256,
                'input_files': frozen.corpus.metadata['source_count'],
                'input_bytes': frozen.corpus.input_bytes,
                'input_documents': len(frozen.corpus.documents),
                'answerable_query_id': frozen.answerable_query['id'],
                'no_answer_query_id': frozen.no_answer_query['id'],
                'scope': frozen.answerable_query['scope'],
                'projection_revision': SLICE_REVISION,
                'collection_base': collection_base,
                'poll_seconds': factories.poll_seconds,
                'model_kind': 'remote' if factories.remote else 'local_fixture',
            },
            'counts': counts,
            'embedding': {
                'document_inputs_per_start': document_inputs,
                'http': factories.embedding_audit.snapshot(),
                'unanswered_transport_failures': factories.unanswered_embedding_failures(),
            },
            'model': {'http': factories.model_audit.snapshot(), 'calls_per_session': factories.model_calls},
            'retries': retries,
            'unexpected_background_exceptions': unexpected_background_exceptions,
            'health': health,
            'business_completed_ms': (business_completed - started) * 1000,
            'persistence_completed_ms': (persistence_completed - started) * 1000,
            'storage_verified_ms': (verified_completed - started) * 1000,
            'persistence_lag_ms': (persistence_completed - business_completed) * 1000,
            'verification_lag_ms': (verified_completed - business_completed) * 1000,
            'elapsed_ms': elapsed_ms,
            'evidence_projection_identity_persisted': False,
            'timing_semantics': 'API terminal/result persistence are polling observations; verification gap includes reopen',
        }
        assert_report_safe(report)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        return report
    except Exception as error:
        failure = {'case': 'prd005-r1-real-application-session', 'case_pass': False, 'error_type': type(error).__name__}
        assert_report_safe(failure)
        (output / 'failure.json').write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
        raise
    finally:
        for service in services:
            classifier = getattr(service.memory_recorder, 'memory_classifier', None)
            if getattr(classifier, 'client', None) is not None:
                classifier.client.close()
        factories.close()


def preflight(repository, manifest, answerable_query_id, no_answer_query_id, milvus_uri):
    frozen = freeze_case_input(repository, manifest, answerable_query_id, no_answer_query_id)
    per_scope = Counter((d.repository_id, d.snapshot_id, d.published_generation, d.commit_sha) for d in frozen.corpus.documents)
    def endpoint_identity(value):
        if not value:
            return {'kind': 'not_configured'}
        url = urlsplit(value)
        return {'scheme': url.scheme, 'host': url.hostname, 'port': url.port} if url.scheme else {'kind': 'local_file'}
    return {
        'manifest_sha256': frozen.manifest_sha256,
        'source_count': frozen.corpus.metadata['source_count'],
        'input_bytes': frozen.corpus.input_bytes,
        'input_documents': len(frozen.corpus.documents),
        'answerable_query_id': frozen.answerable_query['id'],
        'no_answer_query_id': frozen.no_answer_query['id'],
        'scope': frozen.answerable_query['scope'],
        'all_scopes': [dict(repository_id=key[0], snapshot_id=key[1], published_generation=key[2], commit_sha=key[3], documents=value) for key, value in per_scope.items()],
        'minimum_scheduler_wait_seconds': (max(per_scope.values()) - 1) * 5,
        'budget_note': 'Scheduler wait lower bound excludes parsing, slicing, network, models and shutdown. Slices may add tasks.',
        'frozen_versions': frozen.corpus.metadata,
        'embedding_model': QwenFlashEmbedder.model_name,
        'embedding_dimension': int(os.getenv('ANTISENTINEL_EMBEDDING_DIMENSION', '1024')),
        'model_name': os.getenv('ANTISENTINEL_MODEL_NAME') or None,
        'embedding_endpoint': endpoint_identity(os.getenv('ANTISENTINEL_EMBEDDING_BASE_URL', '')),
        'model_endpoint': endpoint_identity(os.getenv('ANTISENTINEL_MODEL_BASE_URL', '')),
        'milvus_endpoint': endpoint_identity(str(milvus_uri)),
        'milvus_uri_configured': bool(str(milvus_uri).strip()),
        'embedding_configured': all(bool(os.getenv(name, '').strip()) for name in (
            'DASHSCOPE_API_KEY', 'ANTISENTINEL_EMBEDDING_BASE_URL',
        )),
        'model_configured': all(bool(os.getenv(name, '').strip()) for name in (
            'ANTISENTINEL_MODEL_API_KEY', 'ANTISENTINEL_MODEL_BASE_URL', 'ANTISENTINEL_MODEL_NAME',
        )),
        'external_calls': 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--milvus-uri', required=True)
    parser.add_argument('--answerable-query-id', required=True)
    parser.add_argument('--no-answer-query-id', required=True)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args(argv)
    if args.preflight:
        print(json.dumps(preflight(
            args.repository, args.manifest, args.answerable_query_id,
            args.no_answer_query_id, args.milvus_uri,
        ), ensure_ascii=False, indent=2))
        return 0
    result = run(
        args.repository, args.manifest, args.output, args.milvus_uri,
        args.answerable_query_id, args.no_answer_query_id, args.timeout,
    )
    print(json.dumps({key: result[key] for key in ('case_pass', 'execution_pass', 'quality_pass')}, indent=2))
    return 0 if result['case_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
