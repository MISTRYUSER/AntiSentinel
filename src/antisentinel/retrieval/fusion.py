"""Deterministic reciprocal-rank fusion for code-search channels."""

from __future__ import annotations

from dataclasses import dataclass

from .models import CodeSearchHit
from .vector import VectorSearchHit


@dataclass(frozen=True)
class FusedCodeSearchHit:
    document_id: str
    score: float
    channels: tuple[str, ...]
    ranks: dict[str, int]
    source_identity: dict[str, object]


class ReciprocalRankFusion:
    def __init__(self, *, k: int = 60) -> None:
        if k < 1:
            raise ValueError("rrf k must be positive")
        self.k = k

    def combine(
        self,
        keyword_hits: tuple[CodeSearchHit, ...] | list[CodeSearchHit],
        vector_hits: tuple[VectorSearchHit, ...] | list[VectorSearchHit],
        *,
        limit: int,
    ) -> tuple[FusedCodeSearchHit, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        channels = (("keyword", keyword_hits), ("vector", vector_hits))
        records: list[tuple[str, int, str, dict[str, object]]] = []
        identities = {}
        for channel, hits in channels:
            seen: set[str] = set()
            for rank, hit in enumerate(hits, start=1):
                previous=identities.get(hit.document_id)
                if previous is not None and any(previous.get(k)!=hit.source_identity.get(k) for k in ('repository_id','snapshot_id','published_generation','commit_sha','node_id','chunk_id','path','source_hash','parent_source_hash')):
                    raise ValueError('conflicting source for document ID')
                if previous is not None and any(k in previous and k in hit.source_identity and previous[k]!=hit.source_identity[k] for k in ('byte_start','byte_end')):
                    raise ValueError('conflicting source range for document ID')
                identities[hit.document_id]={**(previous or {}),**hit.source_identity}
                if hit.document_id in seen:
                    continue
                seen.add(hit.document_id)
                records.append((channel, rank, hit.document_id, dict(hit.source_identity)))
        # Each member must overlap its chosen representative, not merely a bridge.
        groups: list[list[tuple[str,int,str,dict[str,object]]]] = []
        by_document = {}
        for record in sorted(records,key=lambda r:(r[1],r[0],r[2])):
            group = by_document.get(record[2])
            if group is None:
                group = next((g for g in groups if _overlapping_source(g[0][3],record[3])),None)
                if group is None:
                    group=[]
                    groups.append(group)
                by_document[record[2]]=group
            group.append(record)
        fused_values = []
        for group in groups:
            ranks: dict[str, int] = {}
            for channel, rank, _, _ in group:
                ranks[channel] = min(rank, ranks.get(channel, rank))
            representative = min(group, key=lambda record: (record[1], record[0], record[2]))
            fused_values.append(FusedCodeSearchHit(
                document_id=representative[2],
                score=sum(1 / (self.k + rank) for rank in ranks.values()),
                channels=tuple(sorted(ranks)),
                ranks=ranks,
                source_identity=dict(representative[3]),
            ))
        fused = tuple(fused_values)
        return tuple(sorted(fused, key=lambda hit: (-hit.score, hit.document_id))[:limit])


def _overlapping_source(left: dict[str, object], right: dict[str, object]) -> bool:
    keys=('repository_id','snapshot_id','published_generation','commit_sha','path')
    if any(left.get(k) is None or left.get(k)!=right.get(k) for k in keys):
        return False
    if any(not isinstance(left[k],str) or not left[k] for k in ('repository_id','snapshot_id','commit_sha','path')) or any(type(source.get('published_generation')) is not int or source['published_generation']<1 for source in (left,right)):
        return False
    left_start, left_end = left.get("byte_start"), left.get("byte_end")
    right_start, right_end = right.get("byte_start"), right.get("byte_end")
    if all(type(value) is int for value in (left_start, left_end, right_start, right_end)) and 0<=left_start<left_end and 0<=right_start<right_end:
        return max(left_start, right_start) < min(left_end, right_end)
    return False
