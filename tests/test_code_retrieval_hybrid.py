from antisentinel.retrieval.engine import CodeRetrievalService
from antisentinel.retrieval.fusion import ReciprocalRankFusion
from antisentinel.retrieval.models import CodeSearchHit, CodeSearchScope
from antisentinel.retrieval.vector import VectorSearchHit
from antisentinel.retrieval.graph import GraphExpansion, GraphExpansionResult


def identity(document_id):
    return {"repository_id": "repo", "snapshot_id": "snapshot-a", "published_generation": 1, "commit_sha": "commit-a", "node_id": document_id, "chunk_id": f"chunk-{document_id}", "path": f"src/{document_id}.py", "source_hash": f"hash-{document_id}"}


def keyword_hit(document_id, rank):
    return CodeSearchHit(document_id, float(rank), "keyword", identity(document_id))


def vector_hit(document_id, score):
    return VectorSearchHit(document_id, score, identity(document_id))


class FakeKeyword:
    def __init__(self, hits, ready=True):
        self.hits, self.ready = hits, ready

    def search(self, scope, query, *, limit=30, projection_revision=None):
        return self.hits[:limit]

    def is_ready(self, scope, *, projection_revision=None):
        return self.ready


class FakeVector:
    def __init__(self, hits=None, error=None):
        self.hits, self.error = hits or (), error

    def search(self, vector, scope, **kwargs):
        if self.error:
            raise self.error
        return self.hits


class ReadyChannels:
    def get_channel_status(self, *scope):
        return "ready"


def test_rrf_uses_rank_contributions_and_deduplicates_documents():
    fused = ReciprocalRankFusion(k=60).combine(
        (keyword_hit("a", 1), keyword_hit("b", 2)),
        (vector_hit("b", 0.99), vector_hit("c", 0.98)),
        limit=5,
    )

    assert [item.document_id for item in fused] == ["b", "a", "c"]
    assert fused[0].channels == ("keyword", "vector")
    assert fused[0].ranks == {"keyword": 2, "vector": 1}
    assert fused[0].score == 1 / 62 + 1 / 61


def test_hybrid_returns_keyword_results_with_explicit_degraded_status():
    keyword = FakeKeyword((keyword_hit("a", 1),))
    vector = FakeVector(error=TimeoutError("vector timeout"))
    service = CodeRetrievalService(keyword, vector, channel_store=ReadyChannels(), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")

    result = service.search("timeout", CodeSearchScope("repo", "snapshot-a", 1, "commit-a"), mode="hybrid", query_vector=(1.0, 0.0, 0.0))

    assert [hit.document_id for hit in result.hits] == ["a"]
    assert result.degraded is True
    assert result.error_code == "vector_unavailable"


def test_vector_only_reports_unavailable_instead_of_faking_success():
    service = CodeRetrievalService(FakeKeyword(()), FakeVector(error=ConnectionError("down")), channel_store=ReadyChannels(), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")

    result = service.search("timeout", CodeSearchScope("repo", "snapshot-a", 1, "commit-a"), mode="vector", query_vector=(1.0, 0.0, 0.0))

    assert result.hits == ()
    assert result.degraded is True
    assert result.error_code == "vector_unavailable"


def test_fusion_deduplicates_overlapping_source_ranges():
    first = CodeSearchHit("doc-1", 1.0, "keyword", {**identity("doc-1"), "byte_start": 0, "byte_end": 20})
    overlap = VectorSearchHit("doc-2", 0.9, {**identity("doc-2"), "byte_start": 10, "byte_end": 30})
    separate = VectorSearchHit("doc-3", 0.8, {**identity("doc-3"), "byte_start": 40, "byte_end": 50})
    overlap.source_identity["path"] = first.source_identity["path"]
    overlap.source_identity["source_hash"] = first.source_identity["source_hash"]
    separate.source_identity["path"] = first.source_identity["path"]
    separate.source_identity["source_hash"] = first.source_identity["source_hash"]

    fused = ReciprocalRankFusion().combine((first,), (overlap, separate), limit=5)

    assert [item.document_id for item in fused] == ["doc-1", "doc-3"]
    assert fused[0].channels == ("keyword", "vector")
    assert fused[0].score == 1 / 61 + 1 / 61


def test_non_transient_vector_errors_are_not_mapped_to_unavailable():
    service = CodeRetrievalService(FakeKeyword(()), FakeVector(error=ValueError("schema mismatch")), channel_store=ReadyChannels(), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")

    try:
        service.search("timeout", CodeSearchScope("repo", "snapshot-a", 1, "commit-a"), mode="vector", query_vector=(1.0, 0.0, 0.0))
    except ValueError as error:
        assert str(error) == "schema mismatch"
    else:
        raise AssertionError("schema error was swallowed")


def test_keyword_mode_requires_a_ready_lexical_channel():
    service = CodeRetrievalService(FakeKeyword((keyword_hit("a", 1),), ready=False), None, channel_store=ReadyChannels(), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")

    result = service.search("timeout", CodeSearchScope("repo", "snapshot-a", 1, "commit-a"), mode="keyword")

    assert result.hits == ()
    assert result.degraded is True
    assert result.error_code == "lexical_unavailable"


def test_search_rejects_limits_over_the_planned_budget():
    service = CodeRetrievalService(FakeKeyword(()), None, channel_store=ReadyChannels(), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1")
    scope = CodeSearchScope("repo", "snapshot-a", 1, "commit-a")

    import pytest
    with pytest.raises(ValueError, match="maximum"):
        service.search("timeout", scope, mode="keyword", top_k=6)
    with pytest.raises(ValueError, match="maximum"):
        service.search("timeout", scope, mode="keyword", candidate_limit=31)


def test_single_channel_uses_configured_rrf_k():
    service = CodeRetrievalService(FakeKeyword((keyword_hit("a", 1),)), None, channel_store=ReadyChannels(), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", rrf_k=10)

    result = service.search("timeout", CodeSearchScope("repo", "snapshot-a", 1, "commit-a"), mode="keyword")

    assert result.hits[0].score == 1 / 11


def test_graph_mode_returns_seed_and_expansion_with_degraded_reason():
    class Graph:
        def expand(self, seeds, *, scope, **kwargs):
            assert scope["snapshot_id"] == "snapshot-a"
            return GraphExpansionResult((GraphExpansion("n2", "a", "contains", "outbound", "src/a.py", "child", "seed_rank=1;relation=contains;direction=outbound", "resolved", "ast",source_candidates=(identity('n2'),)),), unresolved_count=1, degraded=True)

    service = CodeRetrievalService(FakeKeyword((keyword_hit("a", 1),)), None, channel_store=ReadyChannels(), model_revision="model-v1", template_revision="template-v1", projection_revision="projection-v1", graph_expander=Graph())
    result = service.search("timeout", CodeSearchScope("repo", "snapshot-a", 1, "commit-a"), mode="graph")

    assert [hit.document_id for hit in result.hits] == ["a", "graph:n2:chunk-n2"]
    assert result.degraded is True
    assert result.error_code == "graph_degraded"
