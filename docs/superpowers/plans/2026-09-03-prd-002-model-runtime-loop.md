# PRD-002 Model Runtime Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-neutral, in-memory runtime that completes the PRD-002 model → multi-task → multi-tool → evidence summary → final diagnosis loop for one Session.

**Architecture:** Keep model contracts in `ports/model.py`, tool contracts in `tools/`, context/message construction in `worker/runtime/context.py` and `messages.py`, and orchestration in `worker/runtime/loop.py` and `engine.py`. The engine uses existing domain transitions and pending Events, while `InMemoryCheckpointStore` persists JSON snapshots and `FakeModel`/deterministic tools exercise the path without external SDKs.

**Tech Stack:** Python 3.11+, dataclasses, typing.Protocol, stdlib JSON/time utilities, pytest. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-prd-002-model-runtime-loop-design.md`

## Global Constraints

- Keep Python requirement `>=3.11,<4`.
- Do not add a real LLM Provider or external SDK.
- Preserve PRD-001 domain APIs and keep the existing 58 tests passing.
- Execute Tasks and ToolCalls sequentially; do not add parallelism, retries, streaming, retrieval, or approval UI.
- Never place raw Evidence content or raw tool result payloads in model context; expose only summaries and references.
- Validate model output and tool arguments before invoking any tool handler.
- Use stable error codes: `max_turns_exceeded`, `max_tasks_exceeded`, and `max_tool_calls_exceeded`.

## File map

- Modify `src/antisentinel/ports/model.py`: model request/response dataclasses, parser, port, and provider-neutral errors.
- Modify `src/antisentinel/tools/manifest.py`: tool definition and normalized execution result types.
- Modify `src/antisentinel/tools/registry.py`: exact-name registry and JSON argument validation.
- Modify `src/antisentinel/worker/execution/result.py`: execution result compatibility export if needed.
- Modify `src/antisentinel/worker/execution/tool_executor.py`: registry-backed executor with rejection and approval outcomes.
- Modify `src/antisentinel/worker/runtime/messages.py`: stable JSON-safe model messages and task result messages.
- Modify `src/antisentinel/worker/runtime/context.py`: redacted Incident/Session/Turn/Task/Evidence context builder.
- Modify `src/antisentinel/worker/runtime/checkpoint.py`: snapshot type, Protocol, and in-memory store.
- Modify `src/antisentinel/worker/runtime/loop.py`: one-session orchestration, domain transitions, event correlation, and result aggregation.
- Modify `src/antisentinel/worker/runtime/engine.py`: public configuration and `RuntimeEngine.run` API.
- Modify `src/antisentinel/worker/handler.py`: small worker-facing adapter around RuntimeEngine.
- Create `tests/test_model_port.py`: model contract/parser tests.
- Create `tests/test_tools.py`: registry and executor tests.
- Create `tests/test_runtime_context.py`: redaction/message tests.
- Create `tests/test_checkpoint.py`: snapshot round-trip and in-memory store tests.
- Create `tests/test_runtime_loop.py`: all PRD-002 runtime integration tests.

## Task 1: Define the model contract and strict parser

**Files:**
- Modify: `src/antisentinel/ports/model.py`
- Create: `tests/test_model_port.py`

**Interfaces:**
- Produces `ModelPort.complete(request: ModelRequest) -> ModelResponse`.
- Produces `ModelRequest(incident_id: str, session_id: str, turn_id: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]])`.
- Produces `ModelResponse.final: FinalDiagnosis | None`, `ModelResponse.tasks: tuple[TaskPlan, ...]`, and `ModelResponse.raw_summary: str`.
- Produces `FinalDiagnosis(summary: str, diagnosis: str, confidence: float, evidence_refs: tuple[EvidenceRef, ...])`.
- Produces `TaskPlan(task_id: str, objective: str, tool_calls: tuple[PlannedToolCall, ...])` and `PlannedToolCall(tool_name: str, arguments: dict[str, Any], target_ref: str | None)`.
- Produces `parse_model_response(value: object, *, max_tasks: int = 8, max_tool_calls: int = 8) -> ModelResponse`.

- [ ] **Step 1: Write failing parser tests** for valid final output, valid multiple tasks, missing branch, malformed arguments, duplicate task IDs, and limit violations.
- [ ] **Step 2: Run `pytest tests/test_model_port.py -q`** and confirm failures because the contract is empty.
- [ ] **Step 3: Implement dataclasses with `__post_init__` validation**, deep-copied JSON-safe arguments, bounded raw summaries, and a strict parser that accepts exactly one `final` or `tasks` branch.
- [ ] **Step 4: Add `ModelError` and `ModelTimeoutError`, and make the Protocol synchronous** so FakeModel can be a simple test double.
- [ ] **Step 5: Run `pytest tests/test_model_port.py -q`** and confirm all model contract tests pass.

## Task 2: Add tool definitions, registry, and executor

**Files:**
- Modify: `src/antisentinel/tools/manifest.py`
- Modify: `src/antisentinel/tools/registry.py`
- Modify: `src/antisentinel/worker/execution/result.py`
- Modify: `src/antisentinel/worker/execution/tool_executor.py`
- Create: `tests/test_tools.py`

**Interfaces:**
- Produces `ToolDefinition(name: str, description: str, argument_schema: dict[str, Any], handler: Callable[[dict[str, Any]], Any], requires_approval: bool = False, read_only: bool = True)`.
- Produces `ToolRegistry.register(definition)`, `ToolRegistry.resolve(name)`, and `ToolRegistry.manifests() -> list[dict[str, Any]]`.
- Produces `ToolExecutionResult(status: Literal["succeeded", "failed", "rejected", "waiting_approval"], result: Any = None, result_summary: str | None = None, evidence: Evidence | None = None, error: dict[str, Any] | None = None)`.
- Produces `ToolExecutor.execute(tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult`.

- [ ] **Step 1: Write failing tests** for exact-name lookup, required/type/schema validation, unknown tools, approval-required tools, successful handler calls, and handler exceptions.
- [ ] **Step 2: Run `pytest tests/test_tools.py -q`** and confirm failures.
- [ ] **Step 3: Implement registry and a deliberately small JSON-schema validator** supporting object properties, required keys, primitive types, and `additionalProperties` when specified.
- [ ] **Step 4: Implement executor normalization** so rejected/approval paths never invoke handlers and handler exceptions become structured failures.
- [ ] **Step 5: Run `pytest tests/test_tools.py -q`** and confirm all tests pass.

## Task 3: Build stable messages and redacted context

**Files:**
- Modify: `src/antisentinel/worker/runtime/messages.py`
- Modify: `src/antisentinel/worker/runtime/context.py`
- Create: `tests/test_runtime_context.py`

**Interfaces:**
- Produces `build_system_message() -> dict[str, str]` and `build_task_results_message(results: list[dict[str, Any]]) -> dict[str, Any]`.
- Produces `ContextBuilder.build(incident: Incident, session: Session, turn: Turn, *, prior_turns: list[Turn], task_results: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelRequest`.

- [ ] **Step 1: Write failing tests** asserting Incident/Session metadata is present, task summaries and EvidenceRef IDs are present, and raw Evidence content/raw result payloads are absent.
- [ ] **Step 2: Run `pytest tests/test_runtime_context.py -q`** and confirm failures.
- [ ] **Step 3: Implement stable dictionary messages** with JSON-safe values and deterministic ordering.
- [ ] **Step 4: Implement `ContextBuilder`** to include only IDs, statuses, summaries, tool manifests, and Evidence metadata/reference summaries.
- [ ] **Step 5: Run `pytest tests/test_runtime_context.py -q`** and confirm redaction tests pass.

## Task 4: Implement checkpoint snapshots

**Files:**
- Modify: `src/antisentinel/worker/runtime/checkpoint.py`
- Create: `tests/test_checkpoint.py`

**Interfaces:**
- Produces `RuntimeSnapshot(session_id: str, incident: dict[str, Any], session: dict[str, Any], turns: list[dict[str, Any]], tasks: list[dict[str, Any]], tool_calls: list[dict[str, Any]], attempts: list[dict[str, Any]], messages: list[dict[str, Any]], turn_count: int, current_task_index: int | None, current_tool_call_index: int | None, successful_tool_call_ids: tuple[str, ...], last_error: dict[str, Any] | None)`.
- Produces `CheckpointStore.save(snapshot: RuntimeSnapshot)`, `load(session_id: str) -> RuntimeSnapshot | None`, and `clear(session_id: str)`.
- Produces `InMemoryCheckpointStore` with defensive copies and JSON round-trip behavior.

- [ ] **Step 1: Write failing tests** for save/load/clear, snapshot JSON serialization, and preservation of current position/error/successful IDs.
- [ ] **Step 2: Run `pytest tests/test_checkpoint.py -q`** and confirm failures.
- [ ] **Step 3: Implement snapshot `to_dict/from_dict`** and the in-memory store using deep copies so callers cannot mutate stored state.
- [ ] **Step 4: Run `pytest tests/test_checkpoint.py -q`** and confirm checkpoint tests pass.

## Task 5: Implement the runtime loop and result aggregation

**Files:**
- Modify: `src/antisentinel/worker/runtime/loop.py`
- Modify: `src/antisentinel/worker/runtime/engine.py`
- Modify: `src/antisentinel/worker/handler.py`
- Create: `tests/test_runtime_loop.py`

**Interfaces:**
- Produces `RuntimeConfig(max_turns: int = 8, max_tasks: int = 8, max_tool_calls: int = 8)` with positive values no greater than 8.
- Produces `RuntimeResult(status: Literal["completed", "failed", "waiting_approval"], incident_id: str, session_id: str, turn_count: int, final: FinalDiagnosis | None, evidence_refs: tuple[EvidenceRef, ...], task_summaries: tuple[dict[str, Any], ...], error: dict[str, Any] | None, events: tuple[Event, ...])`.
- Produces `RuntimeEngine.run(incident: Incident, session: Session, model: ModelPort, *, registry: ToolRegistry, checkpoint_store: CheckpointStore | None = None, config: RuntimeConfig | None = None, resume: bool = False) -> RuntimeResult`.
- Produces `WorkerHandler.handle(...) -> RuntimeResult` as a thin delegation adapter.

- [ ] **Step 1: Write failing tests** for final completion, multi-task turn, multi-tool task, model-tool-model-final, invalid model output, tool failure, approval pause, max turns, and model timeout.
- [ ] **Step 2: Run the focused runtime tests** and confirm failures.
- [ ] **Step 3: Implement engine configuration validation and turn creation**, using `Turn.create/start/complete/fail`, `Session.complete/fail/wait`, and `Task`/`ToolCall`/`Attempt` transitions already provided by PRD-001.
- [ ] **Step 4: Implement strict model call handling**: emit `model.called`, call `ModelPort`, parse/validate output before tools, emit `model.responded`, and fail with a bounded raw summary on invalid output or exceptions.
- [ ] **Step 5: Implement ordered Task and ToolCall execution**, creating Attempt records, attaching `EvidenceRef` from successful tool Evidence, converting tool failures to model-visible summaries, and emitting correlated runtime Events with all available IDs.
- [ ] **Step 6: Implement approval pause and limit failures** with `waiting_approval`, `max_turns_exceeded`, `max_tasks_exceeded`, and `max_tool_calls_exceeded` results.
- [ ] **Step 7: Save checkpoints before/after model calls and tool calls**, including domain dictionaries and current execution position.
- [ ] **Step 8: Run `pytest tests/test_runtime_loop.py -q`** and confirm all focused loop tests pass.

## Task 6: Add resume behavior and end-to-end evidence path

**Files:**
- Modify: `src/antisentinel/worker/runtime/loop.py`
- Modify: `src/antisentinel/worker/runtime/engine.py`
- Modify: `tests/test_runtime_loop.py`

**Interfaces:**
- Resume consumes `RuntimeEngine.run(..., resume=True)` and the same `CheckpointStore`.
- A persisted successful Attempt is identified by `AttemptStatus.SUCCEEDED`; its ToolCall ID is included in `successful_tool_call_ids` and must not invoke the handler again.

- [ ] **Step 1: Write a failing resume test** that interrupts after a successful tool result, reruns with `resume=True`, and asserts the handler invocation count remains one.
- [ ] **Step 2: Run the resume test** and confirm it fails by duplicating the handler call or lacking resume support.
- [ ] **Step 3: Restore domain objects from the snapshot**, skip only successful ToolCalls, and surface interrupted in-progress work as a structured execution error rather than replaying it blindly.
- [ ] **Step 4: Add the full end-to-end test** asserting model request 2 contains EvidenceRef and result summary but not raw Evidence content, then returns a final diagnosis with the same EvidenceRef.
- [ ] **Step 5: Run the focused runtime suite** and confirm resume and end-to-end tests pass.

## Task 7: Add metrics and public exports

**Files:**
- Modify: `src/antisentinel/worker/runtime/engine.py`
- Modify: `src/antisentinel/worker/runtime/__init__.py`
- Modify: `src/antisentinel/ports/__init__.py`
- Modify: `src/antisentinel/tools/__init__.py`
- Modify: `tests/test_runtime_loop.py`

**Interfaces:**
- Produces `RuntimeMetrics` with counters for model calls/successes, tool calls/successes, turns, tasks, max-turn failures, and final answers with evidence.
- `RuntimeEngine.run` accepts an optional metrics collector and records elapsed durations with a monotonic clock.

- [ ] **Step 1: Write failing metric assertions** for model/tool counts, turn/task counts, max-turn count, and final evidence count.
- [ ] **Step 2: Implement the in-memory collector** and instrument success/failure paths without changing result semantics.
- [ ] **Step 3: Export the stable public classes from package `__init__.py` files** without importing optional dependencies.
- [ ] **Step 4: Run `pytest tests/test_runtime_loop.py -q`** and confirm metrics tests pass.

## Task 8: Full verification and handoff

**Files:**
- Modify only files required by preceding tasks.

- [ ] **Step 1: Run the complete suite:** `pytest -q`.
- [ ] **Step 2: Confirm the expected baseline:** all original 58 tests remain green, plus the new PRD-002 tests.
- [ ] **Step 3: Run a JSON round-trip smoke test** for `ModelRequest`, `ModelResponse`, `RuntimeSnapshot`, and `RuntimeResult`.
- [ ] **Step 4: Review the diff for raw Evidence leakage, missing correlated IDs, accidental retries, and calls made after invalid model output.**
- [ ] **Step 5: If a Git repository is added, create focused commits per task; in the current workspace report that commits were not possible because `.git` is absent.

## Coverage checklist

- FakeModel final completion: Task 5.
- Multiple Tasks in one Turn: Task 5.
- Multiple ToolCalls in one Task: Task 5.
- Model → tools → model → final loop: Tasks 5 and 6.
- EvidenceRef-only context: Task 3 and Task 6.
- Invalid model output with no tool execution: Task 1 and Task 5.
- Tool failure, approval, timeout, and max turns: Task 5.
- Checkpoint recovery without duplicate successful ToolCall: Tasks 4 and 6.
- Correlated domain IDs and required Events: Task 5.
- Metrics: Task 7.
