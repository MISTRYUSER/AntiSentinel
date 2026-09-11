"""Tools read source facts without mutation; read_evidence appends internal Evidence."""

from __future__ import annotations

from typing import Any, Callable
import json

from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult

from .graph import GraphSeed
from .models import CodeSearchScope
from antisentinel.domain.errors import DomainError
from antisentinel.runtime.deadline import check_deadline, QueryDeadlineExceeded

# Working-set / pack consume result_summary; keep it bounded.
_SEARCH_SUMMARY_MAX_BYTES = 4096
_HIT_IDENTITY_KEYS = (
    "repository_id",
    "snapshot_id",
    "published_generation",
    "commit_sha",
    "node_id",
    "chunk_id",
    "path",
    "source_hash",
    "byte_start",
    "byte_end",
    "parent_source_hash",
)


def _compact_hit(hit: Any) -> dict[str, Any]:
    identity = dict(getattr(hit, "source_identity", None) or {})
    compact: dict[str, Any] = {
        "document_id": getattr(hit, "document_id", identity.get("document_id")),
        "score": getattr(hit, "score", None),
        "channels": list(getattr(hit, "channels", ()) or ()),
    }
    for key in _HIT_IDENTITY_KEYS:
        if key in identity and identity[key] is not None:
            compact[key] = identity[key]
    ranks = getattr(hit, "ranks", None)
    if isinstance(ranks, dict) and ranks:
        compact["ranks"] = dict(ranks)
    return {key: value for key, value in compact.items() if value is not None}


def compact_search_payload(result: Any, *, max_bytes: int = _SEARCH_SUMMARY_MAX_BYTES) -> dict[str, Any]:
    """Compact search result for working-set summaries (not the full tool result)."""
    hits = [_compact_hit(hit) for hit in getattr(result, "hits", ()) or ()]
    payload: dict[str, Any] = {
        "hits": hits,
        "mode": getattr(result, "mode", None),
        "channel_statuses": dict(getattr(result, "channel_statuses", None) or {}),
        "degraded": bool(getattr(result, "degraded", False)),
        "error_code": getattr(result, "error_code", None),
        "truncated": bool(getattr(result, "truncated", False)),
        "incomplete": bool(getattr(result, "incomplete", False)),
        "hit_count": len(hits),
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= max_bytes:
        return payload
    # Drop hits from the tail until under budget; keep status fields.
    kept = list(hits)
    while kept:
        kept.pop()
        payload = {
            "hits": kept,
            "mode": getattr(result, "mode", None),
            "channel_statuses": dict(getattr(result, "channel_statuses", None) or {}),
            "degraded": bool(getattr(result, "degraded", False)),
            "error_code": getattr(result, "error_code", None),
            "truncated": True,
            "incomplete": True,
            "hit_count": len(hits),
            "summary_truncated": True,
        }
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= max_bytes:
            return payload
    payload["hits"] = []
    payload["summary_truncated"] = True
    payload["truncated"] = True
    payload["incomplete"] = True
    return payload


def build_code_retrieval_tools(service, scope_provider: Callable[[dict[str, Any]], Any], *, graph_expander=None, evidence_assembler=None, query_encoder=None, scope_selectors=False) -> tuple[ToolDefinition, ...]:
    def search(arguments: dict[str, Any]):
        scope = scope_provider(arguments)
        if isinstance(scope, dict):
            scope = CodeSearchScope(**{k: scope[k] for k in ('repository_id', 'snapshot_id', 'published_generation', 'commit_sha')})
        mode = arguments.get("mode", "hybrid")
        if query_encoder is not None and "query_vector" in arguments:
            raise DomainError("caller_query_vector_forbidden")
        vector = tuple(arguments["query_vector"]) if arguments.get("query_vector") is not None else None
        # Resolve authorization first, and validate before making a remote call.
        top_k, limit = arguments.get("top_k", 5), arguments.get("candidate_limit", 30)
        if mode not in {"keyword", "vector", "hybrid", "graph", "hybrid_graph"} or type(top_k) is not int or type(limit) is not int or not 1 <= top_k <= 5 or not top_k <= limit <= 30:
            raise DomainError("invalid_retrieval_options")
        if not isinstance(arguments.get("query"), str) or not arguments["query"].strip():
            raise DomainError("invalid_retrieval_query")
        if query_encoder is not None and mode in {"vector", "hybrid", "hybrid_graph"}:
            check_deadline()
            try:
                vector = tuple(query_encoder(arguments["query"]))
            except QueryDeadlineExceeded:
                raise
            except (TimeoutError, ConnectionError):
                check_deadline()
                if mode == 'vector':
                    raise DomainError('vector_unavailable') from None
                vector = None
            except RuntimeError as error:
                if str(error) != 'embedding_transport_error':
                    raise
                check_deadline()
                if mode == 'vector':
                    raise DomainError('vector_unavailable') from None
                vector = None
        check_deadline()
        result = service.search(
            arguments["query"], scope, mode=arguments.get("mode", "hybrid"),
            query_vector=vector,
            top_k=arguments.get("top_k", 5), candidate_limit=arguments.get("candidate_limit", 30),
        )
        full = {
            "hits": [hit.__dict__ for hit in result.hits],
            "mode": result.mode,
            "channel_statuses": result.channel_statuses,
            "degraded": result.degraded,
            "error_code": result.error_code,
            "truncated": result.truncated,
            "incomplete": result.incomplete,
        }
        return ToolExecutionResult(
            status="succeeded",
            result=full,
            result_summary=json.dumps(compact_search_payload(result), ensure_ascii=False),
        )

    definitions = [ToolDefinition(
        "code_retrieval.search", "Search published code. Default hybrid fuses keyword/vector. Experimental hybrid_graph expands fused seeds through verified contains relations; graph uses keyword seeds.",
        {"type": "object", "properties": {"query": {"type": "string"}, "mode": {"type": "string"}, "query_vector": {"type": "array"}, "top_k": {"type": "integer"}, "candidate_limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False},
        search,
    )]
    if graph_expander is not None:
        def expand(arguments: dict[str, Any]):
            result = graph_expander.expand(
                [GraphSeed(**seed) for seed in arguments["seeds"]], scope=scope_provider(arguments),
                depth=arguments.get("depth", 1), node_budget=arguments.get("node_budget", 20), edge_budget=arguments.get("edge_budget", 40),
                relations=tuple(arguments.get('relations', ['contains'])),
            )
            payload = {"items": [item.__dict__ for item in result.items], "unresolved_count": result.unresolved_count,
                       "degraded": result.degraded, "truncated": result.truncated, 'incomplete': result.incomplete,
                       'rejected_count': result.rejected_count, 'inspected_edges': result.inspected_edges,
                       'rejected_chunks':result.rejected_chunks,'unreadable_nodes':result.unreadable_nodes}
            return ToolExecutionResult(status='succeeded', result=payload,
                                       result_summary=json.dumps(payload, ensure_ascii=False))
        definitions.append(ToolDefinition(
            "code_retrieval.expand_graph", "Read bounded validated contains relations; other relations are rejected.",
            {"type": "object", "properties": {"seeds": {"type": "array"}, 'relations': {'type': 'array', 'items': {'type': 'string', 'enum': ['contains']}}, "depth": {"type": "integer"}, "node_budget": {"type": "integer"}, "edge_budget": {"type": "integer"}}, "required": ["seeds"], "additionalProperties": False},
            expand,
        ))
    if evidence_assembler is not None:
        def read_evidence(arguments: dict[str, Any]):
            scope = scope_provider(arguments)
            incident_id = scope["incident_id"] if isinstance(scope, dict) else scope.incident_id
            source_scope = scope if isinstance(scope, dict) else vars(scope)
            candidates = arguments['candidates']
            for candidate in candidates:
                if any(candidate.get(k) != source_scope.get(k) for k in ('repository_id', 'snapshot_id', 'published_generation', 'commit_sha')):
                    raise DomainError('scope_mismatch')
            context = evidence_assembler.select(incident_id, candidates, max_fragments=arguments.get("max_fragments", 4), max_bytes=arguments.get("max_bytes", 32 * 1024))
            evidences = tuple(evidence_assembler.source_evidence_service.evidence_store.get(s.evidence_id) for s in context.slices)
            return ToolExecutionResult(
                status="succeeded",
                result={"slices": [slice.__dict__ if hasattr(slice, "__dict__") else slice for slice in context.slices], "total_bytes": context.total_bytes, "truncated": context.truncated},
                result_summary=f"evidence: {len(context.slices)} slices",
                evidences=evidences,
            )
        definitions.append(ToolDefinition(
            "code_retrieval.read_evidence", "Read-only access to source facts; appends internal Evidence only after scope, hash and budget checks.",
            {"type": "object", "properties": {"candidates": {"type": "array"}, "max_fragments": {"type": "integer"}, "max_bytes": {"type": "integer"}}, "required": ["candidates"], "additionalProperties": False},
            read_evidence,
        ))
    if query_encoder is not None:
        definitions[0].argument_schema['properties'].pop('query_vector')
    if scope_selectors:
        for definition in definitions:
            definition.argument_schema['properties'].update({
                'repository_id': {'type': 'string'}, 'snapshot_id': {'type': 'string'},
            })
    return tuple(definitions)
