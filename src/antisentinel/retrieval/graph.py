"""Bounded structural expansion over an immutable Code Map generation."""
from dataclasses import dataclass
from typing import Any, Mapping
from antisentinel.domain.errors import DomainError
from antisentinel.runtime.deadline import check_deadline


@dataclass(frozen=True)
class GraphSeed:
    node_id: str
    seed_document_id: str
    rank: int


@dataclass(frozen=True)
class GraphExpansion:
    node_id: str
    seed_document_id: str
    relation: str
    direction: str
    path: str
    qualified_name: str
    reason: str
    resolution: str
    basis: str
    published_generation: int = 1
    edge_id: str = ""
    chunk_ids: tuple[str, ...] = ()
    source_candidates: tuple[dict[str, Any], ...] = ()
    seed_kind: str | None = None


@dataclass(frozen=True)
class GraphExpansionResult:
    items: tuple[GraphExpansion, ...]
    unresolved_count: int = 0
    degraded: bool = False
    truncated: bool = False
    rejected_count: int = 0
    inspected_edges: int = 0
    incomplete: bool = False
    rejected_chunks: int = 0
    unreadable_nodes: int = 0


class GraphExpander:
    _RELATIONS = frozenset({'contains'})

    def __init__(self, store):
        self.store = store

    def expand(self, seeds, *, scope: Mapping[str, Any], depth=1, node_budget=20,
               edge_budget=40, direction="outbound",
               relations=('contains',)):
        check_deadline()
        if any(type(v) is not int for v in (depth, node_budget, edge_budget)) or not (
            1 <= depth <= 3 and 1 <= node_budget <= 20 and 1 <= edge_budget <= 40
        ):
            raise ValueError("graph budget exceeds maximum")
        if direction not in {"outbound", "inbound", "both"} or len(seeds) > 5:
            raise ValueError("invalid graph arguments")
        if not relations or not set(relations) <= self._RELATIONS:
            raise ValueError("graph relation is unavailable")
        generation = scope.get("published_generation")
        if type(generation) is not int or generation < 1:
            raise ValueError("graph scope generation is invalid")
        payload = self.store.read_generation(scope['snapshot_id'], generation)
        check_deadline()
        snapshot = payload['snapshot']
        if any(snapshot.get(k) != scope[k] for k in ('repository_id', 'snapshot_id', 'commit_sha')):
            raise DomainError('scope_mismatch')
        if snapshot.get('published_generation') != generation or snapshot['status'] != 'ready':
            raise DomainError('generation_not_ready')
        nodes = {n['node_id']: n for n in payload['nodes']}

        def valid(node):
            return node is not None and all(node.get(k) == scope[k] for k in ('repository_id', 'snapshot_id', 'commit_sha'))

        if any(not valid(nodes.get(s.node_id)) or type(s.rank) is not int or s.rank < 1 for s in seeds):
            raise DomainError('scope_mismatch')
        visited = {s.node_id for s in seeds}
        seed_kinds = {s.seed_document_id: nodes[s.node_id].get('kind') for s in seeds}
        frontier = [(s.node_id, s.seed_document_id, s.rank) for s in sorted(seeds, key=lambda s: (s.rank, s.node_id))]
        items, inspected_ids = [], set()
        unresolved = rejected = 0
        rejected_chunks = unreadable_nodes = 0
        truncated = False
        chunk_truncated = False
        edges = sorted(payload['edges'], key=lambda e: e['edge_id'])
        for _ in range(depth):
            following = []
            for origin, seed_id, rank in frontier:
                for edge in edges:
                    check_deadline()
                    outbound = edge['source_node_id'] == origin and direction in {'outbound', 'both'}
                    inbound = edge.get('target_node_id') == origin and direction in {'inbound', 'both'}
                    if not (outbound or inbound) or edge['relation'] not in relations or edge['edge_id'] in inspected_ids:
                        continue
                    if len(inspected_ids) == edge_budget:
                        truncated = True
                        break
                    inspected_ids.add(edge['edge_id'])
                    if edge.get('resolution') != 'resolved' or not edge.get('target_node_id'):
                        unresolved += 1
                        continue
                    parent, child = nodes.get(edge['source_node_id']), nodes.get(edge['target_node_id'])
                    structural = (
                        edge.get('snapshot_id') == scope['snapshot_id'] and valid(parent) and valid(child)
                        and edge['relation'] == 'contains' and edge.get('basis') in {'ast_parent', 'ast', 'tree-sitter'}
                        and parent['path'] == child['path'] and parent['node_id'] != child['node_id']
                        and parent['start_line'] <= child['start_line'] <= child['end_line'] <= parent['end_line']
                        and (parent['start_line'], parent['end_line']) != (child['start_line'], child['end_line'])
                    )
                    if not structural:
                        rejected += 1
                        continue
                    node = child if outbound else parent
                    if node['node_id'] in visited:
                        continue
                    if len(items) == node_budget:
                        truncated = True
                        break
                    visited.add(node['node_id'])
                    following.append((node['node_id'], seed_id, rank))
                    candidates=[]
                    seen_chunks=set()
                    for c in payload['chunks']:
                        check_deadline()
                        if c['node_id']!=node['node_id']:continue
                        if any(c.get(k)!=scope[k] for k in ('snapshot_id','commit_sha')) or c.get('path')!=node['path'] or not isinstance(c.get('content_hash'),str) or not c['content_hash'] or not isinstance(c.get('chunk_id'),str) or not c['chunk_id'] or type(c.get('byte_start')) is not int or type(c.get('byte_end')) is not int or not 0<=c['byte_start']<c['byte_end']:
                            rejected_chunks+=1
                            continue
                        if c['chunk_id'] in seen_chunks:
                            raise DomainError('graph_chunk_identity_mismatch')
                        seen_chunks.add(c['chunk_id'])
                        candidates.append({**{k:scope[k] for k in ('repository_id','snapshot_id','published_generation','commit_sha')},
                            'node_id':node['node_id'],'chunk_id':c['chunk_id'],'path':c['path'],'source_hash':c['content_hash'],
                            'byte_start':c['byte_start'],'byte_end':c['byte_end']})
                    candidates.sort(key=lambda c:(c['byte_start'],c['byte_end'],c['chunk_id']))
                    chunk_ids=tuple(c['chunk_id'] for c in candidates[:5])
                    if not candidates:unreadable_nodes+=1
                    if len(candidates)>5:chunk_truncated=True
                    items.append(GraphExpansion(node['node_id'], seed_id, 'contains', 'outbound' if outbound else 'inbound',
                        node['path'], node['qualified_name'], f"seed={seed_id};edge={edge['edge_id']};generation={generation}",
                        edge['resolution'], edge['basis'], generation, edge['edge_id'], chunk_ids,tuple(candidates[:5]), seed_kinds[seed_id]))
                if truncated:
                    break
            frontier = following
            if truncated or not frontier:
                break
        check_deadline()
        return GraphExpansionResult(tuple(items), unresolved, bool(unresolved or rejected or rejected_chunks or unreadable_nodes), truncated or chunk_truncated,
                                    rejected, len(inspected_ids), incomplete=truncated or chunk_truncated,
                                    rejected_chunks=rejected_chunks, unreadable_nodes=unreadable_nodes)
