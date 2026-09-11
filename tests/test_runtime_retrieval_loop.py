"""Full-chain RuntimeEngine tests: search → read_evidence → source_context → final."""

from __future__ import annotations

import json
from types import SimpleNamespace

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.retrieval.evidence import CodeEvidenceAssembler
from antisentinel.retrieval.fusion import FusedCodeSearchHit
from antisentinel.retrieval.engine import RetrievalResult
from antisentinel.retrieval.tools import build_code_retrieval_tools, compact_search_payload
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine
from tests.test_retrieval_graph_boundaries import setup_source


class StubSearchService:
    def __init__(self, hits: tuple[FusedCodeSearchHit, ...], *, mode: str = "hybrid", degraded: bool = False):
        self.hits = hits
        self.mode = mode
        self.degraded = degraded
        self.calls: list[dict] = []

    def search(self, query, scope, *, mode="hybrid", query_vector=None, top_k=5, candidate_limit=30):
        self.calls.append(
            {
                "query": query,
                "mode": mode,
                "scope": scope,
                "top_k": top_k,
                "candidate_limit": candidate_limit,
            }
        )
        return RetrievalResult(
            self.hits[:top_k],
            mode,
            {"keyword": "ready", "vector": "unavailable" if self.degraded else "ready"},
            degraded=self.degraded,
            error_code="vector_unavailable" if self.degraded else None,
        )


class SearchThenReadModel:
    """Turn 1: search. Turn 2: read_evidence from hits. Turn 3+: final from source_context."""

    def __init__(self):
        self.requests = []
        self.search_calls = 0
        self.read_calls = 0
        self.phase = "search"

    def complete(self, request):
        self.requests.append(request)
        if self.phase == "search":
            self.phase = "read"
            self.search_calls += 1
            return {
                "tasks": [
                    {
                        "task_id": "search",
                        "objective": "locate code",
                        "tool_calls": [
                            {
                                "tool_name": "code_retrieval.search",
                                "arguments": {"query": "Service", "mode": "hybrid"},
                            }
                        ],
                    }
                ]
            }
        if self.phase == "read":
            self.phase = "final"
            self.read_calls += 1
            hits = []
            for message in request.messages:
                # Working-set path: role=tool task_results summaries, or structured working_set turns.
                blobs: list[str] = []
                if message.get("role") == "tool":
                    for item in message.get("task_results") or []:
                        raw = item.get("summary") or item.get("result_summary") or ""
                        if isinstance(raw, str):
                            blobs.append(raw)
                recent = message.get("recent_turns") or (message.get("working_set") or {}).get("recent_turns") or []
                for turn in recent:
                    for event in turn.get("tool_events") or []:
                        raw = event.get("result_summary") or ""
                        if isinstance(raw, str):
                            blobs.append(raw)
                for raw in blobs:
                    if "hits" not in raw:
                        continue
                    start = raw.find("{")
                    if start < 0:
                        continue
                    try:
                        payload = json.loads(raw[start:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("hits"):
                        hits = payload["hits"]
                        break
                if hits:
                    break
            candidates = []
            for hit in hits:
                identity = hit.get("source_identity") if isinstance(hit.get("source_identity"), dict) else hit
                candidates.append(
                    {
                        "repository_id": identity.get("repository_id"),
                        "snapshot_id": identity.get("snapshot_id"),
                        "published_generation": identity.get("published_generation"),
                        "commit_sha": identity.get("commit_sha"),
                        "chunk_id": identity.get("chunk_id"),
                        "source_hash": identity.get("source_hash"),
                        "path": identity.get("path"),
                        "node_id": identity.get("node_id"),
                    }
                )
            assert candidates, "expected compact search hits in tool task_results summary"
            return {
                "tasks": [
                    {
                        "task_id": "read",
                        "objective": "read evidence",
                        "tool_calls": [
                            {
                                "tool_name": "code_retrieval.read_evidence",
                                "arguments": {"candidates": candidates[:1]},
                            }
                        ],
                    }
                ]
            }
        slices = [s for m in request.messages if m.get("role") == "source_context" for s in m.get("slices", [])]
        return {
            "final": {
                "summary": "verified",
                "diagnosis": "source inspected",
                "confidence": 1.0,
                "evidence_refs": [{"evidence_id": s["evidence_id"]} for s in slices],
            }
        }


def _hit_from_chunk(scope: dict, chunk: dict, document_id: str = "doc-1") -> FusedCodeSearchHit:
    return FusedCodeSearchHit(
        document_id=document_id,
        score=1.0,
        channels=("keyword",),
        ranks={"keyword": 1},
        source_identity={
            **scope,
            "node_id": chunk.get("node_id") or "node",
            "chunk_id": chunk["chunk_id"],
            "path": chunk["path"],
            "source_hash": chunk["content_hash"],
            "byte_start": chunk.get("byte_start"),
            "byte_end": chunk.get("byte_end"),
        },
    )


def test_search_to_evidence_enters_source_context_and_final(tmp_path):
    store, scope, _, chunk, source = setup_source(tmp_path)
    incident = Incident.create(title="retrieval-loop", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker"])
    store.bind_incident(str(incident.incident_id), scope["repository_id"], scope["snapshot_id"])
    hit = _hit_from_chunk(scope, chunk)
    service = StubSearchService((hit,))
    registry = ToolRegistry(auto_discover=False)
    for tool in build_code_retrieval_tools(
        service,
        lambda _: {**scope, "incident_id": str(incident.incident_id)},
        evidence_assembler=CodeEvidenceAssembler(source),
    ):
        registry.register(tool)
    model = SearchThenReadModel()
    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry,
        config=RuntimeConfig(max_turns=4, enable_working_set=True),
    )
    assert result.status == "completed"
    assert service.calls and service.calls[0]["mode"] == "hybrid"
    assert model.search_calls == 1 and model.read_calls == 1
    assert len(store.database.query("SELECT * FROM evidence")) == 1
    assert result.final is not None
    assert len(result.final.evidence_refs) == 1
    # Compact summary must appear in a prior tool message path (working set / task results).
    joined = json.dumps([req.messages for req in model.requests], ensure_ascii=False)
    assert "channel_statuses" in joined or "hit_count" in joined or "document_id" in joined
    source_turns = [m for req in model.requests for m in req.messages if m.get("role") == "source_context"]
    assert source_turns
    assert all(s.get("content") for turn in source_turns for s in turn.get("slices", []))


def test_search_summary_is_compact_on_tool_result(tmp_path):
    store, scope, _, chunk, source = setup_source(tmp_path)
    hit = _hit_from_chunk(scope, chunk)
    # Inject oversized identity text that must not land in summary.
    hit = FusedCodeSearchHit(
        hit.document_id,
        hit.score,
        hit.channels,
        hit.ranks,
        {**hit.source_identity, "text": "BODY" * 2000},
    )
    service = StubSearchService((hit,), degraded=True)
    tools = build_code_retrieval_tools(
        service,
        lambda _: {**scope, "incident_id": "incident-a"},
        evidence_assembler=CodeEvidenceAssembler(source),
    )
    search = tools[0]
    outcome = search.handler({"query": "Service", "mode": "hybrid"})
    assert outcome.status == "succeeded"
    assert "BODY" in json.dumps(outcome.result)
    summary = json.loads(outcome.result_summary)
    assert summary["degraded"] is True
    assert summary["error_code"] == "vector_unavailable"
    assert "BODY" not in outcome.result_summary
    assert "text" not in summary["hits"][0]


class ReadTwiceModel:
    def __init__(self, candidate: dict):
        self.candidate = candidate
        self.requests = []
        self.reads = 0

    def complete(self, request):
        self.requests.append(request)
        if self.reads < 2:
            self.reads += 1
            return {
                "tasks": [
                    {
                        "task_id": f"read-{self.reads}",
                        "objective": "read",
                        "tool_calls": [
                            {
                                "tool_name": "code_retrieval.read_evidence",
                                "arguments": {"candidates": [self.candidate]},
                            }
                        ],
                    }
                ]
            }
        slices = [s for m in request.messages if m.get("role") == "source_context" for s in m.get("slices", [])]
        return {
            "final": {
                "summary": "done",
                "diagnosis": "ok",
                "confidence": 1.0,
                "evidence_refs": [{"evidence_id": s["evidence_id"]} for s in slices],
            }
        }


def test_repeated_read_evidence_reuses_pinned_sources(tmp_path):
    store, scope, _, chunk, source = setup_source(tmp_path)
    incident = Incident.create(title="reuse-pin", source="test")
    session = Session.create(incident_id=incident.incident_id, participant_ids=["worker"])
    store.bind_incident(str(incident.incident_id), scope["repository_id"], scope["snapshot_id"])
    candidate = {
        **scope,
        "chunk_id": chunk["chunk_id"],
        "source_hash": chunk["content_hash"],
        "path": chunk["path"],
        "node_id": chunk.get("node_id"),
    }
    registry = ToolRegistry(auto_discover=False)
    for tool in build_code_retrieval_tools(
        SimpleNamespace(search=lambda *a, **k: None),
        lambda _: {**scope, "incident_id": str(incident.incident_id)},
        evidence_assembler=CodeEvidenceAssembler(source),
    ):
        registry.register(tool)
    model = ReadTwiceModel(candidate)
    # sticky bodies so first pin remains available for second-turn reuse match
    result = RuntimeEngine().run(
        incident,
        session,
        model,
        registry=registry,
        config=RuntimeConfig(max_turns=4, enable_working_set=True, source_pin_policy="sticky_bodies", source_body_ttl_turns=8),
    )
    assert result.status == "completed"
    assert model.reads == 2
    # First read persists evidence once; second should reuse pin without a second write.
    assert len(store.database.query("SELECT * FROM evidence")) == 1
    joined = json.dumps([req.messages for req in model.requests], ensure_ascii=False)
    assert "reused" in joined or any(
        "reused" in (item.get("summary") or "")
        for req in model.requests
        for message in req.messages
        for item in (message.get("task_results") or [])
    ) or any(
        "reused" in str(getattr(summary, "get", lambda *_: None)("summary") if isinstance(summary, dict) else summary)
        for summary in result.task_summaries
    )
    # Prefer explicit check on task summaries from the run.
    assert any(
        "reused" in str(item.get("summary") or "")
        or any("reused" in str(tr.get("summary") or "") for tr in item.get("tool_results") or [])
        for item in result.task_summaries
    )
