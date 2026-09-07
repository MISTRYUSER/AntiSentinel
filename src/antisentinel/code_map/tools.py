"""Startup-bound read-only ToolDefinitions for published code-map facts."""

from __future__ import annotations

from typing import Callable

from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult


def build_code_map_tools(query, scope_provider: Callable[[dict], object], source_evidence_service=None) -> tuple[ToolDefinition, ...]:
    def result(envelope):
        return {
            "items": list(envelope.items), "snapshot_id": envelope.snapshot_id,
            "requested_commit": envelope.requested_commit, "indexed_commit": envelope.indexed_commit,
            "incomplete": envelope.incomplete,
            "error": {"code": envelope.error.code, "retryable": envelope.error.retryable, "details": dict(envelope.error.details)} if envelope.error else None,
        }

    def get_snapshot(arguments):
        return result(query.get_snapshot(scope_provider(arguments), arguments["repository_id"], arguments["requested_commit"]))

    def find_symbols(arguments):
        return result(query.find_symbols(scope_provider(arguments), arguments["repository_id"], arguments["snapshot_id"], arguments["qualified_name"], limit=arguments.get("limit", 20)))

    def get_node(arguments):
        return result(query.get_node(scope_provider(arguments), arguments["repository_id"], arguments["snapshot_id"], arguments["node_id"]))

    def get_neighbors(arguments):
        return result(query.get_neighbors(scope_provider(arguments), arguments["repository_id"], arguments["snapshot_id"], arguments["node_id"], direction=arguments.get("direction", "outbound"), relations=tuple(arguments.get("relations", ["contains"])), depth=arguments.get("depth", 1), node_budget=arguments.get("node_budget", 50)))

    def read_source(arguments):
        if source_evidence_service is not None:
            scope = scope_provider(arguments)
            chunk_id = arguments.get("chunk_id")
            if not chunk_id and arguments.get("node_id"):
                rows = query.store.database.query("SELECT chunk_id FROM code_map_chunks WHERE node_id=? AND snapshot_id=? ORDER BY start_line LIMIT 1", (arguments["node_id"], arguments["snapshot_id"]))
                chunk_id = rows[0]["chunk_id"] if rows else None
            if not chunk_id:
                return result(query.read_source(scope, arguments["repository_id"], arguments["snapshot_id"], ""))
            source = source_evidence_service.read_source(scope.incident_id, arguments["repository_id"], arguments["snapshot_id"], chunk_id)
            evidence = source_evidence_service.evidence_store.get(source.evidence_id)
            return ToolExecutionResult(status="succeeded", result={"items": [{"path": source.path, "content_hash": source.content_hash}], "source_context": source.__dict__}, result_summary=f"source: {source.path}", evidence=evidence)
        return result(query.read_source(scope_provider(arguments), arguments["repository_id"], arguments["snapshot_id"], arguments["chunk_id"]))

    common = {"type": "object", "additionalProperties": False}
    return (
        ToolDefinition("code_map.get_snapshot", "Read one published code-map snapshot.", {**common, "properties": {"repository_id": {"type": "string"}, "requested_commit": {"type": "string"}}, "required": ["repository_id", "requested_commit"]}, get_snapshot),
        ToolDefinition("code_map.find_symbols", "Find exact symbols in one snapshot.", {**common, "properties": {"repository_id": {"type": "string"}, "snapshot_id": {"type": "string"}, "qualified_name": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["repository_id", "snapshot_id", "qualified_name"]}, find_symbols),
        ToolDefinition("code_map.get_node", "Read one code-map node.", {**common, "properties": {"repository_id": {"type": "string"}, "snapshot_id": {"type": "string"}, "node_id": {"type": "string"}}, "required": ["repository_id", "snapshot_id", "node_id"]}, get_node),
        ToolDefinition("code_map.get_neighbors", "Read bounded code-map neighbors.", {**common, "properties": {"repository_id": {"type": "string"}, "snapshot_id": {"type": "string"}, "node_id": {"type": "string"}, "direction": {"type": "string"}, "relations": {"type": "array"}, "depth": {"type": "integer"}, "node_budget": {"type": "integer"}}, "required": ["repository_id", "snapshot_id", "node_id"]}, get_neighbors),
        ToolDefinition("code_map.read_source", "Read one hash-verified source chunk.", {**common, "properties": {"repository_id": {"type": "string"}, "snapshot_id": {"type": "string"}, "chunk_id": {"type": "string"}, "node_id": {"type": "string"}}, "required": ["repository_id", "snapshot_id"]}, read_source),
    )
