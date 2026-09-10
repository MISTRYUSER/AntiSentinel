"""Application-owned projection scheduling and serialized Lite client access."""
from dataclasses import replace
import hashlib
import math
from pathlib import Path
from threading import Event, Lock, RLock, Thread
import time
from uuid import uuid4

from antisentinel.domain.errors import DomainError
from antisentinel.runtime.deadline import query_budget, QueryDeadlineExceeded
from antisentinel.tools.manifest import ToolExecutionResult
from .embedding_worker import EmbeddingWorker
from .engine import CodeRetrievalService
from .evidence import CodeEvidenceAssembler
from .graph import GraphExpander
from .keyword import KeywordRetriever
from .models import CodeSearchDocument, CodeSearchScope
from .runtime import IncidentRetrievalTools
from .sqlite_store import SQLiteCodeSearchStore
from .vector import EmbeddingTaskStore
from .slicing import SLICE_REVISION, slice_documents


class _ProjectionStopped(Exception):
    """Internal cooperative cancellation; never a publication failure."""


class RetrievalCoordinator:
    def __init__(self, store, evidence_service, *, allowed_repositories, index_factory,
                 embedder_factory, poll_seconds=5.0, reconcile_seconds=60.0, query_timeout=10.0):
        if isinstance(allowed_repositories, (str, bytes)) or not allowed_repositories:
            raise ValueError('explicit repository allowlist required')
        self.repositories = frozenset(allowed_repositories)
        if any(not isinstance(repo, str) or not repo.strip() for repo in self.repositories):
            raise ValueError('invalid repository allowlist')
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in (poll_seconds, reconcile_seconds, query_timeout)):
            raise ValueError('invalid scheduler interval')
        self.store, self.evidence_service = store, evidence_service
        self.index_factory, self.embedder_factory = index_factory, embedder_factory
        self.poll_seconds, self.reconcile_seconds = poll_seconds, reconcile_seconds
        self.query_timeout = query_timeout
        self.lock, self.stopping = RLock(), Event()
        self.state_lock = Lock()
        self.thread = None
        self.state = 'new'
        self.errors = {}
        self.ticks = 0
        self.runs = {}
        self.last_reconciled = {}
        self.run_statuses = {}
        self.index = self.embedder = None

    def start(self):
        with self.lock:
            if self.state == 'running':
                return
            if self.state != 'new':
                raise RuntimeError('coordinator cannot be restarted; construct a new instance')
            try:
                self.embedder = self.embedder_factory()
                self.index = self.index_factory(self.embedder)
                self.lexical = SQLiteCodeSearchStore(self.store.database)
                self.channels = EmbeddingTaskStore(self.store.database)
                self.queue = self.channels.queue
                self.worker = EmbeddingWorker(self.queue, self.index, self.embedder, owner=uuid4().hex)
                graph = GraphExpander(self.store)
                service = CodeRetrievalService(KeywordRetriever(self.lexical), self.index,
                    channel_store=self.channels, model_revision=self.index.model_revision,
                    template_revision=self.index.template_revision,
                    projection_revision=self.index.projection_revision, graph_expander=graph,
                    graph_source_resolver=self._resolve_graph_source if self.index.projection_revision == SLICE_REVISION else None)
                self.bindings = IncidentRetrievalTools(service, self.store,
                    allowed_repositories=self.repositories, query_encoder=self._encode,
                    graph_expander=graph, evidence_assembler=CodeEvidenceAssembler(self.evidence_service))
                self.state = 'running'
                self.thread = Thread(target=self._run, name='antisentinel-retrieval', daemon=True)
                self.thread.start()
            except Exception:
                self.state = 'failed'
                self._close()
                raise

    def _encode(self, query):
        values = self.embedder.embed_query([query])
        if len(values) != 1:
            raise ValueError('query embedding count mismatch')
        return values[0]

    def _resolve_graph_source(self, scope, candidate):
        sources = self.lexical.graph_sources(scope, candidate, self.index.projection_revision)
        if not sources:
            aligned = self.store.read_generation_utf8_chunk(scope.repository_id, scope.snapshot_id, scope.published_generation, candidate['chunk_id'])
            if aligned['byte_start'] != aligned['byte_end'] or aligned['parent_source_hash'] != candidate['source_hash']:
                raise ValueError('graph source not projected')
        return sources

    def for_incident(self, incident_id):
        # Registration doesn't use clients and must not queue behind an embedding job.
        with self.state_lock:
            self._require_running()
            definitions = self.bindings.for_incident(incident_id)
        def guarded(definition):
            def call(arguments):
                if definition.name not in {'code_retrieval.search', 'code_retrieval.expand_graph'}:
                    with self.lock:
                        self._require_running()
                        return definition.handler(arguments)
                try:
                    with query_budget(self.query_timeout) as deadline:
                        if not self.lock.acquire(timeout=deadline.remaining()):
                            raise QueryDeadlineExceeded('query_deadline_exceeded')
                        try:
                            deadline.remaining()
                            self._require_running()
                            result = definition.handler(arguments)
                            deadline.remaining()
                            return result
                        finally:
                            self.lock.release()
                except QueryDeadlineExceeded:
                    return ToolExecutionResult(status='failed', error={'code': 'query_deadline_exceeded'},
                        result_summary='query deadline exceeded')
            return call
        return tuple(replace(item, handler=guarded(item)) for item in definitions)

    def _require_running(self):
        if self.state != 'running' or self.stopping.is_set():
            raise DomainError('retrieval_not_running')

    def _project(self, scope):
        self._check_stop()
        revision = self.index.projection_revision
        active = self.lexical.resolve_projection(scope, None)
        if active is not None and active != revision:
            raise ValueError('active projection requires explicit migration')
        if not self.lexical.has_ready_manifest(scope, projection_revision=revision):
            archive = self.store.read_generation(scope.snapshot_id, scope.published_generation)
            if archive['snapshot']['commit_sha'] != scope.commit_sha or archive['snapshot']['repository_id'] != scope.repository_id:
                raise ValueError('archived scope mismatch')
            nodes = {node['node_id']: node for node in archive['nodes']}
            documents = []
            for chunk in archive['chunks']:
                self._check_stop()
                reader = self.store.read_generation_utf8_chunk if revision == SLICE_REVISION else self.store.read_generation_chunk
                source = reader(scope.repository_id, scope.snapshot_id,
                    scope.published_generation, chunk['chunk_id'])
                node = nodes[chunk['node_id']]
                if revision == SLICE_REVISION:
                    documents.extend(slice_documents(scope, source, node['qualified_name'], Path(chunk['path']).suffix.lstrip('.') or 'unknown'))
                    continue
                text = source['content']
                if hashlib.sha256(text.encode('utf-8')).hexdigest() != chunk['content_hash']:
                    raise ValueError('source encoding hash mismatch')
                embedding_input = f"{chunk['path']}\n{node['qualified_name']}\n{text}"
                documents.append(CodeSearchDocument(*scope.key, chunk['node_id'], chunk['chunk_id'],
                    chunk['path'], node['qualified_name'], Path(chunk['path']).suffix.lstrip('.') or 'unknown',
                    chunk['content_hash'], hashlib.sha256(embedding_input.encode()).hexdigest(), revision, text))
            if not documents:
                raise ValueError('empty projection')
            self._check_stop()
            self.lexical.begin_manifest(scope, revision, documents)
            self.lexical.upsert_documents(documents)
            self.lexical.publish_manifest(scope, revision)
        self._check_stop()
        return self.queue.enqueue(scope, model_revision=self.index.model_revision,
            dimension=self.index.dimension, template_revision=self.index.template_revision,
            projection_revision=revision)

    def _check_stop(self):
        if self.stopping.is_set():
            raise _ProjectionStopped()

    def tick(self):
        with self.lock:
            if self.stopping.is_set():
                return
            if self.state != 'running':
                raise DomainError('retrieval_not_running')
            rows = self.store.database.query("SELECT repository_id,snapshot_id,published_generation,commit_sha FROM code_map_snapshots WHERE status='ready' AND published_generation IS NOT NULL ORDER BY repository_id,snapshot_id")
            for row in rows:
                if self.stopping.is_set():
                    return
                if row['repository_id'] not in self.repositories:
                    continue
                scope = CodeSearchScope(**dict(row))
                key = scope.key
                if key in self.runs or key in self.errors:
                    continue
                try:
                    self.runs[key] = self._project(scope)
                except _ProjectionStopped:
                    return
                except Exception as error:
                    # Quarantine this publication until restart/operator repair; never spin on it.
                    self.errors[key] = type(error).__name__
            now = time.monotonic()
            for key, rid in self.runs.items():
                if self.stopping.is_set():
                    return
                run = self.queue.run(rid)
                if run['status'] == 'blocked':
                    continue
                if run['status'] == 'ready' and now - self.last_reconciled.get(rid, -math.inf) < self.reconcile_seconds:
                    continue
                self.worker.run_once(rid)
                self.last_reconciled[rid] = now
            statuses = {}
            for rid in self.runs.values():
                status = self.queue.run(rid)['status']
                statuses[status] = statuses.get(status, 0) + 1
            self.run_statuses = statuses
            self.ticks += 1

    def _run(self):
        try:
            while not self.stopping.is_set():
                self.tick()
                self.stopping.wait(self.poll_seconds)
        except Exception as error:
            self.errors['scheduler'] = type(error).__name__
            with self.state_lock:
                self.state = 'failed'
        finally:
            with self.lock:
                self._close()
                with self.state_lock:
                    if self.state != 'failed':
                        self.state = 'stopped'

    def _close(self):
        for name in ('index', 'embedder'):
            client = getattr(self, name)
            if client is not None:
                try:
                    client.close()
                except Exception as error:
                    self.errors['close_' + name] = type(error).__name__
                    with self.state_lock:
                        self.state = 'failed'
                setattr(self, name, None)

    def stop(self, timeout=40.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('invalid shutdown timeout')
        with self.state_lock:
            self.stopping.set()
            if self.state == 'running':
                self.state = 'stopping'
            elif self.state == 'new':
                self.state = 'stopped'
        if self.thread is not None:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise TimeoutError('retrieval shutdown still waiting for in-flight work')
        if self.state == 'failed':
            raise RuntimeError('retrieval failed; inspect coordinator status')

    def health(self):
        # Do not wait behind a network RPC just to observe scheduler health.
        return {'state': self.state, 'ticks': self.ticks, 'projection_errors': len(self.errors),
                'thread_alive': bool(self.thread and self.thread.is_alive()),
                'runs': dict(self.run_statuses)}
