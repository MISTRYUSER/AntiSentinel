from antisentinel.memory.hybrid_ranker import ReciprocalRankFusion, RetrievalCandidate


def test_rrf_deduplicates_memory_and_rewards_multi_channel_agreement():
    merged = ReciprocalRankFusion().combine({
        "lexical": (RetrievalCandidate("m-shared", "lexical", 1, 3.0), RetrievalCandidate("m-lexical", "lexical", 2, 2.0)),
        "identifier": (RetrievalCandidate("m-shared", "identifier", 1, 1.0),),
        "vector": (RetrievalCandidate("m-vector", "vector", 1, 0.9),),
    }, limit=5)

    assert [item.memory_id for item in merged] == ["m-shared", "m-vector", "m-lexical"]
    assert merged[0].channels == ("identifier", "lexical")
    assert merged[0].rrf_score > merged[1].rrf_score


def test_rrf_ignores_empty_channels_and_uses_stable_memory_id_tie_breaking():
    merged = ReciprocalRankFusion().combine({
        "lexical": (),
        "identifier": (RetrievalCandidate("m-b", "identifier", 1, 1.0),),
        "vector": (RetrievalCandidate("m-a", "vector", 1, 1.0),),
    }, limit=5)

    assert [item.memory_id for item in merged] == ["m-a", "m-b"]


def test_hybrid_retriever_includes_vector_channel_when_configured():
    from antisentinel.memory.models import AuthorizedMemoryScope
    from antisentinel.memory.retrieval import HybridMemoryRetriever

    class Store:
        channel_status = {"lexical": "active", "identifier": "active"}
        def lexical_candidates(self, *_args, **_kwargs): return ()
        def identifier_candidates(self, *_args, **_kwargs): return ()
    class Vector:
        channel_status = {"vector": "active"}
        def vector_candidates(self, *_args, **_kwargs): return (RetrievalCandidate("vector-hit", "vector", 1, .9),)

    retriever = HybridMemoryRetriever(Store(), vector_memory=Vector())
    result = retriever.retrieve("q", AuthorizedMemoryScope("op", "inc", "s"), limit=5)

    assert [item.memory_id for item in result] == ["vector-hit"]
    assert retriever.channel_status["vector"] == "active"
