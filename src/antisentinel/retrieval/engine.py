"""Unified code retrieval facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from itertools import zip_longest
import hashlib

from .fusion import FusedCodeSearchHit, ReciprocalRankFusion, _overlapping_source
from .graph import GraphSeed
from .models import CodeSearchScope

MAX_TOP_K = 5
MAX_CANDIDATE_LIMIT = 30

@dataclass(frozen=True)
class RetrievalResult:
    hits: tuple[FusedCodeSearchHit, ...]
    mode: str
    channel_statuses: dict[str, str]
    degraded: bool = False
    error_code: str | None = None
    truncated: bool = False
    incomplete: bool = False


class CodeRetrievalService:
    def __init__(self, keyword_retriever: Any, vector_retriever: Any | None, *, channel_store: Any, model_revision: str, template_revision: str, projection_revision: str, rrf_k: int = 60, graph_expander: Any | None = None, graph_selection: str = 'interleave', graph_source_resolver=None) -> None:
        if graph_selection not in {'interleave', 'replace_seed', 'replace_container'}:
            raise ValueError('unsupported graph selection policy')
        self.graph_selection = graph_selection
        self.keyword_retriever = keyword_retriever
        self.vector_retriever = vector_retriever
        self.channel_store = channel_store
        self.model_revision = model_revision
        self.template_revision = template_revision
        self.projection_revision = projection_revision
        self.graph_expander = graph_expander
        self.graph_source_resolver = graph_source_resolver
        self.fusion = ReciprocalRankFusion(k=rrf_k)

    def search(self, query: str, scope: CodeSearchScope, *, mode: str, query_vector: tuple[float, ...] | list[float] | None = None, top_k: int = 5, candidate_limit: int = 30) -> RetrievalResult:
        if mode not in {"keyword", "vector", "hybrid", "graph", "hybrid_graph"}:
            raise ValueError("mode must be keyword, vector, hybrid, graph, or hybrid_graph")
        if top_k < 1 or top_k > MAX_TOP_K or candidate_limit < top_k or candidate_limit > MAX_CANDIDATE_LIMIT:
            raise ValueError("top_k and candidate_limit exceed maximum")
        if mode == 'hybrid_graph':
            base = self.search(query, scope, mode='hybrid', query_vector=query_vector,
                top_k=min(MAX_TOP_K, candidate_limit), candidate_limit=candidate_limit)
            return self._expand_hybrid(base, scope, top_k)
        lexical_ready = True
        if mode in {"keyword", "hybrid", "graph"}:
            readiness = getattr(self.keyword_retriever, "is_ready", None)
            lexical_ready = bool(readiness(scope, projection_revision=self.projection_revision)) if readiness is not None else False
        keyword_hits = self.keyword_retriever.search(scope, query, limit=candidate_limit, projection_revision=self.projection_revision) if lexical_ready and mode in {"keyword", "hybrid", "graph"} else ()
        keyword_fused = self._single_channel(keyword_hits, top_k, "keyword")
        if mode == "keyword":
            if not lexical_ready:
                return RetrievalResult((), mode, {"keyword": "unavailable"}, degraded=True, error_code="lexical_unavailable")
            return RetrievalResult(keyword_fused, mode, {"keyword": "ready"})
        if mode == "graph":
            if not lexical_ready:
                return RetrievalResult((), mode, {"keyword": "unavailable", "graph": "unavailable"}, degraded=True, error_code="lexical_unavailable")
            if self.graph_expander is None:
                return RetrievalResult(keyword_fused, mode, {"keyword": "ready", "graph": "unavailable"}, degraded=True, error_code="graph_unavailable")
            seeds = tuple(GraphSeed(hit.source_identity["node_id"], hit.document_id, rank) for rank, hit in enumerate(keyword_hits[:5], start=1) if hit.source_identity.get("node_id"))
            graph_scope = {"repository_id": scope.repository_id, "snapshot_id": scope.snapshot_id, "published_generation": scope.published_generation, "commit_sha": scope.commit_sha}
            try:
                expansion = self.graph_expander.expand(seeds, scope=graph_scope, depth=1, node_budget=20, edge_budget=40)
            except (TimeoutError, ConnectionError, OSError):
                return RetrievalResult(keyword_fused, mode, {"keyword": "ready", "graph": "unavailable"}, degraded=True, error_code="graph_unavailable")
            chunk_candidates=self._graph_sources(expansion, scope)
            expanded = tuple(
                FusedCodeSearchHit(candidate.get('document_id', f"graph:{item.node_id}:{candidate['chunk_id']}"), 1 / (self.fusion.k + rank), ("graph",), {"graph": rank},
                    {**candidate,'qualified_name':item.qualified_name,'relation':item.relation,'reason':item.reason,'edge_id':item.edge_id,'seed_document_id':item.seed_document_id})
                for rank,(item,candidate) in enumerate(chunk_candidates[:MAX_CANDIDATE_LIMIT],start=1)
            )
            status = {"keyword": "ready", "graph": "ready" if not expansion.degraded else "degraded"}
            selected, selection_truncated = _select_graph_hits(keyword_fused, expanded, top_k)
            truncated = expansion.truncated or selection_truncated or len(chunk_candidates)>MAX_CANDIDATE_LIMIT
            return RetrievalResult(selected, mode, status, degraded=expansion.degraded,
                error_code="graph_degraded" if expansion.degraded else None,
                truncated=truncated, incomplete=expansion.incomplete or truncated)

        vector_hits = ()
        vector_error: str | None = None
        if self.vector_retriever is None or query_vector is None:
            vector_error = "vector_unavailable"
        else:
            try:
                vector_hits = self.vector_retriever.search(
                    query_vector,
                    scope,
                    model_revision=self.model_revision,
                    template_revision=self.template_revision,
                    projection_revision=self.projection_revision,
                    channel_store=self.channel_store,
                    limit=candidate_limit,
                )
            except (TimeoutError, ConnectionError, OSError):
                vector_error = "vector_unavailable"
        if mode == "vector":
            if vector_error:
                return RetrievalResult((), mode, {"vector": "unavailable"}, degraded=True, error_code=vector_error)
            return RetrievalResult(self._single_channel(vector_hits, top_k, "vector"), mode, {"vector": "ready"})
        if vector_error:
            return RetrievalResult(keyword_fused, mode, {"keyword": "ready" if lexical_ready else "unavailable", "vector": "unavailable"}, degraded=True, error_code=vector_error)
        if not lexical_ready:
            return RetrievalResult(self._single_channel(vector_hits, top_k, "vector"), mode, {"keyword": "unavailable", "vector": "ready"}, degraded=True, error_code="lexical_unavailable")
        return RetrievalResult(self.fusion.combine(keyword_hits, vector_hits, limit=top_k), mode, {"keyword": "ready", "vector": "ready"})

    def _expand_hybrid(self, base, scope, top_k):
        statuses = dict(base.channel_statuses)
        if self.graph_expander is None:
            return RetrievalResult(base.hits, 'hybrid_graph', {**statuses, 'graph': 'unavailable'},
                degraded=True, error_code=base.error_code or 'graph_unavailable')
        seeds = tuple(GraphSeed(hit.source_identity['node_id'], hit.document_id, rank)
            for rank, hit in enumerate(base.hits, 1) if hit.source_identity.get('node_id'))
        try:
            expansion = self.graph_expander.expand(seeds, scope=vars(scope), depth=1, node_budget=20, edge_budget=40)
        except (TimeoutError, ConnectionError, OSError):
            return RetrievalResult(base.hits, 'hybrid_graph', {**statuses, 'graph': 'unavailable'},
                degraded=True, error_code=base.error_code or 'graph_unavailable')
        candidates = self._graph_sources(expansion, scope)
        expanded = []
        for rank, (item, candidate) in enumerate(candidates[:MAX_CANDIDATE_LIMIT], 1):
            identity = '\x1f'.join((*map(str, scope.key), candidate['node_id'], candidate['chunk_id'], self.projection_revision))
            document_id = candidate.get('document_id') or hashlib.sha256(identity.encode()).hexdigest()
            expanded.append(FusedCodeSearchHit(document_id, 1 / (self.fusion.k + rank), ('graph',), {'graph': rank},
                {**candidate, 'edge_id': item.edge_id, 'seed_document_id': item.seed_document_id, 'relation': item.relation,
                 'seed_kind': getattr(item, 'seed_kind', None)}))
        if self.graph_selection in {'replace_seed', 'replace_container'}:
            hits, clipped = _replace_graph_seed_hits(base.hits, expanded, top_k,
                containers_only=self.graph_selection == 'replace_container')
        else:
            hits, clipped = _select_graph_hits(base.hits, expanded, top_k)
        truncated = base.truncated or expansion.truncated or clipped or len(candidates) > MAX_CANDIDATE_LIMIT
        return RetrievalResult(hits, 'hybrid_graph', {**statuses, 'graph': 'degraded' if expansion.degraded else 'ready'},
            degraded=base.degraded or expansion.degraded,
            error_code=base.error_code or ('graph_degraded' if expansion.degraded else None),
            truncated=truncated, incomplete=base.incomplete or expansion.incomplete or truncated)

    def _single_channel(self, hits, limit: int, channel: str) -> tuple[FusedCodeSearchHit, ...]:
        return self.fusion.combine(hits, (), limit=limit) if channel == 'keyword' else self.fusion.combine((), hits, limit=limit)

    def _graph_sources(self, expansion, scope):
        values = []
        for item in expansion.items:
            for candidate in item.source_candidates:
                sources = self.graph_source_resolver(scope, candidate) if self.graph_source_resolver else (candidate,)
                values.extend((item, source) for source in sources)
        return values


def _replace_graph_seed_hits(seeds, expanded, limit, *, containers_only=False):
    """Each verified child can compete only for its own seed's original slot."""
    seed_ids = {hit.document_id for hit in seeds}
    by_parent = {}
    for hit in expanded:
        if containers_only and hit.source_identity.get('seed_kind') not in {'class', 'module'}:
            continue
        parent = hit.source_identity.get('seed_document_id')
        if parent in seed_ids and hit.document_id not in seed_ids and hit.source_identity.get('relation') == 'contains':
            by_parent.setdefault(parent, []).append(hit)
    result, seen = [], set()
    for seed in seeds[:limit]:
        selected = next((hit for hit in by_parent.get(seed.document_id, ()) if hit.document_id not in seen), seed)
        if selected.document_id not in seen:
            result.append(selected)
            seen.add(selected.document_id)
    return tuple(result), len(seed_ids | {hit.document_id for hit in expanded}) > len(result)


def _select_graph_hits(seeds, expanded, limit):
    """Interleave ranked seeds/neighbors; one slot favors a traceable neighbor."""
    if limit == 1 and expanded:
        ordered = (*expanded, *seeds)
    else:
        ordered = tuple(hit for pair in zip_longest(seeds, expanded) for hit in pair if hit is not None)
    unique, seen = [], set()
    for hit in ordered:
        identity = hit.source_identity
        key = tuple(identity.get(k) for k in ('repository_id', 'snapshot_id', 'published_generation', 'commit_sha')) + (identity.get('chunk_id') or identity.get('node_id') or hit.document_id,)
        if identity.get('parent_source_hash') is not None:
            key += (identity.get('byte_start'), identity.get('byte_end'))
            if any(_overlapping_source(identity, selected.source_identity) for selected in unique):
                continue
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return tuple(unique[:limit]), len(unique) > limit
