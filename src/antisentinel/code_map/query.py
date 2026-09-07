"""Scoped read-only queries over published code-map snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .models import CodeMapError


@dataclass(frozen=True)
class QueryScope:
    incident_id: str
    allowed_repositories: frozenset[str]


@dataclass(frozen=True)
class QueryEnvelope:
    items: tuple[dict[str, Any], ...] = ()
    error: CodeMapError | None = None
    snapshot_id: str | None = None
    requested_commit: str | None = None
    indexed_commit: str | None = None
    incomplete: bool = False


class CodeMapQuery:
    def __init__(self, store) -> None:
        self.store = store

    def get_snapshot(self, scope: QueryScope, repository_id: str, requested_commit: str) -> QueryEnvelope:
        rejected = self._scope_error(scope, repository_id)
        if rejected:
            return QueryEnvelope(error=rejected, requested_commit=requested_commit)
        snapshot = self.store.get_published_snapshot(repository_id, requested_commit)
        if snapshot is None:
            return QueryEnvelope(error=CodeMapError("snapshot_missing", False, {}), requested_commit=requested_commit)
        return QueryEnvelope(snapshot_id=snapshot.snapshot_id, requested_commit=requested_commit, indexed_commit=snapshot.commit_sha)

    def find_symbols(self, scope: QueryScope, repository_id: str, snapshot_id: str, qualified_name: str, *, limit: int = 20) -> QueryEnvelope:
        rejected = self._snapshot_scope_error(scope, repository_id, snapshot_id)
        if rejected:
            return QueryEnvelope(error=rejected, snapshot_id=snapshot_id)
        rows = self.store.database.query(
            """SELECT n.node_id,n.kind,n.qualified_name,n.path,n.start_line,n.end_line,
                      c.chunk_id FROM code_map_nodes n LEFT JOIN code_map_chunks c ON c.node_id=n.node_id AND c.snapshot_id=n.snapshot_id
               WHERE n.snapshot_id=? AND n.repository_id=? AND n.qualified_name=?
               ORDER BY n.path,n.qualified_name,n.start_line,n.node_id LIMIT ?""",
            (snapshot_id, repository_id, qualified_name, limit),
        )
        return QueryEnvelope(items=tuple(dict(row) for row in rows), snapshot_id=snapshot_id)

    def get_neighbors(self, scope: QueryScope, repository_id: str, snapshot_id: str, node_id: str, *, direction: str, relations: tuple[str, ...], depth: int = 1, node_budget: int = 50) -> QueryEnvelope:
        rejected = self._snapshot_scope_error(scope, repository_id, snapshot_id)
        if rejected:
            return QueryEnvelope(error=rejected, snapshot_id=snapshot_id)
        if direction not in {"outbound", "inbound", "both"} or not 1 <= depth <= 3 or not 1 <= node_budget <= 200:
            return QueryEnvelope(error=CodeMapError("invalid_arguments", False, {}), snapshot_id=snapshot_id)
        if not relations or any(r not in {"contains", "calls", "imports", "inherits", "tested_by"} for r in relations):
            return QueryEnvelope(error=CodeMapError("relation_not_available", False, {}), snapshot_id=snapshot_id)
        seed = self.store.database.query("SELECT node_id FROM code_map_nodes WHERE node_id=? AND snapshot_id=?", (node_id, snapshot_id))
        if not seed:
            return QueryEnvelope(error=CodeMapError("source_not_found", False, {}), snapshot_id=snapshot_id)
        visited, frontier, items = {node_id}, [node_id], []
        limited = False
        inspected = 0
        for _ in range(depth):
            following = []
            for source in frontier:
                columns = [("source_node_id", "target_node_id")] if direction == "outbound" else [("target_node_id", "source_node_id")]
                if direction == "both":
                    columns = [("source_node_id", "target_node_id"), ("target_node_id", "source_node_id")]
                for origin, target in columns:
                    marks = ",".join("?" for _ in relations)
                    rows = self.store.database.query(
                        f"SELECT n.node_id,n.kind,n.qualified_name,n.path,n.start_line,n.end_line FROM code_map_edges e JOIN code_map_nodes n ON n.node_id=e.{target} AND n.snapshot_id=e.snapshot_id WHERE e.snapshot_id=? AND e.{origin}=? AND e.relation IN ({marks}) ORDER BY n.path,n.start_line,n.node_id LIMIT ?",
                        (snapshot_id, source, *relations, max(1, 1001-inspected)))
                    for row in rows:
                        inspected += 1
                        if inspected > 1000:
                            limited = True
                            break
                        if row["node_id"] in visited:
                            continue
                        if len(items) >= node_budget:
                            limited = True
                            break
                        visited.add(row["node_id"])
                        following.append(row["node_id"])
                        items.append(dict(row))
                    if limited:
                        break
                if limited:
                    break
            frontier = following
            if limited or not frontier:
                break
        return QueryEnvelope(items=tuple(items), snapshot_id=snapshot_id, incomplete=limited)

    def get_node(self, scope: QueryScope, repository_id: str, snapshot_id: str, node_id: str) -> QueryEnvelope:
        rejected = self._snapshot_scope_error(scope, repository_id, snapshot_id)
        if rejected:
            return QueryEnvelope(error=rejected, snapshot_id=snapshot_id)
        rows = self.store.database.query(
            "SELECT n.node_id,n.kind,n.qualified_name,n.path,n.start_line,n.end_line,c.chunk_id FROM code_map_nodes n LEFT JOIN code_map_chunks c ON c.node_id=n.node_id AND c.snapshot_id=n.snapshot_id WHERE n.node_id=? AND n.snapshot_id=? AND n.repository_id=?",
            (node_id, snapshot_id, repository_id),
        )
        return QueryEnvelope(items=tuple(dict(row) for row in rows), snapshot_id=snapshot_id)

    def read_source(self, scope: QueryScope, repository_id: str, snapshot_id: str, chunk_id: str) -> QueryEnvelope:
        rejected = self._snapshot_scope_error(scope, repository_id, snapshot_id)
        if rejected:
            return QueryEnvelope(error=rejected, snapshot_id=snapshot_id)
        rows = self.store.database.query(
            "SELECT path,byte_start,byte_end,content_hash,encoding FROM code_map_chunks WHERE chunk_id=? AND snapshot_id=?",
            (chunk_id, snapshot_id),
        )
        if not rows:
            return QueryEnvelope(error=CodeMapError("source_not_found", False, {}), snapshot_id=snapshot_id)
        chunk = rows[0]
        snapshot = self.store.get_snapshot(snapshot_id)
        assert snapshot is not None
        blob = self.store.read_chunk_source(repository_id, snapshot.commit_sha, chunk_id)
        data = blob[int(chunk["byte_start"]):int(chunk["byte_end"])]
        if hashlib.sha256(data).hexdigest() != chunk["content_hash"]:
            return QueryEnvelope(error=CodeMapError("hash_mismatch", False, {}), snapshot_id=snapshot_id)
        return QueryEnvelope(items=({
            "chunk_id": chunk_id, "path": chunk["path"], "content": data.decode(chunk["encoding"]),
            "content_hash": chunk["content_hash"],
        },), snapshot_id=snapshot_id, indexed_commit=snapshot.commit_sha)

    def _snapshot_scope_error(self, scope: QueryScope, repository_id: str, snapshot_id: str) -> CodeMapError | None:
        rejected = self._scope_error(scope, repository_id)
        if rejected:
            return rejected
        snapshot = self.store.get_snapshot(snapshot_id)
        if snapshot is None or snapshot.repository_id != repository_id or snapshot.status != "ready":
            return CodeMapError("scope_mismatch", False, {})
        return None

    @staticmethod
    def _scope_error(scope: QueryScope, repository_id: str) -> CodeMapError | None:
        if repository_id not in scope.allowed_repositories:
            return CodeMapError("scope_mismatch", False, {"incident_id": scope.incident_id})
        return None
