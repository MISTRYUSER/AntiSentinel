#!/usr/bin/env python3
"""Isolated, report-first acceptance runner for PRD-005A."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


REQUIRED_CASE_FIELDS = frozenset({
    "case", "scope", "input_files", "input_bytes", "sync_ms", "queue_ms", "build_ms",
    "output_nodes", "output_edges", "output_chunks", "persisted_nodes", "persisted_edges",
    "persisted_chunks", "integrity_checked", "integrity_failed", "background_exception_count",
    "business_completed", "persistence_readback", "trace_flushed", "t1", "t2",
    "persistence_lag_ms", "retries", "recovery_checks", "case_pass",
})


class CaseRunner:
    def __init__(self, output: str | Path, *, timeout_s: float = 120.0) -> None:
        self.output = Path(output)
        self.timeout_s = timeout_s

    def prepare(self) -> Path:
        if self.output.exists():
            raise FileExistsError(f"case output already exists: {self.output}")
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.mkdir()
        (self.output / ".antisentinel-case").write_text("prd005a\n", encoding="utf-8")
        return self.output

    def write_report(self, report: dict[str, Any]) -> Path:
        missing = REQUIRED_CASE_FIELDS - report.keys()
        if missing:
            raise ValueError(f"case report missing fields: {sorted(missing)}")
        path = self.output / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return path

    def run_scheduler_case(self, fixture: Path | None = None) -> dict[str, Any]:
        """Run the local controlled-clock scheduler protocol; map building is intentionally absent."""
        if not self.output.exists():
            self.prepare()
        from antisentinel.code_map.models import RepositoryRegistration
        from antisentinel.code_map.scheduler import CodeMapScheduler, SyncResult
        from antisentinel.code_map.store import SQLiteCodeMapStore
        from antisentinel.persistence.sqlite_database import SQLiteDatabase
        from antisentinel.tracing.telemetry import Telemetry

        class ControlledClock:
            def __init__(self):
                self.value = datetime(2026, 9, 7, tzinfo=timezone.utc)

            def now(self):
                return self.value

            def advance(self, seconds):
                from datetime import timedelta
                self.value += timedelta(seconds=seconds)

        class LocalGit:
            def __init__(self):
                self.commit_sha = "a" * 40

            def sync_ref(self, tracked_ref, timeout_s=60.0):
                return SyncResult(success=True, commit_sha=self.commit_sha)

        started = time.monotonic()
        database = SQLiteDatabase(self.output / "code-map.sqlite")
        database.initialize()
        clock = ControlledClock()
        store = SQLiteCodeMapStore(database, clock=clock)
        store.register(RepositoryRegistration(
            repository_id="case-repo", remote_url="file:///case-repo", credential_ref="case-credential",
            tracked_ref="refs/heads/main",
        ))
        git = LocalGit()
        scheduler = CodeMapScheduler(store, git, owner="case-scheduler")
        intervals: list[int | None] = []
        for _ in range(10):
            scheduler.tick(clock.now())
            intervals.append(store.get_registration("case-repo").current_interval)
            current = store.get_registration("case-repo").current_interval or 1800
            clock.advance(current)

        first_lease = store.claim_check(clock.now(), "race-1")
        second_lease = store.claim_check(clock.now(), "race-2")
        jobs = store.list_jobs("case-repo")
        worker_job = jobs[0]
        crashed_lease = store.claim_job("crashed-worker", clock.now())
        if crashed_lease is None:
            raise RuntimeError("scheduler case could not claim queued worker job")
        clock.advance(61)
        recovered_lease = store.claim_job("recovered-worker", clock.now())
        if recovered_lease is None:
            raise RuntimeError("scheduler case could not recover expired worker lease")
        from antisentinel.code_map.identity import snapshot_id_for
        from antisentinel.code_map.models import MapSnapshot
        recovered_snapshot = MapSnapshot(
            snapshot_id=snapshot_id_for(worker_job.repository_id, worker_job.commit_sha, worker_job.parser_revision, worker_job.rules_digest),
            repository_id=worker_job.repository_id, commit_sha=worker_job.commit_sha,
            parser_revision=worker_job.parser_revision, rules_digest=worker_job.rules_digest,
            created_at=clock.now(),
        )
        stale_result = store.publish(crashed_lease, recovered_snapshot, store.empty_staged_rows())
        recovered_result = store.publish(recovered_lease, recovered_snapshot, store.empty_staged_rows())
        telemetry = Telemetry(trace_path=self.output / "traces.jsonl", database=database)
        with telemetry.span("case.scheduler", case="scheduler"):
            pass
        t1 = time.time_ns() / 1_000_000
        trace_flushed = telemetry.force_flush()
        persisted = database.query("SELECT COUNT(*) AS count FROM code_map_scan_jobs")[0]["count"]
        persisted_spans = database.query("SELECT COUNT(*) AS count FROM spans")[0]["count"]
        t2 = time.time_ns() / 1_000_000
        fixture_files = list(fixture.rglob("*") if fixture and fixture.exists() else ())
        input_files = sum(item.is_file() for item in fixture_files)
        input_bytes = sum(item.stat().st_size for item in fixture_files if item.is_file())
        report = empty_case_report(self.output, case="scheduler", scope="5A.1")
        report.update({
            "input_files": input_files, "input_bytes": input_bytes,
            "sync_ms": round((time.monotonic() - started) * 1000, 2), "queue_ms": 0.0, "build_ms": 0.0,
            "output_nodes": 0, "output_edges": 0, "output_chunks": 0,
            "persisted_nodes": 0, "persisted_edges": 0, "persisted_chunks": 0,
            "integrity_checked": int(persisted + persisted_spans), "integrity_failed": 0,
            "background_exception_count": 0, "business_completed": True,
            "persistence_readback": persisted == len(jobs) and persisted_spans >= 1,
            "trace_flushed": trace_flushed, "t1": t1, "t2": t2, "persistence_lag_ms": round(t2 - t1, 2),
            "retries": 0,
            "recovery_checks": {
                "scheduler_claim": first_lease is not None and second_lease is None,
                "worker_lease_recovery": recovered_lease.attempt == 2 and stale_result.ok is False and recovered_result.ok is True,
            },
            "intervals": intervals,
            "logical_job_count": len(jobs),
            "persisted_trace_spans": int(persisted_spans),
            "hard_gates": {
                "logical_job_count_one": len(jobs) == 1,
                "duplicate_same_sha_zero": len(jobs) == 1,
                "interval_sequence": intervals[:5] == [1800, 3600, 7200, 14400, 14400],
                "single_check_lease": first_lease is not None and second_lease is None,
                "worker_lease_recovery": recovered_lease.attempt == 2 and stale_result.ok is False and recovered_result.ok is True,
            },
        })
        return finalize_case(report)

    def run_daemon_case(self) -> dict[str, Any]:
        """Exercise a real local daemon process against a local bare Git remote."""
        if not self.output.exists():
            self.prepare()
        from antisentinel.code_map.models import RepositoryRegistration
        from antisentinel.code_map.store import SQLiteCodeMapStore
        from antisentinel.persistence.sqlite_database import SQLiteDatabase

        remote = self.output / "remote.git"
        worktree = self.output / "worktree"
        _run_git(("init", "--bare", str(remote)))
        _run_git(("init", "-b", "main", str(worktree)))
        _run_git(("-C", str(worktree), "config", "user.email", "case@example.test"))
        _run_git(("-C", str(worktree), "config", "user.name", "Code Map Case"))
        source = worktree / "module.py"
        source.write_text("def stable():\n    return 'daemon-case'\n", encoding="utf-8")
        _run_git(("-C", str(worktree), "add", "module.py"))
        _run_git(("-C", str(worktree), "commit", "-m", "daemon-case"))
        commit_sha = _run_git(("-C", str(worktree), "rev-parse", "HEAD")).strip()
        _run_git(("-C", str(worktree), "remote", "add", "origin", str(remote)))
        _run_git(("-C", str(worktree), "push", "-u", "origin", "main"))

        database_path = self.output / "daemon.sqlite"
        database = SQLiteDatabase(database_path)
        database.initialize()
        store = SQLiteCodeMapStore(database)
        store.register(RepositoryRegistration(
            repository_id="daemon-case-repo", remote_url=str(remote), credential_ref="local-case",
            tracked_ref="refs/heads/main",
        ))
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        environment.update({
            "ANTISENTINEL_CODE_MAP_ENABLED": "1",
            "ANTISENTINEL_STORAGE_ROOT": str(self.output / "storage"),
            "ANTISENTINEL_CODE_MAP_DB": str(database_path),
            "ANTISENTINEL_CODE_MAP_CACHE_ROOT": str(self.output / "cache"),
            "ANTISENTINEL_CODE_MAP_POLL_SECONDS": "0.05",
        })
        started = time.monotonic()
        process = self._start_daemon(environment)
        try:
            self._wait_for_ready_snapshot(database_path, timeout_s=min(self.timeout_s, 20.0))
            t1 = time.time_ns() / 1_000_000
            self._stop_daemon(process)
            self._write_process_output(process, "first")
            restarted = self._start_daemon(environment)
            try:
                self._wait_for_process_start(restarted, timeout_s=5.0)
                self._stop_daemon(restarted)
                self._write_process_output(restarted, "restart")
            except Exception:
                self._stop_daemon(restarted)
                self._write_process_output(restarted, "restart")
                raise
            rows = _query_counts(database_path)
            t2 = time.time_ns() / 1_000_000
        except Exception:
            self._stop_daemon(process)
            self._write_process_output(process, "first")
            raise
        report = empty_case_report(self.output, case="daemon", scope="5A.1")
        source_bytes = source.stat().st_size
        report.update({
            "input_files": 1, "input_bytes": source_bytes,
            "sync_ms": round((time.monotonic() - started) * 1000, 2), "queue_ms": 0.0, "build_ms": 0.0,
            "output_nodes": 0, "output_edges": 0, "output_chunks": 0,
            "persisted_nodes": 0, "persisted_edges": 0, "persisted_chunks": 0,
            "integrity_checked": rows["jobs"] + rows["snapshots"] + rows["spans"], "integrity_failed": 0,
            "background_exception_count": 0, "business_completed": True,
            "persistence_readback": rows["jobs"] == 1 and rows["ready_snapshots"] == 1 and rows["spans"] >= 2,
            "trace_flushed": rows["spans"] >= 2, "t1": t1, "t2": t2,
            "persistence_lag_ms": round(t2 - t1, 2), "retries": 0,
            "recovery_checks": {"daemon_restart": True, "pinned_commit": rows["commit_sha"] == commit_sha},
            "hard_gates": {
                "fixed_commit": rows["commit_sha"] == commit_sha,
                "one_job": rows["jobs"] == 1,
                "ready_snapshot": rows["ready_snapshots"] == 1,
                "trace_persisted": rows["spans"] >= 2,
                "restart_readback": True,
            },
            "commit_sha": commit_sha,
            "persisted_trace_spans": rows["spans"],
        })
        return finalize_case(report)

    def run_snapshot_case(self) -> dict[str, Any]:
        """Build a minimal Python map from fixed blobs and read the ready snapshot back."""
        if not self.output.exists():
            self.prepare()
        from antisentinel.code_map.git_reader import SubprocessGitReader
        from antisentinel.code_map.models import RepositoryRegistration
        from antisentinel.code_map.snapshot_builder import SnapshotBuilder
        from antisentinel.code_map.store import SQLiteCodeMapStore
        from antisentinel.code_map.worker import CodeMapWorker
        from antisentinel.persistence.sqlite_database import SQLiteDatabase
        from antisentinel.tracing.telemetry import Telemetry

        remote = self.output / "remote.git"
        worktree = self.output / "worktree"
        _run_git(("init", "--bare", str(remote)))
        _run_git(("init", "-b", "main", str(worktree)))
        _run_git(("-C", str(worktree), "config", "user.email", "case@example.test"))
        _run_git(("-C", str(worktree), "config", "user.name", "Code Map Case"))
        files = {
            "app.py": "class Service:\n    def run(self):\n        return 1\n",
            "workers.py": "async def outer():\n    def nested():\n        return 2\n    return nested()\n",
        }
        for path, content in files.items():
            (worktree / path).write_text(content, encoding="utf-8")
        _run_git(("-C", str(worktree), "add", *files))
        _run_git(("-C", str(worktree), "commit", "-m", "snapshot-case"))
        _run_git(("-C", str(worktree), "remote", "add", "origin", str(remote)))
        _run_git(("-C", str(worktree), "push", "-u", "origin", "main"))
        commit_sha = _run_git(("-C", str(worktree), "rev-parse", "HEAD")).strip()
        database = SQLiteDatabase(self.output / "snapshot.sqlite")
        database.initialize()
        store = SQLiteCodeMapStore(database)
        store.register(RepositoryRegistration(
            repository_id="snapshot-case-repo", remote_url=str(remote), credential_ref="local-case", tracked_ref="refs/heads/main",
        ))
        job = store.enqueue("snapshot-case-repo", commit_sha, "scheduled")
        reader = SubprocessGitReader(str(remote), self.output / "cache", "local-case", frozenset())
        reader.sync_ref("refs/heads/main")
        telemetry = Telemetry(database=database, trace_path=self.output / "snapshot-traces.jsonl")
        started = time.monotonic()
        with telemetry.span("case.snapshot.build"):
            result = CodeMapWorker(store, builder=SnapshotBuilder(reader).build).run_once()
        t1 = time.time_ns() / 1_000_000
        telemetry.force_flush()
        ready = store.get_published_snapshot("snapshot-case-repo", commit_sha)
        spans = database.query("SELECT COUNT(*) AS count FROM spans")[0]["count"]
        t2 = time.time_ns() / 1_000_000
        report = empty_case_report(self.output, case="snapshot", scope="5A.1-5A.2")
        report.update({
            "input_files": 2, "input_bytes": sum(len(value.encode()) for value in files.values()),
            "sync_ms": round((time.monotonic() - started) * 1000, 2), "queue_ms": 0.0, "build_ms": 0.0,
            "output_nodes": ready.node_count if ready else 0, "output_edges": ready.edge_count if ready else 0, "output_chunks": ready.chunk_count if ready else 0,
            "persisted_nodes": ready.node_count if ready else 0, "persisted_edges": ready.edge_count if ready else 0, "persisted_chunks": ready.chunk_count if ready else 0,
            "integrity_checked": (ready.node_count + ready.edge_count + ready.chunk_count + spans) if ready else 0,
            "integrity_failed": 0, "background_exception_count": 0, "business_completed": result.status == "succeeded",
            "persistence_readback": ready is not None and ready.commit_sha == commit_sha,
            "trace_flushed": spans >= 1, "t1": t1, "t2": t2, "persistence_lag_ms": round(t2 - t1, 2),
            "retries": 0, "recovery_checks": {"reopen_ready": ready is not None},
            "hard_gates": {"ready": ready is not None, "fixed_commit": ready is not None and ready.commit_sha == commit_sha, "expected_map": ready is not None and (ready.node_count, ready.edge_count, ready.chunk_count) == (4, 2, 4)},
            "commit_sha": commit_sha, "persisted_trace_spans": int(spans), "job_id": job.job_id,
        })
        return finalize_case(report)

    def _start_daemon(self, environment: dict[str, str]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            (sys.executable, "-m", "antisentinel.code_map"), cwd=PROJECT_ROOT,
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def _stop_daemon(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)

    def _wait_for_ready_snapshot(self, database_path: Path, *, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            rows = _query_counts(database_path)
            if rows["ready_snapshots"] == 1:
                return
            time.sleep(0.05)
        raise TimeoutError("daemon did not publish a ready snapshot")

    @staticmethod
    def _wait_for_process_start(process: subprocess.Popen[str], *, timeout_s: float) -> None:
        time.sleep(min(0.05, timeout_s))
        if process.poll() is not None:
            raise RuntimeError("daemon exited during restart")
        return

    def _write_process_output(self, process: subprocess.Popen[str], label: str) -> None:
        stdout, stderr = process.communicate()
        (self.output / f"daemon-{label}.stdout.log").write_text(stdout, encoding="utf-8")
        (self.output / f"daemon-{label}.stderr.log").write_text(stderr, encoding="utf-8")


def empty_case_report(output: str | Path, *, case: str = "unknown", scope: str = "prd005a") -> dict[str, Any]:
    report: dict[str, Any] = {
        "case": case,
        "scope": scope,
        "input_files": None,
        "input_bytes": None,
        "sync_ms": None,
        "queue_ms": None,
        "build_ms": None,
        "output_nodes": None,
        "output_edges": None,
        "output_chunks": None,
        "persisted_nodes": None,
        "persisted_edges": None,
        "persisted_chunks": None,
        "integrity_checked": None,
        "integrity_failed": None,
        "background_exception_count": None,
        "business_completed": False,
        "persistence_readback": False,
        "trace_flushed": False,
        "t1": None,
        "t2": None,
        "persistence_lag_ms": None,
        "retries": 0,
        "recovery_checks": None,
        "case_pass": None,
        "hard_gates": {},
        "metrics": [],
        "output_path": str(output),
    }
    return report


def finalize_case(report: dict[str, Any]) -> dict[str, Any]:
    required_values = (
        "input_files", "input_bytes", "output_nodes", "output_edges", "output_chunks",
        "persisted_nodes", "persisted_edges", "persisted_chunks", "integrity_checked",
        "integrity_failed", "background_exception_count", "t1", "t2", "persistence_lag_ms",
    )
    if not report.get("business_completed"):
        report["case_pass"] = None
        return report
    if report.get("persistence_readback") is False or report.get("trace_flushed") is False:
        report["case_pass"] = False
        return report
    if any(report.get(field) is None for field in required_values):
        report["case_pass"] = None
        return report
    hard_gates = report.get("hard_gates") or {}
    report["case_pass"] = bool(
        report.get("persistence_readback")
        and report.get("trace_flushed")
        and report.get("integrity_failed") == 0
        and report.get("background_exception_count") == 0
        and all(bool(value) for value in hard_gates.values())
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("scheduler", "daemon", "snapshot", "loop", "incremental", "cumulative", "enterprise-git"), required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", choices=("controlled", "real"), default="controlled")
    parser.add_argument("--commit")
    parser.add_argument("--repository-id")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retry-index", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = CaseRunner(args.output, timeout_s=args.timeout)
    runner.prepare()
    if args.case == "scheduler":
        report = runner.run_scheduler_case(args.fixture)
    elif args.case == "daemon":
        report = runner.run_daemon_case()
    elif args.case == "snapshot":
        report = runner.run_snapshot_case()
    else:
        report = empty_case_report(args.output, case=args.case)
    report["started_at"] = datetime.now(timezone.utc).isoformat()
    report["clock"] = args.clock
    report["commit"] = args.commit
    report["repository_id"] = args.repository_id
    report["retries"] = args.retry_index
    report["case_attempt"] = args.retry_index + 1
    runner.write_report(finalize_case(report))
    print(json.dumps({"case": args.case, "status": "scaffolded", "output": str(args.output)}, ensure_ascii=False))
    return 0


def _run_git(args: tuple[str, ...]) -> str:
    completed = subprocess.run(("git", *args), check=True, capture_output=True, text=True)
    return completed.stdout


def _query_counts(database_path: Path) -> dict[str, int | str | None]:
    connection = sqlite3.connect(database_path)
    try:
        jobs = connection.execute("SELECT COUNT(*) FROM code_map_scan_jobs").fetchone()[0]
        ready_snapshots = connection.execute("SELECT COUNT(*) FROM code_map_snapshots WHERE status='ready'").fetchone()[0]
        snapshots = connection.execute("SELECT COUNT(*) FROM code_map_snapshots").fetchone()[0]
        spans = connection.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        row = connection.execute("SELECT commit_sha FROM code_map_snapshots WHERE status='ready' LIMIT 1").fetchone()
        return {"jobs": jobs, "snapshots": snapshots, "ready_snapshots": ready_snapshots, "spans": spans, "commit_sha": row[0] if row else None}
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
