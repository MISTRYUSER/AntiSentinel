"""Deterministic reciprocal-rank fusion for memory candidate channels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class RetrievalCandidate:
    memory_id: str
    channel: str
    rank: int
    raw_score: float


@dataclass(frozen=True)
class FusedCandidate:
    memory_id: str
    rrf_score: float
    channels: tuple[str, ...]
    ranks: dict[str, int]


class ReciprocalRankFusion:
    def __init__(self, *, k: int = 60) -> None:
        if k < 1:
            raise ValueError("rrf k must be positive")
        self.k = k

    def combine(self, channels: Mapping[str, Sequence[RetrievalCandidate]], *, limit: int) -> tuple[FusedCandidate, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        merged: dict[str, tuple[float, dict[str, int]]] = {}
        for channel, candidates in channels.items():
            for candidate in candidates:
                if candidate.channel != channel or candidate.rank < 1:
                    raise ValueError("candidate channel and rank must be valid")
                score, ranks = merged.get(candidate.memory_id, (0.0, {}))
                if channel not in ranks or candidate.rank < ranks[channel]:
                    ranks[channel] = candidate.rank
                    score = sum(1 / (self.k + rank) for rank in ranks.values())
                merged[candidate.memory_id] = (score, ranks)
        fused = [FusedCandidate(memory_id, score, tuple(sorted(ranks)), dict(ranks)) for memory_id, (score, ranks) in merged.items()]
        return tuple(sorted(fused, key=lambda item: (-item.rrf_score, item.memory_id))[:limit])
