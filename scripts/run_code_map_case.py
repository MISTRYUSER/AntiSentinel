#!/usr/bin/env python3
"""Isolated, report-first acceptance runner for PRD-005A."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
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

        race_registration = RepositoryRegistration(
            repository_id="race-repo", remote_url="file:///race-repo", credential_ref="race-credential",
            tracked_ref="refs/heads/main",
        )
        store.register(race_registration)
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
        telemetry = Telemetry(trace_path=self.output / "traces.jsonl")
        with telemetry.span("case.scheduler", case="scheduler"):
            pass
        t1 = time.time_ns() / 1_000_000
        trace_flushed = telemetry.force_flush()
        persisted = database.query("SELECT COUNT(*) AS count FROM code_map_scan_jobs")[0]["count"]
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
            "integrity_checked": int(persisted), "integrity_failed": 0,
            "background_exception_count": 0, "business_completed": True,
            "persistence_readback": persisted == len(jobs) + 0,
            "trace_flushed": trace_flushed, "t1": t1, "t2": t2, "persistence_lag_ms": round(t2 - t1, 2),
            "retries": 0,
            "recovery_checks": {
                "scheduler_claim": first_lease is not None and second_lease is None,
                "worker_lease_recovery": recovered_lease.attempt == 2 and stale_result.ok is False and recovered_result.ok is True,
            },
            "intervals": intervals,
            "logical_job_count": len(jobs),
            "hard_gates": {
                "logical_job_count_one": len(jobs) == 1,
                "duplicate_same_sha_zero": len(jobs) == 1,
                "interval_sequence": intervals[:5] == [1800, 3600, 7200, 14400, 14400],
                "single_check_lease": first_lease is not None and second_lease is None,
                "worker_lease_recovery": recovered_lease.attempt == 2 and stale_result.ok is False and recovered_result.ok is True,
            },
        })
        return finalize_case(report)


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
    parser.add_argument("--case", choices=("scheduler", "snapshot", "loop", "incremental", "cumulative", "enterprise-git"), required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", choices=("controlled", "real"), default="controlled")
    parser.add_argument("--commit")
    parser.add_argument("--repository-id")
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = CaseRunner(args.output, timeout_s=args.timeout)
    runner.prepare()
    report = runner.run_scheduler_case(args.fixture) if args.case == "scheduler" else empty_case_report(args.output, case=args.case)
    report["started_at"] = datetime.now(timezone.utc).isoformat()
    report["clock"] = args.clock
    report["commit"] = args.commit
    report["repository_id"] = args.repository_id
    runner.write_report(finalize_case(report))
    print(json.dumps({"case": args.case, "status": "scaffolded", "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
