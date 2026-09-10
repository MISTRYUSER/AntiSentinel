from types import SimpleNamespace
import time

import pytest

from antisentinel.domain.errors import DomainError
from antisentinel.domain.incident import Incident
from antisentinel.entry.application import DiagnosisApplicationService
from antisentinel.retrieval.engine import RetrievalResult
from antisentinel.retrieval.evidence import CodeEvidenceAssembler
from antisentinel.retrieval.runtime import IncidentRetrievalTools
from antisentinel.tools.registry import ToolRegistry
from tests.test_retrieval_graph_boundaries import setup_source
from tests.test_retrieval_runtime_sources import SourceModel


def setup_tools(tmp_path):
    store, scope, _, chunk, source = setup_source(tmp_path)
    calls = []
    def search(query, selected, **options):
        calls.append((query, selected, options))
        return RetrievalResult((), options['mode'], {})
    encoded = []
    def encode(query):
        encoded.append(query)
        return (1., 0.)
    tools = IncidentRetrievalTools(SimpleNamespace(search=search), store,
        allowed_repositories=['repo-a'], query_encoder=encode,
        evidence_assembler=CodeEvidenceAssembler(source))
    return tools, store, scope, chunk, source, calls, encoded


def test_server_encoding_and_production_schema(tmp_path):
    tools, _, scope, _, _, calls, encoded = setup_tools(tmp_path)
    search = tools.for_incident('incident-a')[0]
    assert 'query_vector' not in search.argument_schema['properties']
    assert 'repository_id' in search.argument_schema['properties']
    search.handler({'query': 'Service', 'mode': 'hybrid'})
    assert encoded == ['Service']
    assert calls[0][1].snapshot_id == scope['snapshot_id']
    assert calls[0][2]['query_vector'] == (1., 0.)
    search.handler({'query': 'Service', 'mode': 'keyword'})
    assert encoded == ['Service']


@pytest.mark.parametrize('extra', [
    {'repository_id': 'foreign'}, {'snapshot_id': 'foreign'},
    {'published_generation': 99}, {'incident_id': 'incident-b'},
    {'query_vector': [1, 0]}, {'top_k': 6}, {'candidate_limit': 31},
    {'mode': 'unknown'}, {'query': ' '}, {'top_k': True},
])
def test_invalid_input_does_not_upload_query(tmp_path, extra):
    tools, _, _, _, _, calls, encoded = setup_tools(tmp_path)
    with pytest.raises(DomainError):
        tools.for_incident('incident-a')[0].handler({'query': 'private', **extra})
    assert calls == encoded == []


def test_binding_rechecked_on_every_call(tmp_path):
    tools, store, _, _, _, _, encoded = setup_tools(tmp_path)
    search = tools.for_incident('incident-a')[0]
    search.handler({'query': 'first'})
    with store.database.transaction() as connection:
        connection.execute('DELETE FROM code_map_diagnosis_bindings')
    with pytest.raises(DomainError, match='unbound'):
        search.handler({'query': 'second'})
    assert encoded == ['first']


def test_normal_application_session_reads_persistent_evidence(tmp_path):
    tools, store, scope, chunk, source, _, encoded = setup_tools(tmp_path)
    incident = Incident.create(title='source', source='test')
    store.bind_incident(str(incident.incident_id), 'repo-a', scope['snapshot_id'])
    model = SourceModel([{**scope, 'chunk_id': chunk['chunk_id'], 'source_hash': chunk['content_hash']}])
    app = DiagnosisApplicationService.default_fake()
    app.code_map_store = store
    app.code_map_evidence_store = source.evidence_store
    app.retrieval_tools = tools
    app.incidents[str(incident.incident_id)] = incident
    app._runtime_builder = lambda: (model, ToolRegistry(auto_discover=False))
    started = app.start_session(incident_id=str(incident.incident_id), participant_ids=['worker'], model_mode='fake')
    deadline = time.monotonic() + 10
    while started['session_id'] not in app.results and time.monotonic() < deadline:
        time.sleep(.01)
    result = app.results[started['session_id']]
    assert result.status == 'completed'
    assert len(result.final.evidence_refs) == 1
    assert len(store.database.query('SELECT * FROM evidence')) == 1
    assert any(m['role'] == 'source_context' for m in model.requests[-1].messages)
    assert encoded == []
    assert app.audit_degraded == []


def test_default_application_has_no_retrieval_clients():
    assert DiagnosisApplicationService.default_fake().retrieval_tools is None
