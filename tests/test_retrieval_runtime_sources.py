from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.checkpoint import InMemoryCheckpointStore, RuntimeSnapshot
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine
from antisentinel.retrieval.evidence import CodeEvidenceAssembler
from antisentinel.retrieval.tools import build_code_retrieval_tools
from tests.test_retrieval_graph_boundaries import setup_source


class SourceModel:
    def __init__(self, candidates=None):
        self.candidates = candidates
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.candidates is not None:
            candidates, self.candidates = self.candidates, None
            return {'tasks': [{'task_id': 'read', 'objective': 'read source', 'tool_calls': [
                {'tool_name': 'code_retrieval.read_evidence', 'arguments': {'candidates': candidates}}
            ]}]}
        slices = [s for m in request.messages if m['role'] == 'source_context' for s in m['slices']]
        return {'final': {'summary': 'verified', 'diagnosis': 'source inspected', 'confidence': 1,
                          'evidence_refs': [{'evidence_id': s['evidence_id']} for s in slices]}}


def test_retrieval_evidence_enters_context_and_survives_serialized_checkpoint(tmp_path):
    store, scope, _, _, source = setup_source(tmp_path)
    incident = Incident.create(title='source', source='test')
    session = Session.create(incident_id=incident.incident_id, participant_ids=['worker'])
    store.bind_incident(str(incident.incident_id), scope['repository_id'], scope['snapshot_id'])
    chunks = store.read_generation(scope['snapshot_id'], 1)['chunks']
    candidates = [{**scope, 'chunk_id': c['chunk_id'], 'source_hash': c['content_hash']} for c in chunks]
    registry = ToolRegistry(auto_discover=False)
    for tool in build_code_retrieval_tools(None, lambda _: {**scope, 'incident_id': str(incident.incident_id)}, evidence_assembler=CodeEvidenceAssembler(source)):
        registry.register(tool)
    checkpoints = InMemoryCheckpointStore()
    first = SourceModel(candidates)
    RuntimeEngine().run(incident, session, first, registry=registry, checkpoint_store=checkpoints, config=RuntimeConfig(max_turns=1))
    saved = checkpoints.load(session.session_id)
    assert len(saved.source_context_refs) == len(candidates)
    assert len(store.database.query('SELECT * FROM evidence')) == len(candidates)

    # New store instance reconstructs the serialized checkpoint, as after restart.
    reopened = InMemoryCheckpointStore()
    reopened.save(RuntimeSnapshot.from_dict(saved.to_dict()))
    model = SourceModel()
    result = RuntimeEngine().run(incident, session, model, registry=registry, checkpoint_store=reopened,
                                 resume=True, source_context_rehydrator=source.rehydrate)
    assert result.status == 'completed'
    slices = next(m['slices'] for m in model.requests[0].messages if m['role'] == 'source_context')
    assert len(slices) == len(candidates)
    assert all(s['published_generation'] == 1 and s['byte_end'] > s['byte_start'] for s in slices)
    assert {r.evidence_id for r in result.final.evidence_refs} == {s['evidence_id'] for s in slices}
    assert len(store.database.query('SELECT * FROM evidence')) == len(candidates)

def test_multiple_source_calls_cannot_persist_beyond_context_budget(tmp_path):
    store, scope, _, chunk, source = setup_source(tmp_path)
    incident = Incident.create(title='budget', source='test')
    session = Session.create(incident_id=incident.incident_id, participant_ids=['worker'])
    store.bind_incident(str(incident.incident_id), scope['repository_id'], scope['snapshot_id'])
    candidate = {**scope, 'chunk_id': chunk['chunk_id']}
    registry = ToolRegistry(auto_discover=False)
    for tool in build_code_retrieval_tools(None, lambda _: {**scope, 'incident_id': str(incident.incident_id)}, evidence_assembler=CodeEvidenceAssembler(source)):
        registry.register(tool)

    class ManyReads:
        def complete(self, request):
            return {'tasks': [{'task_id': 'read', 'objective': 'read', 'tool_calls': [
                {'tool_name': 'code_retrieval.read_evidence', 'arguments': {'candidates': [candidate], 'max_bytes': 1000 + i}}
                for i in range(5)
            ]}]}

    result = RuntimeEngine().run(incident, session, ManyReads(), registry=registry, config=RuntimeConfig(max_turns=1))
    assert len(store.database.query('SELECT * FROM evidence')) <= 4
    assert len(result.evidence_refs) <= 4
