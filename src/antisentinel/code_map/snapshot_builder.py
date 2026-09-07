"""Build the minimum Python code-map projection from one fixed Git commit."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib

from .identity import content_hash, node_id_for, snapshot_id_for
from .incremental import AstFactCache, AstFactKey
from .models import MapSnapshot, RepositoryBudget
from .python_parser import PythonAstParser
from .store import JobLease, SourceBlob, SourceFile, StagedMapRows


@dataclass(frozen=True)
class BuildOutput:
    snapshot: MapSnapshot
    rows: StagedMapRows
    cache_hits: int = 0


class SnapshotBuilder:
    def __init__(self, reader, *, parser: PythonAstParser | None = None, budget: RepositoryBudget | None = None, fact_cache: AstFactCache | None = None) -> None:
        self.reader = reader
        self.parser = parser or PythonAstParser("python-3.13/ast-v1")
        self.budget = budget or RepositoryBudget()
        self.fact_cache = fact_cache

    def build(self, lease: JobLease) -> BuildOutput:
        snapshot_id = snapshot_id_for(lease.repository_id, lease.commit_sha, lease.parser_revision, lease.rules_digest)
        parsed_files = []
        blobs = []
        files = []
        logical_bytes = 0
        failed_files = []
        cache_hits = 0
        for entry in self.reader.list_tree(lease.commit_sha, self.budget):
            if not entry.included or not entry.path.endswith(".py"):
                continue
            data = self.reader.read_blob(lease.commit_sha, entry.object_id, self.budget.max_file_bytes)
            key = AstFactKey(content_hash(data), entry.path, "", lease.parser_revision, lease.rules_digest)
            parsed = self.fact_cache.get(key) if self.fact_cache else None
            if parsed is None:
                parsed = self.parser.parse_file(entry.path, data, snapshot_id)
                if self.fact_cache is not None:
                    self.fact_cache.put(key, parsed)
            else:
                parsed = _rebind(parsed, snapshot_id)
                cache_hits += 1
            if parsed.errors:
                failed_files.append(entry.path)
                continue
            parsed_files.append(parsed)
            logical_bytes += len(data)
            blobs.append(SourceBlob(parsed.file_hash, entry.object_id, data, parsed.encoding))
            file_identity = f"{snapshot_id}|{entry.path}"
            files.append(SourceFile(
                file_id=__import__("hashlib").sha256(file_identity.encode()).hexdigest(), snapshot_id=snapshot_id,
                path=entry.path, git_object_id=entry.object_id, content_hash=parsed.file_hash, byte_count=len(data),
            ))
        nodes = tuple(symbol for parsed in parsed_files for symbol in parsed.symbols)
        chunks = tuple(chunk for parsed in parsed_files for chunk in parsed.chunks)
        edges = self.parser.contains_edges(tuple(parsed_files), snapshot_id)
        snapshot = MapSnapshot(
            snapshot_id=snapshot_id, repository_id=lease.repository_id, commit_sha=lease.commit_sha,
            parser_revision=lease.parser_revision, rules_digest=lease.rules_digest,
            status="partial" if failed_files else "building", file_count=len(parsed_files),
            failed_files=tuple(failed_files), logical_bytes=logical_bytes,
        )
        return BuildOutput(snapshot, StagedMapRows(nodes=nodes, edges=edges, chunks=chunks, blobs=tuple(blobs), files=tuple(files)), cache_hits)


def _rebind(parsed, snapshot_id):
    identifiers = {}
    nodes = []
    for node in parsed.symbols:
        identifier = node_id_for(snapshot_id, node.path, node.kind, node.qualified_name, node.start_line, node.end_line)
        identifiers[node.node_id] = identifier
        nodes.append(replace(node, node_id=identifier, snapshot_id=snapshot_id))
    chunks = []
    for chunk in parsed.chunks:
        node_id = identifiers[chunk.node_id]
        chunk_id = hashlib.sha256(f"{node_id}|{chunk.byte_start}|{chunk.byte_end}".encode()).hexdigest()
        chunks.append(replace(chunk, chunk_id=chunk_id, node_id=node_id, snapshot_id=snapshot_id))
    parents = {identifiers[child]: identifiers[parent] for child, parent in parsed.parents.items()}
    return replace(parsed, symbols=tuple(nodes), chunks=tuple(chunks), parents=parents)
