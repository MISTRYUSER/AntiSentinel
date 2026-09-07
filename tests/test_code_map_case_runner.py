from pathlib import Path

import pytest


def test_runner_accepts_new_child_but_rejects_existing_output(tmp_path):
    from scripts.run_code_map_case import CaseRunner

    parent = tmp_path / "cases"
    parent.mkdir()
    new_output = parent / "scheduler-1"
    runner = CaseRunner(new_output, timeout_s=120)
    runner.prepare()
    assert (new_output / ".antisentinel-case").exists()
    with pytest.raises(FileExistsError):
        CaseRunner(new_output, timeout_s=120).prepare()


def test_case_report_requires_persistence_and_trace_before_pass(tmp_path):
    from scripts.run_code_map_case import empty_case_report, finalize_case

    report = empty_case_report(tmp_path)
    report["business_completed"] = True
    report["persistence_readback"] = False
    report["trace_flushed"] = True
    assert finalize_case(report)["case_pass"] is False


def test_empty_report_has_all_numeric_case_categories_and_starts_pending(tmp_path):
    from scripts.run_code_map_case import REQUIRED_CASE_FIELDS, empty_case_report

    report = empty_case_report(tmp_path)
    assert report["case_pass"] is None
    assert REQUIRED_CASE_FIELDS <= report.keys()
    for field in ("input_files", "input_bytes", "output_nodes", "output_edges", "output_chunks", "persisted_nodes", "persisted_edges", "persisted_chunks", "integrity_checked", "background_exception_count", "t1", "t2", "persistence_lag_ms"):
        assert field in report
        assert report[field] is None


def test_runner_exposes_staged_scheduler_case_entrypoint():
    from scripts.run_code_map_case import CaseRunner

    assert callable(CaseRunner.run_scheduler_case)


def test_runner_exposes_daemon_git_case_entrypoint():
    from scripts.run_code_map_case import CaseRunner

    assert callable(CaseRunner.run_daemon_case)


def test_runner_exposes_snapshot_case_entrypoint():
    from scripts.run_code_map_case import CaseRunner

    assert callable(CaseRunner.run_snapshot_case)


def test_runner_exposes_loop_case_entrypoint():
    from scripts.run_code_map_case import CaseRunner

    assert callable(CaseRunner.run_loop_case)


def test_daemon_start_probe_returns_after_process_is_observed_alive(tmp_path):
    import time
    from scripts.run_code_map_case import CaseRunner

    class AliveProcess:
        def poll(self):
            return None

    started = time.monotonic()
    CaseRunner._wait_for_process_start(AliveProcess(), timeout_s=1.0)
    assert time.monotonic() - started < 0.5


def test_finalize_case_passes_only_with_complete_persistent_report(tmp_path):
    from scripts.run_code_map_case import empty_case_report, finalize_case

    report = empty_case_report(tmp_path)
    report.update({
        "business_completed": True, "persistence_readback": True, "trace_flushed": True,
        "input_files": 1, "input_bytes": 10, "output_nodes": 1, "output_edges": 1, "output_chunks": 1,
        "persisted_nodes": 1, "persisted_edges": 1, "persisted_chunks": 1,
        "integrity_checked": 3, "integrity_failed": 0, "background_exception_count": 0, "t1": 1.0, "t2": 1.1,
        "persistence_lag_ms": 100.0,
    })
    assert finalize_case(report)["case_pass"] is True
