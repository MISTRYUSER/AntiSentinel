"""Production candidate retrieval composition before trusted-memory filtering."""

from __future__ import annotations

from antisentinel.memory.hybrid_ranker import ReciprocalRankFusion


class HybridMemoryRetriever:
    def __init__(self, candidate_store, *, vector_memory=None, rrf_k: int = 60) -> None:
        self.candidate_store = candidate_store
        self.vector_memory = vector_memory
        self.fusion = ReciprocalRankFusion(k=rrf_k)
        self.channel_status: dict[str, str] = {}

    def retrieve(self, query, scope, *, limit: int):
        lexical = self.candidate_store.lexical_candidates(query, scope, limit=limit)
        identifiers = self.candidate_store.identifier_candidates(query, scope, limit=limit)
        vectors = self.vector_memory.vector_candidates(query, scope, limit=limit) if self.vector_memory is not None else ()
        vector_status = self.vector_memory.channel_status["vector"] if self.vector_memory is not None else "bypass"
        self.channel_status = {**self.candidate_store.channel_status, "vector": vector_status}
        return self.fusion.combine({"lexical": lexical, "identifier": identifiers, "vector": vectors}, limit=limit)
