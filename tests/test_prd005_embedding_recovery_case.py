import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_embedding_worker_case import CrashAfterFirstUpsert
from scripts.prd005_embedding_recovery import run_real_recovery
from antisentinel.retrieval.embedding_jobs import LeaseLost
from tests.test_embedding_worker import setup_queue


def test_crash_only_after_successful_write_and_durable_marker(tmp_path):
    written = []
    class Crash(BaseException):
        pass
    def crash(code):
        assert code == 86
        assert json.loads((tmp_path / 'marker.json').read_text()) == {'point_ids': ['point'], 'upserted': 1}
        raise Crash()
    def upsert(points):
        written.extend(points)
        return len(points)
    point = SimpleNamespace(point_id='point')
    index = CrashAfterFirstUpsert(SimpleNamespace(upsert=upsert), tmp_path / 'marker.json', crash)
    with pytest.raises(Crash):
        index.upsert([point])
    assert written == [point]


def test_incomplete_upsert_is_not_accepted_as_crash_point(tmp_path):
    index = CrashAfterFirstUpsert(SimpleNamespace(upsert=lambda _: 0), tmp_path / 'marker.json',
                                  lambda _: pytest.fail('incomplete upsert must not exit'))
    with pytest.raises(ConnectionError):
        index.upsert([SimpleNamespace(point_id='point')])
    assert not (tmp_path / 'marker.json').exists()


def test_lost_lease_cannot_cache_fail_renew_or_complete(tmp_path):
    _, queue, run_id, _, _ = setup_queue(tmp_path)
    old = queue.claim(run_id, 'same-owner', now=0, lease_seconds=1)
    current = queue.claim(run_id, 'same-owner', now=2, lease_seconds=10)
    for operation in (
        lambda: queue.cache_vector(old, [1., 0., 0.], now=3),
        lambda: queue.fail(old, 'stale', now=3),
        lambda: queue.renew(old, now=3),
        lambda: queue.complete(old, now=3),
    ):
        with pytest.raises(LeaseLost):
            operation()
    assert queue.task_rows(run_id)[0] == current


def test_real_process_exit_recovers_from_cache_with_one_logical_point(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = run_real_recovery(root, root / 'tests/fixtures/code_retrieval/v1/manifest.json',
                              tmp_path / 'r2', str(tmp_path / 'vectors.db'), 'q01', local_embedding=True)
    assert report['case_pass'], report
    assert report['counts']['tasks'] == report['counts']['vectors'] == 1
    assert report['counts']['attempts'] == 2
    assert report['embedding_inputs_per_process'] == [1, 0]
    assert report['injected_process_exits'] == 1
    assert report['child_exit_code'] == 86
    assert report['retries'] == 1
    assert report['external_model_calls'] == 0
    assert report['counts']['evidence'] == 0
