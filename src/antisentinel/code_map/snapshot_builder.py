"""Build the minimum Python code-map projection from one fixed Git commit."""

from __future__ import annotations

from dataclasses import dataclass

from .identity import snapshot_id_for
from .models import MapSnapshot, RepositoryBudget
from .python_parser import PythonAstParser
from .store import JobLease, StagedMapRows


@dataclass(frozen=True)
class BuildOutput:
    snapshot: MapSnapshot
    rows: StagedMapRows


class SnapshotBuilder:
    def __init__(self, reader, *, parser: PythonAstParser | None = None, budget: RepositoryBudget | None = None) -> None:
        self.reader = reader
        self.parser = parser or PythonAstParser("python-3.13/ast-v1")
        self.budget = budget or RepositoryBudget()

    def build(self, lease: JobLease) -> BuildOutput:
        snapshot_id = snapshot_id_for(lease.repository_id, lease.commit_sha, lease.parser_revision, lease.rules_digest)
        parsed_files = []
        logical_bytes = 0
        failed_files = []
        for entry in self.reader.list_tree(lease.commit_sha, self.budget):
            if not entry.included or not entry.path.endswith(".py"):
                continue
            data = self.reader.read_blob(lease.commit_sha, entry.object_id, self.budget.max_file_bytes)
            parsed = self.parser.parse_file(entry.path, data, snapshot_id)
            if parsed.errors:
                failed_files.append(entry.path)
                continue
            parsed_files.append(parsed)
            logical_bytes += len(data)
        nodes = tuple(symbol for parsed in parsed_files for symbol in parsed.symbols)
        chunks = tuple(chunk for parsed in parsed_files for chunk in parsed.chunks)
        edges = self.parser.contains_edges(tuple(parsed_files), snapshot_id)
        snapshot = MapSnapshot(
            snapshot_id=snapshot_id, repository_id=lease.repository_id, commit_sha=lease.commit_sha,
            parser_revision=lease.parser_revision, rules_digest=lease.rules_digest,
            status="partial" if failed_files else "building", file_count=len(parsed_files),
            failed_files=tuple(failed_files), logical_bytes=logical_bytes,
        )
        return BuildOutput(snapshot, StagedMapRows(nodes=nodes, edges=edges, chunks=chunks))
