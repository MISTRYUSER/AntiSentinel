"""Unit tests for compact code_retrieval.search summaries."""

from __future__ import annotations

import json
from types import SimpleNamespace

from antisentinel.retrieval.fusion import FusedCodeSearchHit
from antisentinel.retrieval.tools import _SEARCH_SUMMARY_MAX_BYTES, compact_search_payload


def _hit(document_id: str, *, path: str = "a.py", chunk_id: str = "c1", score: float = 0.5) -> FusedCodeSearchHit:
    return FusedCodeSearchHit(
        document_id=document_id,
        score=score,
        channels=("keyword", "vector"),
        ranks={"keyword": 1, "vector": 2},
        source_identity={
            "repository_id": "repo-a",
            "snapshot_id": "snap-a",
            "published_generation": 1,
            "commit_sha": "a" * 40,
            "node_id": "n1",
            "chunk_id": chunk_id,
            "path": path,
            "source_hash": "hash-" + document_id,
            "text": "x" * 5000,
        },
    )


def test_compact_search_payload_keeps_status_and_identity_not_full_text():
    result = SimpleNamespace(
        hits=(_hit("d1"),),
        mode="hybrid",
        channel_statuses={"keyword": "ready", "vector": "ready"},
        degraded=False,
        error_code=None,
        truncated=False,
        incomplete=False,
    )
    payload = compact_search_payload(result)
    assert payload["mode"] == "hybrid"
    assert payload["channel_statuses"]["keyword"] == "ready"
    assert payload["degraded"] is False
    assert payload["hit_count"] == 1
    hit = payload["hits"][0]
    assert hit["document_id"] == "d1"
    assert hit["path"] == "a.py"
    assert hit["chunk_id"] == "c1"
    assert hit["channels"] == ["keyword", "vector"]
    assert "text" not in hit
    assert "source_identity" not in hit


def test_compact_search_payload_preserves_degraded_status():
    result = SimpleNamespace(
        hits=(),
        mode="hybrid",
        channel_statuses={"keyword": "ready", "vector": "unavailable"},
        degraded=True,
        error_code="vector_unavailable",
        truncated=False,
        incomplete=False,
    )
    payload = compact_search_payload(result)
    assert payload["degraded"] is True
    assert payload["error_code"] == "vector_unavailable"
    assert payload["channel_statuses"]["vector"] == "unavailable"


def test_compact_search_payload_truncates_under_byte_budget():
    hits = tuple(_hit(f"d{i}", path=f"path/{i}/" + ("z" * 80), chunk_id=f"c{i}", score=1.0 / (i + 1)) for i in range(40))
    result = SimpleNamespace(
        hits=hits,
        mode="hybrid",
        channel_statuses={"keyword": "ready", "vector": "ready"},
        degraded=False,
        error_code=None,
        truncated=False,
        incomplete=False,
    )
    payload = compact_search_payload(result, max_bytes=800)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert len(encoded.encode("utf-8")) <= 800
    assert payload["summary_truncated"] is True
    assert payload["hit_count"] == 40
    assert len(payload["hits"]) < 40


def test_compact_default_budget_fits_small_result():
    result = SimpleNamespace(
        hits=(_hit("d1"), _hit("d2", chunk_id="c2")),
        mode="keyword",
        channel_statuses={"keyword": "ready"},
        degraded=False,
        error_code=None,
        truncated=False,
        incomplete=False,
    )
    payload = compact_search_payload(result)
    assert "summary_truncated" not in payload
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= _SEARCH_SUMMARY_MAX_BYTES
