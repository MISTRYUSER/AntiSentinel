"""Offline 5.4 verification with production parser, stores, tools and Runtime."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from antisentinel.code_map.models import MapSnapshot, RepositoryRegistration, CodeEdge
from antisentinel.code_map.python_parser import PythonAstParser
from antisentinel.code_map.store import SQLiteCodeMapStore, SourceBlob, SourceFile, StagedMapRows
from antisentinel.code_map.source_context import SourceEvidenceService
from antisentinel.domain.errors import DomainError
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.persistence.sqlite_stores import SQLiteEvidenceStore
from antisentinel.retrieval.graph import GraphExpander, GraphSeed
from antisentinel.retrieval.evidence import CodeEvidenceAssembler
from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.keyword import KeywordRetriever
from antisentinel.retrieval.models import CodeSearchDocument
from antisentinel.retrieval.sqlite_store import SQLiteCodeSearchStore
from antisentinel.retrieval.tools import build_code_retrieval_tools
from antisentinel.tools.registry import ToolRegistry
from antisentinel.tools.manifest import ToolExecutionResult
from antisentinel.worker.runtime.engine import RuntimeEngine, RuntimeConfig
from antisentinel.worker.runtime.checkpoint import RuntimeSnapshot


class FileCaseCheckpoint:
    def __init__(self, path):
        self.path = path

    def save(self, snapshot):
        self.path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False), encoding='utf-8')

    def load(self, session_id):
        if not self.path.exists():
            return None
        snapshot = RuntimeSnapshot.from_dict(json.loads(self.path.read_text()))
        if snapshot.session_id != session_id:
            raise ValueError('checkpoint session mismatch')
        return snapshot

    def clear(self, session_id):
        if self.path.exists():
            self.path.unlink()


class ScriptedGraphModel:
    """Deterministic protocol driver; no inference or external API call."""
    def __init__(self, resume=False):
        self.phase = 2 if resume else 0
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.phase == 0:
            name, args = 'code_retrieval.search', {'query': 'class', 'mode': 'graph'}
        elif self.phase == 1:
            results = next(m['task_results'] for m in request.messages if m['role'] == 'tool')
            hits = json.loads(results[0]['summary'])['hits']
            expanded = next(h for h in hits if h['channels'] == ['graph'])
            identity = expanded['source_identity']
            candidate = dict(identity)
            name, args = 'code_retrieval.read_evidence', {'candidates': [candidate]}
        else:
            slices = next(m['slices'] for m in request.messages if m['role'] == 'source_context')
            return {'final': {'summary': '源码已核验', 'diagnosis': '已回读方法源码；静态结构不代表实际执行轨迹',
                              'confidence': 1.0, 'evidence_refs': [{'evidence_id': s['evidence_id']} for s in slices]}}
        self.phase += 1
        return {'tasks': [{'task_id': f'step-{self.phase}', 'objective': '检查固定代次源码', 'tool_calls': [{'tool_name': name, 'arguments': args}]}]}


def run(output, *, timeout_seconds=120.0):
    output = Path(output)
    if timeout_seconds <= 0:
        raise ValueError('timeout must be positive')
    output.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    db = SQLiteDatabase(output / 'facts.sqlite')
    db.initialize()
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    store = SQLiteCodeMapStore(db, clock=lambda: now)
    store.register(RepositoryRegistration('repo', 'file:///fixture', 'local', 'main'))
    commit = 'a' * 40
    job = store.enqueue('repo', commit, 'manual')
    lease = store.claim_job('case', now)
    sid = 'fixture-snapshot'
    data = (b'class Service:\n    def child(self):\n        return 42\n'
            b'\ndef seed_one():\n    return "class one"\n'
            b'\ndef seed_two():\n    return "class two"\n'
            b'\ndef seed_three():\n    return "class three"\n'
            b'\ndef seed_four():\n    return "class four"\n')
    parser = PythonAstParser('python-ast-v1')
    parsed = parser.parse_file('source.py', data, sid)
    # An injected malformed contains edge verifies conservative handling in the allowed relation set.
    edges = parser.contains_edges((parsed,), sid) + (CodeEdge('unresolved', sid, parsed.symbols[0].node_id, 'contains', None, unresolved_expression='unknown_parent', resolution='unresolved', basis='text'),)
    published = store.publish(lease, MapSnapshot(sid, 'repo', commit, 'python-ast-v1', job.rules_digest, file_count=1), StagedMapRows(
        nodes=parsed.symbols, edges=edges, chunks=parsed.chunks,
        blobs=(SourceBlob(parsed.file_hash, 'object-a', data, parsed.encoding),),
        files=(SourceFile('file-a', sid, 'source.py', 'object-a', parsed.file_hash, len(data)),)))
    assert published.ok
    scope = dict(repository_id='repo', snapshot_id=sid, published_generation=1, commit_sha=commit)
    incident = Incident.create(title='源码检查', source='fixture')
    session = Session.create(incident_id=incident.incident_id, participant_ids=['case'])
    store.bind_incident(str(incident.incident_id), 'repo', sid)
    seed = GraphSeed(parsed.symbols[0].node_id, 'root', 1)
    graph = GraphExpander(store)
    expanded = graph.expand([seed], scope=scope)
    # Disturb current facts only: the archived generation must remain authoritative.
    with db.transaction() as connection:
        connection.execute("UPDATE code_map_nodes SET qualified_name='new-current-generation'")
        connection.execute('DELETE FROM code_map_edges')
    isolated = graph.expand([seed], scope=scope)
    generation_isolated = isolated == expanded
    source = SourceEvidenceService(None, store, SQLiteEvidenceStore(db))
    assembler = CodeEvidenceAssembler(source)
    child = parsed.chunks[1]
    candidate = {**scope, 'chunk_id': child.chunk_id, 'source_hash': child.content_hash}
    negative_results = {}
    for relation in ('calls', 'imports', 'inherits', 'tested_by'):
        try:
            graph.expand([seed], scope=scope, relations=(relation,))
            negative_results[f'relation_{relation}'] = False
        except ValueError:
            negative_results[f'relation_{relation}'] = True
    for field, value in [('published_generation', 99), ('repository_id', 'foreign'), ('commit_sha', 'b' * 40)]:
        try:
            graph.expand([seed], scope={**scope, field: value})
            negative_results[field] = False
        except DomainError:
            negative_results[field] = True
    try:
        assembler.select(str(incident.incident_id), [{**candidate, 'source_hash': 'wrong'}])
        negative_results['hash'] = False
    except DomainError:
        negative_results['hash'] = True
    budget = assembler.select(str(incident.incident_id), [candidate], max_bytes=1)
    negative_results['budget'] = budget.truncated and not budget.slices
    budget_written = db.query('SELECT COUNT(*) FROM evidence')[0][0]
    search_store = SQLiteCodeSearchStore(db)
    documents = [CodeSearchDocument('repo', sid, 1, commit, c.node_id, c.chunk_id, c.path,
                 n.qualified_name, 'python', c.content_hash, c.content_hash, 'projection-v1',
                 data[c.byte_start:c.byte_end].decode()) for n, c in zip(parsed.symbols, parsed.chunks)]
    from antisentinel.retrieval.models import CodeSearchScope
    search_store.begin_manifest(CodeSearchScope(**scope), 'projection-v1', documents)
    search_store.upsert_documents(documents)
    search_store.publish_manifest(CodeSearchScope(**scope), 'projection-v1')
    service = CodeRetrievalService(KeywordRetriever(search_store), None, channel_store=None,
                model_revision='none', template_revision='none', projection_revision='projection-v1', graph_expander=graph)
    registry = ToolRegistry(auto_discover=False)
    for tool in build_code_retrieval_tools(service, lambda _: {**scope, 'incident_id': str(incident.incident_id)}, graph_expander=graph, evidence_assembler=assembler):
        registry.register(tool)
    graph_tool = registry.resolve('code_retrieval.expand_graph')
    tool_result = graph_tool.handler({'seeds': [{'node_id': seed.node_id, 'seed_document_id':seed.seed_document_id, 'rank':1}], 'edge_budget':1})
    typed_truncated_result = isinstance(tool_result, ToolExecutionResult) and tool_result.result['truncated'] and tool_result.result['incomplete']
    checkpoint = FileCaseCheckpoint(output / 'checkpoint.json')
    first = ScriptedGraphModel()
    interrupted = RuntimeEngine().run(incident, session, first, registry=registry, checkpoint_store=checkpoint, config=RuntimeConfig(max_turns=2))
    saved = checkpoint.load(session.session_id)
    for field, changed, original in [('published_generation', 2, 1), ('requested_commit', 'b' * 40, commit)]:
        with db.transaction() as conn:
            conn.execute(f'UPDATE code_map_diagnosis_bindings SET {field}=? WHERE incident_id=?', (changed, str(incident.incident_id)))
        try:
            source.rehydrate(saved.source_context_refs)
            negative_results[f'binding_{field}'] = False
        except DomainError:
            negative_results[f'binding_{field}'] = True
        finally:
            with db.transaction() as conn:
                conn.execute(f'UPDATE code_map_diagnosis_bindings SET {field}=? WHERE incident_id=?', (original, str(incident.incident_id)))
    restored_db = SQLiteDatabase(output / 'facts.sqlite')
    restored_source = SourceEvidenceService(None, SQLiteCodeMapStore(restored_db), SQLiteEvidenceStore(restored_db))
    restored_slices = restored_source.rehydrate(saved.source_context_refs)
    resumed_model = ScriptedGraphModel(resume=True)
    result = RuntimeEngine().run(incident, session, resumed_model, registry=registry,
                checkpoint_store=FileCaseCheckpoint(output / 'checkpoint.json'), resume=True,
                source_context_rehydrator=restored_source.rehydrate)
    # Exercise the normal application's tool registration and session thread too.
    from antisentinel.entry.application import DiagnosisApplicationService
    from antisentinel.retrieval.runtime import IncidentRetrievalTools

    def forbidden_encoder(query):
        raise AssertionError('graph mode must not call an embedding provider')

    application = DiagnosisApplicationService.default_fake()
    application.code_map_store = store
    application.code_map_evidence_store = source.evidence_store
    application.retrieval_tools = IncidentRetrievalTools(service, store,
        allowed_repositories=['repo'], query_encoder=forbidden_encoder,
        graph_expander=graph, evidence_assembler=assembler)
    application.incidents[str(incident.incident_id)] = incident
    application_model = ScriptedGraphModel()
    application._runtime_builder = lambda: (application_model, ToolRegistry(auto_discover=False))
    started = application.start_session(incident_id=str(incident.incident_id), participant_ids=['case'], model_mode='fake')
    deadline = start + timeout_seconds
    while started['session_id'] not in application.results and time.perf_counter() < deadline:
        time.sleep(.01)
    application_result = application.results.get(started['session_id'])
    application_pass = (application_result is not None and application_result.status == 'completed'
        and len(application_result.final.evidence_refs) == 1
        and not application.audit_degraded
        and all(attempt.status.value == 'succeeded' for attempt in application_result.attempts))
    application_refs = {str(ref.evidence_id) for ref in application_result.final.evidence_refs} if application_pass else set()
    application_slices = [item for message in application_model.requests[-1].messages
                          if message['role'] == 'source_context' for item in message['slices']]
    application_evidence_valid = (len(application_slices) == 1
        and application_refs == {item['evidence_id'] for item in application_slices}
        and all(hashlib.sha256(item['content'].encode()).hexdigest() == item['content_hash'] for item in application_slices))
    t1 = time.perf_counter()
    evidence_rows = restored_db.query('SELECT * FROM evidence')
    hashes = sum(hashlib.sha256(s.content.encode()).hexdigest() == s.content_hash for s in restored_slices)
    context_slices = [s for m in resumed_model.requests[0].messages if m['role'] == 'source_context' for s in m['slices']]
    quoted = {str(r.evidence_id) for r in result.final.evidence_refs} if result.final else set()
    disclosed = {s['evidence_id'] for s in context_slices}
    links = quoted == disclosed and len(quoted) == 1
    errors = [a for a in result.attempts if a.status.value != 'succeeded']
    keyword_seeds = service.keyword_retriever.search(CodeSearchScope(**scope), 'class', limit=30)
    runtime_hits = interrupted.attempts[0].result['hits']
    graph_visible = len(keyword_seeds) == 5 and len(runtime_hits) == 5 and any('graph' in h['channels'] for h in runtime_hits)
    checks = dict(application_runtime=application_pass, expansion=len(expanded.items) == 1, unresolved=expanded.unresolved_count == 1,
                  generation=generation_isolated, negative=all(negative_results.values()), budget_no_write=budget_written == 0,
                  runtime=result.status == 'completed', checkpoint=len(saved.source_context_refs) == 1,
                  evidence=len(evidence_rows) == 2 and {row['evidence_id'] for row in evidence_rows} == quoted | application_refs,
                  application_evidence=application_evidence_valid,
                  hashes=hashes == 1, disclosed=links, tool_errors=not errors,
                  interrupted=interrupted.error is not None and interrupted.error['code'] == 'max_turns_exceeded',
                  full_seeds_graph_visible=graph_visible, graph_tool_contract=typed_truncated_result)
    t2 = time.perf_counter()
    checks['deadline'] = t2 - start <= timeout_seconds
    report = dict(case='graph', input_files=1, input_bytes=len(data), elapsed_ms=round((t2-start)*1000, 2),
        expanded_nodes=len(expanded.items), unresolved_edges=expanded.unresolved_count, graph_degraded=expanded.degraded,
        output_count=len(context_slices), persisted_documents=len(parsed.chunks), evidence_count=len(evidence_rows),
        hash_verified=hashes, scope_leaks=sum(not negative_results[k] for k in ('published_generation','repository_id','commit_sha')),
        negative_checks=len(negative_results), negative_results=negative_results, budget_rejected_evidence=budget_written,
        generation_isolated=generation_isolated, runtime_completed=result.status == 'completed',
        checkpoint_restored=len(restored_slices), model_kind='scripted_protocol_driver', external_model_calls=0,
        keyword_seed_count=len(keyword_seeds), graph_visible_with_full_seeds=graph_visible,
        typed_graph_tool_result=typed_truncated_result,
        application_runtime_completed=application_pass, application_evidence_count=len(application_refs),
        runtime_tool_calls=len(result.tool_calls), background_exceptions=int(application_result is None), worker_count=0, retries=0,
        business_completed_ms=round((t1-start)*1000,2), persistence_completed_ms=round((t2-start)*1000,2),
        persistence_lag_ms=round((t2-t1)*1000,2), failures=sum(not v for v in checks.values()),
        integrity_checks=sum(checks.values()), integrity_checks_threshold=len(checks), checks=checks,
        artifacts=3, case_pass=all(checks.values()))
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report
