import time
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from antisentinel.api.app import create_app
from antisentinel.domain.errors import DomainError
from antisentinel.entry.application import DiagnosisApplicationService
from antisentinel.retrieval.config import coordinator_from_environment
from antisentinel.retrieval.coordinator import RetrievalCoordinator
from antisentinel.retrieval.milvus_adapter import MilvusAdapter
from tests.test_retrieval_graph_boundaries import setup_source


class Encoder:
    model_name = 'local-coordinator-fixture'
    dimension = 3
    max_retries = 0
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


def make_coordinator(store, source, path, encoder, repositories=('repo-a',)):
    return RetrievalCoordinator(store, source, allowed_repositories=repositories,
        embedder_factory=lambda: encoder,
        index_factory=lambda e: MilvusAdapter(path / 'vectors.db', 'coordinator', dimension=e.dimension,
            model_revision=e.model_name, template_revision='path-symbol-source-v1',
            projection_revision='coordinator-v1', rpc_timeout=5), poll_seconds=.02)


def wait_ready(coordinator):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        rows = coordinator.store.database.query("SELECT status FROM code_embedding_projection_runs")
        if rows and all(row['status'] == 'ready' for row in rows) and coordinator.health()['runs'] == {'ready': 1}:
            return
        assert coordinator.health()['projection_errors'] == 0
        time.sleep(.02)
    pytest.fail('coordinator did not reach ready')


def test_lifespan_projects_searches_and_reopens_without_reembedding(tmp_path):
    store, scope, _, _, source = setup_source(tmp_path)
    expected = len(store.read_generation(scope['snapshot_id'], 1)['chunks'])
    for attempt in range(2):
        encoder = Encoder()
        coordinator = make_coordinator(store, source, tmp_path, encoder)
        app = DiagnosisApplicationService.default_fake()
        app.retrieval_coordinator = app.retrieval_tools = coordinator
        assert coordinator.state == 'new' and encoder.calls == 0
        with TestClient(create_app(app)) as client:
            wait_ready(coordinator)
            search = coordinator.for_incident('incident-a')[0]
            result = search.handler({'query': 'Service', 'mode': 'hybrid'})
            assert result.result['channel_statuses'] == {'keyword': 'ready', 'vector': 'ready'}
            assert len(result.result['hits']) == expected
            assert encoder.calls == (expected if attempt == 0 else 0)
            assert encoder.queries == 1
            assert client.get('/api/retrieval/status').json()['state'] == 'running'
        assert encoder.closed
        assert coordinator.health() == {'state': 'stopped', 'ticks': coordinator.ticks,
            'projection_errors': 0, 'thread_alive': False, 'runs': {'ready': 1}}
        with pytest.raises(DomainError, match='not_running'):
            search.handler({'query': 'after close'})
    assert len(store.database.query('SELECT * FROM code_embedding_tasks')) == expected
    assert len(store.database.query('SELECT * FROM code_embedding_attempts')) == expected


def test_allowlist_does_not_project_other_repositories(tmp_path):
    store, _, _, _, source = setup_source(tmp_path)
    encoder = Encoder()
    coordinator = make_coordinator(store, source, tmp_path, encoder, ('other',))
    coordinator.start()
    try:
        coordinator.tick()
        assert coordinator.runs == {}
        assert encoder.calls == 0
        with pytest.raises(DomainError, match='unbound'):
            coordinator.for_incident('incident-a')[0].handler({'query': 'secret'})
        assert encoder.queries == 0
    finally:
        coordinator.stop()


def test_disabled_config_does_not_construct_clients(monkeypatch):
    monkeypatch.delenv('ANTISENTINEL_CODE_RETRIEVAL_ENABLED', raising=False)
    assert coordinator_from_environment(None) is None


def test_enabled_config_requires_allowlist_before_clients(monkeypatch):
    monkeypatch.setenv('ANTISENTINEL_CODE_RETRIEVAL_ENABLED', 'true')
    monkeypatch.delenv('ANTISENTINEL_CODE_RETRIEVAL_REPOSITORIES', raising=False)
    with pytest.raises(ValueError, match='allowlist'):
        coordinator_from_environment(None)


def test_projection_corruption_is_quarantined_without_upload(tmp_path):
    store, _, _, _, source = setup_source(tmp_path)
    with store.database.transaction() as connection:
        connection.execute("UPDATE code_map_blobs SET content=?", (b'corrupt',))
    encoder = Encoder()
    coordinator = make_coordinator(store, source, tmp_path, encoder)
    coordinator.start()
    try:
        coordinator.tick()
        coordinator.tick()
        assert len(coordinator.errors) == 1
        assert encoder.calls == 0
        assert coordinator.runs == {}
    finally:
        coordinator.stop()


def test_startup_failure_closes_already_opened_encoder(tmp_path):
    store, _, _, _, source = setup_source(tmp_path)
    encoder = Encoder()
    def failing_factory(_):
        raise ConnectionError('unavailable')
    coordinator = RetrievalCoordinator(store, source, allowed_repositories=['repo-a'],
        embedder_factory=lambda: encoder, index_factory=failing_factory)
    with pytest.raises(ConnectionError):
        coordinator.start()
    assert encoder.closed and coordinator.state == 'failed'
    assert not coordinator.health()['thread_alive']


def test_lifecycle_case_runner(tmp_path):
    from scripts.run_retrieval_coordinator_case import run
    report = run(tmp_path / 'case')
    assert report['case_pass']
    assert report['embedding_documents_per_start'] == [2, 0]
    assert report['background_exceptions'] == 0


def test_stop_during_failure_does_not_hide_scheduler_exception(tmp_path):
    store, _, _, _, source = setup_source(tmp_path)
    encoder = Encoder()
    coordinator = make_coordinator(store, source, tmp_path, encoder)
    entered, release = Event(), Event()
    def failing_tick():
        entered.set()
        assert release.wait(5)
        raise RuntimeError('real failure during shutdown')
    coordinator.tick = failing_tick
    coordinator.start()
    assert entered.wait(5)
    try:
        with pytest.raises(TimeoutError):
            coordinator.stop(timeout=.01)
        assert coordinator.health()['state'] == 'stopping'
        assert not encoder.closed
    finally:
        release.set()
        coordinator.thread.join(5)
    assert coordinator.health()['state'] == 'failed'
    assert coordinator.errors['scheduler'] == 'RuntimeError'
    assert encoder.closed


def test_close_failure_cannot_report_clean_stop(tmp_path):
    store, _, _, _, source = setup_source(tmp_path)
    class BrokenClose(Encoder):
        def close(self):
            raise OSError('close failed')
    coordinator = make_coordinator(store, source, tmp_path, BrokenClose(), ('other',))
    coordinator.start()
    with pytest.raises(RuntimeError, match='failed'):
        coordinator.stop()
    assert coordinator.health()['state'] == 'failed'
    assert coordinator.errors['close_embedder'] == 'OSError'


def test_stop_signal_prevents_next_projection(tmp_path):
    store, scope, _, _, source = setup_source(tmp_path)
    encoder = Encoder()
    coordinator = make_coordinator(store, source, tmp_path, encoder)
    # Exercise a scheduler round deterministically, without racing the background loop.
    coordinator.state = 'running'
    coordinator.queue = SimpleNamespace(run=lambda _: {'status': 'blocked'})
    rows = [scope, {**scope, 'snapshot_id': 'second'}]
    store.database.query = lambda *args: rows
    projected = []
    def project(selected):
        projected.append(selected.snapshot_id)
        coordinator.stopping.set()
        return 'run'
    coordinator._project = project
    coordinator.tick()
    assert projected == [scope['snapshot_id']]


def test_shutdown_probe_recovers_remaining_task_without_duplicate_encoding(tmp_path):
    from scripts.run_retrieval_coordinator_case import run
    report = run(tmp_path / 'shutdown', shutdown_probe=True)
    assert report['case_pass']
    assert report['shutdown_probe_encoded'] == 1
    assert report['embedding_documents_per_start'] == [1, 0]


@pytest.mark.parametrize('timeout', [-1, float('inf'), float('nan'), True, None])
def test_invalid_stop_timeout_does_not_change_state(tmp_path, timeout):
    store, _, _, _, source = setup_source(tmp_path)
    coordinator = make_coordinator(store, source, tmp_path, Encoder())
    with pytest.raises(ValueError, match='timeout'):
        coordinator.stop(timeout)
    assert coordinator.state == 'new' and not coordinator.stopping.is_set()
