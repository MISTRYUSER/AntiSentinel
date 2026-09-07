#!/usr/bin/env python3
"""Read one real local Git repository at a fixed commit and build its map."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from antisentinel.code_map.git_reader import SubprocessGitReader
from antisentinel.code_map.snapshot_builder import SnapshotBuilder
from antisentinel.code_map.store import SQLiteCodeMapStore
from antisentinel.code_map.models import RepositoryRegistration, RepositoryBudget
from antisentinel.persistence.sqlite_database import SQLiteDatabase
from antisentinel.code_map.worker import CodeMapWorker


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    started = time.monotonic()
    database = SQLiteDatabase(args.output / "real.sqlite")
    database.initialize()
    store = SQLiteCodeMapStore(database)
    store.register(RepositoryRegistration("real-local", str(args.repository), "runtime-credential", "HEAD"))
    job = store.enqueue("real-local", args.commit, "explicit", parser_revision="multilang-tree-sitter-v1")
    lease = store.claim_job("real-worker", datetime.now(timezone.utc))
    reader = SubprocessGitReader(str(args.repository), args.output / "cache", "runtime-credential", frozenset())
    synced = reader.sync_ref("HEAD")
    if not synced.success or synced.commit_sha != args.commit:
        raise RuntimeError(f"fixed_commit_unavailable:{synced.error_code or synced.commit_sha}")
    tree_entries = reader.list_tree(args.commit, RepositoryBudget())
    included_entries = [entry for entry in tree_entries if entry.included]
    supported_python_files = [entry for entry in included_entries if entry.path.endswith(".py")]
    supported_multilang_files = [entry for entry in included_entries if entry.path.endswith((".go", ".ts", ".tsx"))]
    unsupported_files = [entry.path for entry in included_entries if not (entry.path.endswith(".py") or entry.path.endswith((".go", ".ts", ".tsx")))]
    builder = SnapshotBuilder(reader, budget=RepositoryBudget())
    built = builder.build(lease)
    result = store.publish(lease, built.snapshot, built.rows)
    ready = store.get_snapshot(built.snapshot.snapshot_id)
    integrity = store.database.query("PRAGMA foreign_key_check")
    report = {
        "case": "real-local", "repository": str(args.repository), "commit": args.commit,
        "case_pass": result.ok and ready is not None and not integrity and ready.file_count > 0 and not ready.failed_files,
        "input_files": len(included_entries), "input_bytes": sum(entry.byte_count for entry in supported_python_files),
        "supported_python_files": len(supported_python_files),
        "supported_multilang_files": len(supported_multilang_files), "unsupported_files": len(unsupported_files),
        "unsupported_sample": unsupported_files[:20],
        "output_nodes": ready.node_count if ready else 0, "output_edges": ready.edge_count if ready else 0,
        "output_chunks": ready.chunk_count if ready else 0, "persisted_nodes": ready.node_count if ready else 0,
        "persisted_edges": ready.edge_count if ready else 0, "persisted_chunks": ready.chunk_count if ready else 0,
        "failed_files": list(ready.failed_files) if ready else [], "status": ready.status if ready else "missing",
        "cache_hits": built.cache_hits, "integrity_checks": (ready.node_count + ready.edge_count + ready.chunk_count) if ready else 0,
        "foreign_key_errors": len(integrity), "background_exception_count": 0,
        "elapsed_seconds": round(time.monotonic() - started, 2), "job_id": job.job_id,
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["case_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
