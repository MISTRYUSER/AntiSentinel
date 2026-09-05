# Memory & Evidence Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a recoverable short-term Session Tree and long-term memory pipeline with Redis hot caching, scoped recall, tracing metrics, and a frontend observability dashboard.

**Architecture:** EventStore and EvidenceStore remain authoritative Incident facts on local append-only storage. Session Tree, Rollout Memory, and Preference Graph are replaceable memory ports; Redis is cache-aside only. Runtime Loop returns a bounded RuntimeResult synchronously while background jobs perform candidate extraction, small-model classification, normalization, entity resolution, merge, and persistence.

**Tech Stack:** Python 3.11+, FastAPI, existing dataclasses/Protocols, JSONL/JSON local adapters, Redis adapter seam with in-memory test double, existing vanilla HTML/CSS/JavaScript frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-memory-evidence-foundation-design.md`

## Global Constraints

- Event facts are append-only under `storage/incidents/{incident_id}/events.jsonl` and are never stored only in process memory or Redis.
- Evidence is immutable; duplicate `evidence_id` is idempotent only when its content hash matches.
- Redis is cache-aside and must be rebuildable from durable sources.
- Memory recall must require an explicit scope and must not leak across operator, incident, or session boundaries.
- Runtime Loop must not wait for asynchronous memory extraction or classification.
- Trace attributes must not contain API keys, full preferences, raw Evidence, or sensitive tool output.
- Every task begins with failing tests and ends with focused tests plus `pytest -q`.

### Task 1: Facts and correlation primitives

**Files:**
- Create: `src/antisentinel/ports/event_store.py`
- Create: `src/antisentinel/ports/evidence_store.py`
- Create: `src/antisentinel/ports/state_store.py`
- Create: `src/antisentinel/persistence/event_store.py`
- Create: `src/antisentinel/persistence/evidence_store.py`
- Create: `src/antisentinel/persistence/state_store.py`
- Modify: `src/antisentinel/domain/event.py`
- Test: `tests/test_persistence.py`

**Interfaces:**
- `EventStore.append(event: Event) -> None`, `list_by_aggregate(aggregate_type: str, aggregate_id: str) -> list[Event]`, `list_by_correlation(correlation_id: str) -> list[Event]`.
- `EvidenceStore.put_once(evidence: Evidence) -> None`, `get(evidence_id: str) -> Evidence | None`.
- `StateStore.save_projection(scope_id: str, state: dict) -> None`, `load_projection(scope_id: str) -> dict | None`, `mark_projection_lag(scope_id: str, error: str) -> None`.
- Correlation value object carries `trace_id`, `request_id`, `parent_request_id`, and `causation_id` without using `span_id` as a business key.

- [x] Write tests for append-only event ordering, aggregate/correlation queries, Evidence hash-idempotence, hash conflict rejection, state projection lag, and JSONL recovery after reopen.
- [x] Run `pytest tests/test_persistence.py -q` and verify the new tests fail because the ports/adapters are absent.
- [x] Implement the smallest file-backed adapters with atomic append/replace boundaries and explicit serialization errors.
- [x] Run `pytest tests/test_persistence.py -q` and verify all focused tests pass.
- [x] Run `pytest -q` and record the complete result: 120 passed, 1 warning.

### Task 2: Session Tree, Digest, and scoped memory views

**Files:**
- Modify: `src/antisentinel/memory/session_tree.py`
- Modify: `src/antisentinel/memory/recall.py`
- Modify: `src/antisentinel/memory/indexer.py`
- Create: `src/antisentinel/memory/models.py`
- Modify: `src/antisentinel/worker/runtime/context.py`
- Test: `tests/test_session_tree.py`
- Test: `tests/test_memory_recall.py`

**Interfaces:**
- `SessionTimelineTree.append(node: TimelineNode) -> None`, `children(session_id: str, parent_node_id: str | None) -> list[TimelineNode]`, `rebuild(session_id: str, events: list[Event]) -> None`.
- `SessionDigest.build(tree: SessionTimelineTree, token_budget: int) -> DigestView`.
- `MemoryRecall.recall(scope: MemoryScope, query: str, token_budget: int) -> MemoryContextView`.
- `ContextBuilder.build(..., memory_context: MemoryContextView | None = None) -> ModelRequest`.

- [x] Write tests proving Incident→Session→Turn→Task→ToolCall→Attempt nodes retain `parent_node_id`, parallel tasks branch, replay equals incremental update, and digest excludes raw tool output.
- [x] Write tests proving recall requires scope and cannot return another operator's preference or another incident's temporary hypothesis.
- [x] Run `pytest tests/test_session_tree.py tests/test_memory_recall.py -q` and verify failure.
- [x] Implement node models, bounded timeline reads, replay, digest truncation, and ContextBuilder injection of only digest/ref/summary views.
- [x] Run focused tests, then `pytest -q`; focused 4 passed, full regression 124 passed, 1 warning, and cumulative real Case passed.

### Task 3: Rollout Memory, Preference Graph, Redis cache-aside

**Files:**
- Modify: `src/antisentinel/ports/cache.py`
- Create: `src/antisentinel/ports/memory_store.py`
- Modify: `src/antisentinel/memory/operator_graph.py`
- Modify: `src/antisentinel/memory/recorder.py`
- Create: `src/antisentinel/adapters/cache/in_memory.py`
- Create: `src/antisentinel/adapters/cache/redis.py`
- Create: `src/antisentinel/persistence/memory_store.py`
- Test: `tests/test_memory_store.py`
- Test: `tests/test_operator_graph.py`

**Interfaces:**
- `MemoryCache.get(key: str, version: int | None = None) -> CacheEntry | None`, `set(key: str, value: dict, ttl_seconds: int, version: int) -> None`, `delete(key: str) -> None`.
- `MemoryDurableStore.append(record: MemoryRecord) -> None`, `list_by_operator(operator_id: str) -> list[MemoryRecord]`, `list_rollout(rollout_id: str) -> RolloutMemory | None`.
- `PreferenceGraph.upsert(candidate: PreferenceCandidate) -> MergeResult`.
- `MemoryRecorder.record_runtime_action(action: RuntimeMemoryAction) -> None`.

- [ ] Write tests for durable append/replay, namespace separation, Redis hit/miss/fallback, TTL metadata, version rejection, and concurrent rebuild single-flight behavior using the in-memory cache double.
- [ ] Write tests for preference edges, common error codes, source IDs, confidence, and operator isolation.
- [ ] Run focused tests and verify failure.
- [ ] Implement cache-aside adapters and versioned memory envelopes; keep Redis dependency optional until production wiring.
- [ ] Run focused tests, then `pytest -q`.

### Task 4: Async Candidate pipeline and small-model classifier

**Files:**
- Create: `src/antisentinel/memory/candidates.py`
- Create: `src/antisentinel/memory/extractor.py`
- Create: `src/antisentinel/memory/classifier.py`
- Create: `src/antisentinel/memory/resolver.py`
- Create: `src/antisentinel/memory/jobs.py`
- Modify: `src/antisentinel/memory/recorder.py`
- Modify: `src/antisentinel/worker/runtime/loop.py`
- Test: `tests/test_memory_pipeline.py`
- Test: `tests/test_runtime_memory_boundary.py`

**Interfaces:**
- `CandidateExtractor.extract(source: CandidateSource) -> list[MemoryCandidate]`.
- `MemoryClassifier.classify(candidate: MemoryCandidate) -> MemoryClassification` with `should_remember`, `memory_type`, `confidence`, `ttl_policy`, and `reason_code`.
- `EntityResolver.resolve(candidate: MemoryCandidate, operator_id: str) -> EntityResolution`.
- `MemoryJobQueue.enqueue(job: MemoryJob) -> str`, `claim() -> MemoryJob | None`, `ack(job_id: str) -> None`, `retry(job_id: str, error: str) -> None`.

- [ ] Write tests proving user input, successful Tool result, Agent conclusion, and Session completion create candidates while low-signal lifecycle events do not.
- [ ] Write tests proving Loop returns RuntimeResult immediately and remains successful when classifier times out or returns malformed data.
- [ ] Write tests for exact alias resolution, uncertain candidates, conflict audit records, retries, duplicate job idempotence, and dead-letter transitions.
- [ ] Run focused tests and verify failure.
- [ ] Implement bounded candidate extraction, strict classifier parsing, deterministic normalization, merge policy, and background queue orchestration.
- [ ] Run focused tests, then `pytest -q`.

### Task 5: Tracing metrics API and frontend observability dashboard

**Files:**
- Modify: `src/antisentinel/tracing/__init__.py`
- Create: `src/antisentinel/tracing/memory_metrics.py`
- Create: `src/antisentinel/api/observability.py`
- Modify: `src/antisentinel/api/app.py`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Test: `tests/test_observability_api.py`
- Test: `tests/test_dashboard_surface.py`

**Interfaces:**
- `MemorySpanRecorder.record_lookup(...) -> None`, `record_context_build(...) -> None`, `record_pipeline_stage(...) -> None`.
- `MetricsQuery.summary(window: TimeWindow, filters: MetricsFilters) -> MetricsSnapshot`.
- `TraceQuery.get(trace_id: str) -> TraceView` with redacted attributes only.
- HTTP: `GET /api/observability/memory`, `GET /api/observability/traces/{trace_id}`, and dashboard route `/dashboard`.

- [ ] Write API tests for time-window and memory-type/cache-layer filters, incomplete-data flags, trace correlation, and redaction of secrets/raw evidence.
- [ ] Write frontend tests for cache, context, quality, and audit sections plus empty/loading/error states.
- [ ] Run focused tests and verify failure.
- [ ] Implement metric aggregation from recorded spans and read-only FastAPI endpoints.
- [ ] Implement the dashboard with four sections: cache, context tokens, memory quality, and trace/job audit; render missing data as unavailable rather than zero.
- [ ] Run focused tests, then `pytest -q`.
- [ ] Verify manually with `uvicorn` and a fake runtime: create one diagnosis, inspect `/dashboard`, and confirm the trace path shows Incident/Session/Turn/Task/ToolCall/Attempt plus async memory stages.
