from types import SimpleNamespace
from scripts import preflight_standalone as module


def test_low_disk_stops_before_docker_access(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1024**3))
    def forbidden(*args, **kwargs):
        raise AssertionError('must not touch Docker')
    monkeypatch.setattr(module.subprocess, 'run', forbidden)
    result = module.inspect_environment(tmp_path)
    assert not result['ready'] and result['errors'] == ['insufficient_host_disk_headroom']


def test_missing_engine_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=20*1024**3))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    assert module.inspect_environment(tmp_path)['errors'] == ['docker_engine_unavailable']


def test_local_readiness_does_not_claim_production_capacity(monkeypatch, tmp_path):
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=20*1024**3))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"NCPU":8,"MemTotal":8321994752}'))
    result = module.inspect_environment(tmp_path)
    assert result['ready'] and not result['production_capacity_verified']
