"""The glue layer between structured model output and domain execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from time import monotonic
from typing import Any, Callable, Literal
from contextvars import ContextVar

from antisentinel.domain.attempt import Attempt
from antisentinel.domain.event import Event
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.task import Task
from antisentinel.domain.tool_call import ToolCall
from antisentinel.domain.turn import ModelOutputKind, Turn
from antisentinel.ports.model import (
    FinalDiagnosis,
    ModelError,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelTimeoutError,
    parse_model_response,
    TokenUsage,
)
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.execution.tool_executor import ToolExecutor
from antisentinel.worker.execution.tool_executor import ToolExecutionScope

from .context import ContextBuilder
from .checkpoint import CheckpointStore, RuntimeSnapshot, rebuild_working_set
from .messages import summarize_plan, summarize_tool_result, summarize_turn_outcome
from .working_set import ToolEvent, TurnRecord, create_empty
from antisentinel.tracing.telemetry import Telemetry, TraceContext

_current_skill_runtime: ContextVar[Any | None] = ContextVar("antisentinel_skill_runtime", default=None)


@dataclass(frozen=True)
class RuntimeConfig:
    max_turns: int = 8
    max_tasks: int = 8
    max_tool_calls: int = 8
    max_total_tool_calls: int = 8
    recent_turn_limit: int = 3
    # Phase A comparison switch: False restores pre-Working-Set "last turn only" continuity.
    enable_working_set: bool = True
    max_older_digest_tokens: int = 2048
    # 0 = auto-resolve from model context window; >0 = explicit override.
    max_context_tokens: int = 0
    model_context_window: int | None = None
    max_output_tokens_reserve: int = 4096
    context_input_ratio: float = 0.85
    context_strict: bool = True
    min_recent_turns: int = 1
    source_window_radius: int = 40
    tools_min_share: float = 0.08
    tools_core_limit: int = 8
    target_fill_ratio: float = 0.65
    # CodeMap-first: ephemeral bodies (TTL packs) vs sticky_bodies (legacy full re-pin).
    source_pin_policy: str = "ephemeral"
    source_body_ttl_turns: int = 1
    max_source_pins: int = 8

    def __post_init__(self) -> None:
        for name in ("max_tasks", "max_tool_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
                raise ValueError(f"{name} must be an integer between 1 and 8")
        if isinstance(self.max_turns, bool) or not isinstance(self.max_turns, int) or not 1 <= self.max_turns <= 32:
            raise ValueError("max_turns must be an integer between 1 and 32")
        if isinstance(self.max_total_tool_calls, bool) or not isinstance(self.max_total_tool_calls, int) or not 1 <= self.max_total_tool_calls <= 64:
            raise ValueError("max_total_tool_calls must be an integer between 1 and 64")
        if (
            isinstance(self.recent_turn_limit, bool)
            or not isinstance(self.recent_turn_limit, int)
            or not 1 <= self.recent_turn_limit <= 32
        ):
            raise ValueError("recent_turn_limit must be an integer between 1 and 32")
        if not isinstance(self.enable_working_set, bool):
            raise ValueError("enable_working_set must be a boolean")
        if (
            isinstance(self.max_older_digest_tokens, bool)
            or not isinstance(self.max_older_digest_tokens, int)
            or self.max_older_digest_tokens < 0
        ):
            raise ValueError("max_older_digest_tokens must be a non-negative integer")
        if (
            isinstance(self.max_context_tokens, bool)
            or not isinstance(self.max_context_tokens, int)
            or self.max_context_tokens < 0
        ):
            raise ValueError("max_context_tokens must be a non-negative integer (0 = auto from model)")
        if self.model_context_window is not None and (
            isinstance(self.model_context_window, bool)
            or not isinstance(self.model_context_window, int)
            or self.model_context_window < 1
        ):
            raise ValueError("model_context_window must be a positive integer when set")
        if (
            isinstance(self.max_output_tokens_reserve, bool)
            or not isinstance(self.max_output_tokens_reserve, int)
            or self.max_output_tokens_reserve < 0
        ):
            raise ValueError("max_output_tokens_reserve must be a non-negative integer")
        if not isinstance(self.context_input_ratio, (int, float)) or isinstance(self.context_input_ratio, bool):
            raise ValueError("context_input_ratio must be a float")
        if not 0.0 < float(self.context_input_ratio) <= 1.0:
            raise ValueError("context_input_ratio must be in (0, 1]")
        if not isinstance(self.context_strict, bool):
            raise ValueError("context_strict must be a boolean")
        if (
            isinstance(self.min_recent_turns, bool)
            or not isinstance(self.min_recent_turns, int)
            or self.min_recent_turns < 1
        ):
            raise ValueError("min_recent_turns must be >= 1")
        if self.min_recent_turns > self.recent_turn_limit:
            raise ValueError("min_recent_turns cannot exceed recent_turn_limit")
        if (
            isinstance(self.source_window_radius, bool)
            or not isinstance(self.source_window_radius, int)
            or self.source_window_radius < 1
        ):
            raise ValueError("source_window_radius must be a positive integer")
        if not 0 <= self.tools_min_share < 1:
            raise ValueError("tools_min_share must be in [0, 1)")
        if (
            isinstance(self.tools_core_limit, bool)
            or not isinstance(self.tools_core_limit, int)
            or self.tools_core_limit < 0
        ):
            raise ValueError("tools_core_limit must be a non-negative integer")
        if not isinstance(self.target_fill_ratio, (int, float)) or isinstance(self.target_fill_ratio, bool):
            raise ValueError("target_fill_ratio must be a float")
        if not 0.0 < float(self.target_fill_ratio) <= 1.0:
            raise ValueError("target_fill_ratio must be in (0, 1]")
        if self.source_pin_policy not in {"ephemeral", "sticky_bodies"}:
            raise ValueError("source_pin_policy must be 'ephemeral' or 'sticky_bodies'")
        if (
            isinstance(self.source_body_ttl_turns, bool)
            or not isinstance(self.source_body_ttl_turns, int)
            or self.source_body_ttl_turns < 0
        ):
            raise ValueError("source_body_ttl_turns must be a non-negative integer")
        if (
            isinstance(self.max_source_pins, bool)
            or not isinstance(self.max_source_pins, int)
            or self.max_source_pins < 1
        ):
            raise ValueError("max_source_pins must be a positive integer")

    def resolved_max_context_tokens(self, *, model: str | None = None) -> int:
        from .model_profile import resolve_max_context_tokens

        return resolve_max_context_tokens(
            model=model,
            override=self.max_context_tokens if self.max_context_tokens > 0 else None,
            context_window=self.model_context_window,
            max_output_tokens=self.max_output_tokens_reserve,
            input_ratio=float(self.context_input_ratio),
        )

    def context_budget(self, *, model: str | None = None):
        from .budget import ContextBudget, default_budget

        max_tokens = self.resolved_max_context_tokens(model=model)
        budget = default_budget(max_context_tokens=max_tokens)
        return ContextBudget(
            max_context_tokens=max_tokens,
            recent_turn_limit=self.recent_turn_limit,
            min_recent_turns=self.min_recent_turns,
            strict=self.context_strict,
            source_window_radius=self.source_window_radius,
            source_window_by_suffix=budget.source_window_by_suffix,
            max_older_digest_tokens=self.max_older_digest_tokens,
            tools_min_share=self.tools_min_share,
            tools_core_limit=self.tools_core_limit,
            target_fill_ratio=float(self.target_fill_ratio),
            source_pin_policy=self.source_pin_policy,
            source_body_ttl_turns=self.source_body_ttl_turns,
            max_source_pins=self.max_source_pins,
            max_source_slices=min(4, self.max_source_pins),
        )

@dataclass(frozen=True)
class RuntimeResult:
    status: Literal["completed", "failed", "waiting_approval"]
    incident_id: str
    session_id: str
    turn_count: int
    final: FinalDiagnosis | None
    evidence_refs: tuple[Any, ...]
    task_summaries: tuple[dict[str, Any], ...]
    error: dict[str, Any] | None
    events: tuple[Event, ...]
    turns: tuple[Turn, ...] = ()
    tasks: tuple[Task, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    evidences: tuple[Any, ...] = ()
    trace_id: str | None = None
    token_usage: TokenUsage = TokenUsage()
    skill_usage: dict[str, Any] | None = None


@dataclass
class RuntimeMetrics:
    counters: dict[str, int] = field(default_factory=dict)
    durations_ms: dict[str, list[float]] = field(default_factory=dict)

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def observe(self, name: str, duration_ms: float) -> None:
        self.durations_ms.setdefault(name, []).append(duration_ms)


class RuntimeLoop:
    def __init__(self, *, context_builder: ContextBuilder | None = None, telemetry: Telemetry | None = None) -> None:
        self.context_builder = context_builder or ContextBuilder()
        self.telemetry = telemetry or Telemetry(service_name="antisentinel.runtime.loop")

    def run(
        self,
        incident: Incident,
        session: Session,
        model: ModelPort,
        *,
        registry: ToolRegistry,
        config: RuntimeConfig,
        metrics: RuntimeMetrics | None = None,
        checkpoint_store: CheckpointStore | None = None,
        resume_snapshot: RuntimeSnapshot | None = None,
        event_sink=None,
        trace_context: TraceContext | None = None,
        observability_metrics=None,
        memory_context_provider: Callable[[Incident, Session, Turn], Any] | None = None,
        source_context_rehydrator: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
        skill_runtime=None,
    ) -> RuntimeResult:
        metrics = metrics or RuntimeMetrics()
        _current_skill_runtime.set(skill_runtime)
        if skill_runtime is not None:
            registry = skill_runtime.bind_registry(registry)
        executor = ToolExecutor(registry)
        turns: list[Turn] = [Turn.from_dict(item) for item in resume_snapshot.turns] if resume_snapshot else []
        tasks: list[Task] = [Task.from_dict(item) for item in resume_snapshot.tasks] if resume_snapshot else []
        tool_calls: list[ToolCall] = [ToolCall.from_dict(item) for item in resume_snapshot.tool_calls] if resume_snapshot else []
        attempts: list[Attempt] = [Attempt.from_dict(item) for item in resume_snapshot.attempts] if resume_snapshot else []
        task_summaries: list[dict[str, Any]] = []
        runtime_events: list[Event] = []
        evidence_refs: list[Any] = [ref for attempt in attempts for ref in attempt.evidence_refs]
        evidences: list[Any] = []
        token_usage = TokenUsage()
        final: FinalDiagnosis | None = None
        use_working_set = config.enable_working_set
        if use_working_set and resume_snapshot is not None and resume_snapshot.working_set is not None:
            working_set = rebuild_working_set(resume_snapshot, recent_turn_limit=config.recent_turn_limit)
            working_set.max_older_digest_tokens = config.max_older_digest_tokens
            working_set.compact_older(config.max_older_digest_tokens)
        else:
            working_set = create_empty(
                recent_turn_limit=config.recent_turn_limit,
                max_older_digest_tokens=config.max_older_digest_tokens,
            )
        if use_working_set:
            pending_results: list[dict[str, Any]] = working_set.export_task_results()
            if not pending_results and resume_snapshot is not None:
                pending_results = list(resume_snapshot.pending_results)
        else:
            pending_results = list(resume_snapshot.pending_results) if resume_snapshot else []
        from .source_pins import (
            decay_packed_bodies,
            materialize_for_pack,
            pin_from_slice,
            pins_from_refs,
            refs_from_pins,
            upsert_pin,
        )

        source_pins: list[Any] = []
        if resume_snapshot and resume_snapshot.source_context_refs:
            if config.source_pin_policy == "sticky_bodies" and source_context_rehydrator is not None:
                hydrated = source_context_rehydrator(resume_snapshot.source_context_refs)
                source_pins = [
                    pin_from_slice(item, body_ttl=max(1, config.source_body_ttl_turns))
                    for item in hydrated
                ]
            else:
                # CodeMap-first default: resume as pointers only (re-call read_source for bodies).
                source_pins = pins_from_refs(
                    resume_snapshot.source_context_refs,
                    incident_id=str(incident.incident_id),
                    body_ttl=0,
                )
        completed_invocations = dict(resume_snapshot.completed_invocations) if resume_snapshot else {}
        duplicate_only_turns = 0
        model_name = getattr(model, "model", None)
        context_budget = config.context_budget(model=model_name if isinstance(model_name, str) else None)
        try:
            from .messages import build_system_message

            override = getattr(self.context_builder, "system_override", None)
            context_budget.preflight_system(override if override is not None else build_system_message()["content"])
        except ValueError as exc:
            bootstrap = Turn.create(session_id=session.session_id)
            return self._fail(
                incident,
                session,
                bootstrap,
                [bootstrap],
                tasks,
                tool_calls,
                attempts,
                runtime_events,
                {"code": "context_system_over_budget", "message": str(exc)[:2000]},
                task_summaries,
                evidence_refs,
                failure_event="context.preflight_failed",
                token_usage=token_usage,
            )
        last_compacted_turns = working_set.compacted_turns if use_working_set else 0

        for turn_number in range(1, config.max_turns + 1):
            turn_had_tool_call = False
            turn_had_fresh_tool_call = False
            turn = Turn.create(session_id=session.session_id)
            turn_context = trace_context.child(turn_id=turn.turn_id, request_id=f"turn-{turn.turn_id}") if trace_context else None
            session.add_turn(turn.turn_id)
            turns.append(turn)
            metrics.increment("turns")
            turn.start()
            self._publish(runtime_events, self._event("turn.started", incident, session, turn), event_sink)
            turn_scope = (
                skill_runtime.execution_scope()
                if skill_runtime is not None and hasattr(skill_runtime, "execution_scope")
                else ToolExecutionScope(skill_runtime.visible_tool_names()) if skill_runtime is not None else None
            )
            visible_tools = skill_runtime.manifests(registry) if skill_runtime is not None else registry.manifests()
            budget_holder: list[Any] = []

            def _on_budget_report(report, _holder=budget_holder) -> None:
                _holder.append(report)

            try:
                pack_slices = materialize_for_pack(source_pins)
                request = self.context_builder.build(
                    incident,
                    session,
                    turn,
                    prior_turns=turns[:-1],
                    task_results=pending_results,
                    tools=visible_tools,
                    memory_context=memory_context_provider(incident, session, turn) if memory_context_provider else None,
                    skill_context=skill_runtime.context_payload() if skill_runtime is not None else None,
                    source_context=pack_slices,
                    working_set=working_set if use_working_set else None,
                    budget=context_budget,
                    on_budget_report=_on_budget_report,
                )
                decay_packed_bodies(source_pins, pack_slices)
            except ValueError as exc:
                message = str(exc)
                code = "context_pack_exceeded" if "context_pack_exceeded" in message else "context_build_failed"
                return self._fail(
                    incident,
                    session,
                    turn,
                    turns,
                    tasks,
                    tool_calls,
                    attempts,
                    runtime_events,
                    {"code": code, "message": message[:2000]},
                    task_summaries,
                    evidence_refs,
                    failure_event="context.pack_failed",
                    token_usage=token_usage,
                )
            if budget_holder:
                report = budget_holder[-1]
                self._publish(
                    runtime_events,
                    self._event("context.built", incident, session, turn, extra=report.to_event_payload()),
                    event_sink,
                )
                if observability_metrics is not None and hasattr(observability_metrics, "observe_context_budget"):
                    observability_metrics.observe_context_budget(report)
                if use_working_set and report.compacted_turns > last_compacted_turns:
                    self._publish(
                        runtime_events,
                        self._event(
                            "context.compacted",
                            incident,
                            session,
                            turn,
                            extra={
                                "compacted_turns": report.compacted_turns,
                                "recent_turn_count": report.recent_turn_count,
                            },
                        ),
                        event_sink,
                    )
                    last_compacted_turns = report.compacted_turns
            self._publish(runtime_events, self._event("model.started", incident, session, turn), event_sink)
            self._publish(runtime_events, self._event("model.called", incident, session, turn), event_sink)
            metrics.increment("model_calls")
            model_started = monotonic()
            try:
                if turn_context:
                    with self.telemetry.span("model.complete", context=turn_context) as model_span:
                        response = model.complete(request)
                        response = response if isinstance(response, ModelResponse) else parse_model_response(
                            response,
                            max_tasks=config.max_tasks,
                            max_tool_calls=config.max_tool_calls,
                        )
                        token_usage = token_usage.add(response.usage)
                        if observability_metrics is not None:
                            observability_metrics.observe_tokens(response.usage, model=getattr(model, "model", "unknown"))
                        for key, value in {
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                            "cached_input_tokens": response.usage.cached_input_tokens,
                            "reasoning_tokens": response.usage.reasoning_tokens,
                            "tool_tokens": response.usage.tool_tokens,
                        }.items():
                            model_span.set_attribute(key, value)
                else:
                    response = model.complete(request)
                    response = response if isinstance(response, ModelResponse) else parse_model_response(
                        response,
                        max_tasks=config.max_tasks,
                        max_tool_calls=config.max_tool_calls,
                    )
                    token_usage = token_usage.add(response.usage)
                    if observability_metrics is not None:
                        observability_metrics.observe_tokens(response.usage, model=getattr(model, "model", "unknown"))
            except ModelTimeoutError as exc:
                metrics.observe("model_latency_ms", (monotonic() - model_started) * 1000)
                metrics.increment("model_failures")
                return self._fail(
                    incident, session, turn, turns, tasks, tool_calls, attempts, runtime_events,
                    {"code": "model_timeout", "message": str(exc)},
                    task_summaries, evidence_refs,
                )
            except ModelError as exc:
                metrics.observe("model_latency_ms", (monotonic() - model_started) * 1000)
                metrics.increment("model_failures")
                return self._fail(
                    incident, session, turn, turns, tasks, tool_calls, attempts, runtime_events,
                    {"code": str(exc).split(":", 1)[0], "message": str(exc)[:2000]},
                    task_summaries, evidence_refs,
                )
            except Exception as exc:  # noqa: BLE001 - runtime boundary normalizes unexpected failures
                metrics.observe("model_latency_ms", (monotonic() - model_started) * 1000)
                metrics.increment("model_failures")
                return self._fail(
                    incident, session, turn, turns, tasks, tool_calls, attempts, runtime_events,
                    {"code": "invalid_model_output", "message": str(exc)[:2000]},
                    task_summaries, evidence_refs,
                )

            self._publish(runtime_events, self._event("model.completed", incident, session, turn), event_sink)
            self._publish(runtime_events, self._event("model.responded", incident, session, turn), event_sink)
            metrics.observe("model_latency_ms", (monotonic() - model_started) * 1000)
            metrics.increment("model_successes")
            if response.final is not None:
                final = response.final
                evidence_refs.extend(final.evidence_refs)
                if final.evidence_refs:
                    metrics.increment("final_answers_with_evidence")
                turn.complete(ModelOutputKind.FINAL_ANSWER, final.summary)
                session.complete(final.summary)
                return self._result(
                    "completed", incident, session, turns, tasks, tool_calls, attempts,
                    runtime_events, task_summaries, evidence_refs, final, None,
                    evidences,
                    token_usage,
                )

            if len(response.tasks) > config.max_tasks:
                return self._fail(
                    incident, session, turn, turns, tasks, tool_calls, attempts, runtime_events,
                    {"code": "max_tasks_exceeded", "message": "model returned too many tasks"},
                    task_summaries, evidence_refs,
                )
            turn_tool_events: list[ToolEvent] = []
            turn_results: list[dict[str, Any]] = []
            successful_tool_names: list[str] = []
            plan_summary = summarize_plan(response.tasks)
            for task_plan in response.tasks:
                task = Task.create(turn_id=turn.turn_id, task_id=task_plan.task_id, objective=task_plan.objective)
                session_task_summary: dict[str, Any] = {
                    "task_id": task.task_id,
                    "status": "succeeded",
                    "summary": task.objective,
                    "evidence_refs": [],
                    "tool_results": [],
                }
                turn.add_task(task.task_id)
                task.start()
                tasks.append(task)
                metrics.increment("tasks")
                if len(task_plan.tool_calls) > config.max_tool_calls:
                    return self._fail(
                        incident, session, turn, turns, tasks, tool_calls, attempts, runtime_events,
                        {"code": "max_tool_calls_exceeded", "message": "task returned too many tool calls"},
                        task_summaries, evidence_refs,
                    )
                for planned_call in task_plan.tool_calls:
                    if len(tool_calls) >= config.max_total_tool_calls:
                        return self._fail(
                            incident, session, turn, turns, tasks, tool_calls, attempts, runtime_events,
                            {"code": "max_total_tool_calls_exceeded", "message": "session reached total tool call budget"},
                            task_summaries, evidence_refs, token_usage=token_usage,
                        )
                    turn_had_tool_call = True
                    tool_call = ToolCall.create(
                        task_id=task.task_id,
                        tool_name=planned_call.tool_name,
                        arguments=planned_call.arguments,
                        target=planned_call.target_ref,
                    )
                    task.add_tool_call(tool_call.tool_call_id)
                    tool_calls.append(tool_call)
                    tool_call.start()
                    metrics.increment("tool_calls")
                    invocation_key = _invocation_key(planned_call.tool_name, planned_call.arguments, planned_call.target_ref)
                    tool_started = monotonic()
                    rejected = executor.preflight(planned_call.tool_name, planned_call.arguments, scope=turn_scope)
                    execution_arguments = planned_call.arguments
                    if rejected is None and planned_call.tool_name == 'code_retrieval.read_evidence':
                        packed_now = materialize_for_pack(source_pins)
                        remaining_fragments = max(0, 4 - len(source_pins))
                        remaining_bytes = 32 * 1024 - sum(len(s.content.encode('utf-8')) for s in packed_now)
                        if remaining_fragments <= 0 or remaining_bytes <= 0:
                            from antisentinel.tools.manifest import ToolExecutionResult
                            rejected = ToolExecutionResult(status='rejected', error={'code': 'source_budget_exceeded', 'message': 'source context budget exhausted'})
                        else:
                            execution_arguments = {**planned_call.arguments,
                                'max_fragments': min(remaining_fragments, planned_call.arguments.get('max_fragments', 4)),
                                'max_bytes': min(remaining_bytes, planned_call.arguments.get('max_bytes', 32 * 1024))}
                    cached = completed_invocations.get(invocation_key) if rejected is None else None
                    if rejected is not None:
                        result = rejected
                    elif cached is not None:
                        from antisentinel.tools.manifest import ToolExecutionResult

                        result = ToolExecutionResult(
                            status="rejected",
                            error={"code": "duplicate_tool_call", "message": "this tool call already completed in this run"},
                            result_summary="Duplicate tool call. Reuse the matching result already present in the tool-result history.",
                        )
                    else:
                        turn_had_fresh_tool_call = True
                        tool_context = turn_context.child(request_id=f"tool-{tool_call.tool_call_id}") if turn_context else None
                        if tool_context:
                            with self.telemetry.span("tool_call.execute", context=tool_context, tool_name=planned_call.tool_name):
                                result = executor.execute(planned_call.tool_name, execution_arguments, scope=turn_scope)
                        else:
                            result = executor.execute(planned_call.tool_name, execution_arguments, scope=turn_scope)
                    metrics.observe("tool_latency_ms", (monotonic() - tool_started) * 1000)
                    if skill_runtime is not None and planned_call.tool_name in {"skill.load", "skill.read_reference"}:
                        if planned_call.tool_name == "skill.load":
                            skill_event = "skill.loaded" if result.status == "succeeded" else "skill.load_failed"
                        else:
                            skill_event = "skill.reference_read" if result.status == "succeeded" else "skill.load_failed"
                        usage = skill_runtime.usage()
                        attributes = {
                            "skill_id": usage.get("selected_skill_id") or str(planned_call.arguments.get("skill_id", "")),
                            "skill_version": usage.get("selected_skill_version") or "",
                            "release_id": usage.get("release_id") or "",
                            "reference_id": str(planned_call.arguments.get("reference_id", "")),
                            "success": result.status == "succeeded",
                        }
                        with self.telemetry.span(skill_event, context=turn_context, **attributes):
                            pass
                        self._publish(
                            runtime_events,
                            self._event(skill_event, incident, session, turn, task, tool_call, extra=attributes),
                            event_sink,
                        )
                    if result.status == "waiting_approval":
                        tool_call.deny("approval_required")
                        task.wait_for_approval()
                        turn.wait_for_tool()
                        session.wait()
                        self._publish(runtime_events, self._event("tool.completed", incident, session, turn, task, tool_call), event_sink)
                        self._publish(runtime_events, self._event("tool_call.result_received", incident, session, turn, task, tool_call), event_sink)
                        return self._result(
                            "waiting_approval", incident, session, turns, tasks, tool_calls, attempts,
                            runtime_events, task_summaries, evidence_refs, None, result.error,
                        )
                    attempt = Attempt.create(tool_call_id=tool_call.tool_call_id)
                    tool_call.add_attempt(attempt.attempt_id)
                    attempt.start()
                    attempts.append(attempt)
                    if result.status == "succeeded":
                        successful_tool_names.append(planned_call.tool_name)
                        metrics.increment("tool_successes")
                        for evidence in ((result.evidence,) if result.evidence else ()) + result.evidences:
                            from antisentinel.domain.evidence import EvidenceRef

                            ref = EvidenceRef(evidence_id=evidence.evidence_id, role="tool_result")
                            attempt.add_evidence(ref)
                            evidence_refs.append(ref)
                            session_task_summary["evidence_refs"].append({"evidence_id": ref.evidence_id, "role": ref.role})
                            evidences.append(evidence)
                        if planned_call.tool_name == 'code_retrieval.read_evidence' and isinstance(result.result, dict):
                            from antisentinel.code_map.source_context import SourceContextSlice
                            ttl = (
                                max(1, config.source_body_ttl_turns)
                                if config.source_pin_policy == "ephemeral"
                                else max(config.source_body_ttl_turns, 32)
                            )
                            if config.source_pin_policy == "sticky_bodies":
                                ttl = max(ttl, 64)
                            for raw in result.result.get('slices', []):
                                matching = next((e for e in result.evidences if str(e.evidence_id) == raw.get('evidence_id')), None)
                                if matching is None or matching.content_hash != raw.get('content_hash'):
                                    raise ValueError('source_evidence_mismatch')
                                slice_obj = raw if isinstance(raw, SourceContextSlice) else SourceContextSlice(**raw)
                                source_pins = upsert_pin(
                                    source_pins,
                                    pin_from_slice(slice_obj, body_ttl=ttl),
                                    max_pins=config.max_source_pins,
                                )
                        if planned_call.tool_name == "code_map.read_source" and isinstance(result.result, dict):
                            raw_source = result.result.get("source_context")
                            if raw_source and result.evidence is not None and raw_source.get("evidence_id") == str(result.evidence.evidence_id):
                                from antisentinel.code_map.source_context import SourceContextSlice

                                slice_obj = SourceContextSlice(**raw_source)
                                ttl = (
                                    max(1, config.source_body_ttl_turns)
                                    if config.source_pin_policy == "ephemeral"
                                    else max(config.source_body_ttl_turns, 32)
                                )
                                if config.source_pin_policy == "sticky_bodies":
                                    ttl = max(ttl, 64)
                                source_pins = upsert_pin(
                                    source_pins,
                                    pin_from_slice(slice_obj, body_ttl=ttl),
                                    max_pins=config.max_source_pins,
                                )
                        attempt.succeed(
                            result=result.result,
                            result_summary=result.result_summary,
                        )
                        tool_call.succeed()
                        completed_invocations[invocation_key] = {
                            "result": result.result,
                            "result_summary": result.result_summary,
                        }
                        session_task_summary["summary"] = result.result_summary or session_task_summary["summary"]
                    else:
                        error = result.error or {"code": "tool_failed", "message": "tool execution failed"}
                        attempt.fail(error)
                        tool_call.fail(error)
                        if task.status.value == "running":
                            task.fail(error)
                        session_task_summary.update(status="failed", summary=result.result_summary or error["message"])
                    session_task_summary["tool_results"].append({
                        "tool_name": planned_call.tool_name,
                        "arguments": planned_call.arguments,
                        "status": result.status,
                        "summary": result.result_summary,
                        "error": result.error,
                    })
                    event_summary = summarize_tool_result(
                        tool_name=planned_call.tool_name,
                        status=result.status,
                        result_summary=result.result_summary,
                        error=result.error,
                    )
                    turn_tool_events.append(
                        ToolEvent(
                            tool_name=planned_call.tool_name,
                            args_fingerprint=_args_fingerprint(planned_call.arguments),
                            status=result.status,
                            result_summary=event_summary,
                            evidence_refs=[str(ref.evidence_id) for ref in attempt.evidence_refs],
                        )
                    )
                    self._publish(runtime_events, self._event("tool.completed", incident, session, turn, task, tool_call), event_sink)
                    self._publish(runtime_events, self._event("tool_call.result_received", incident, session, turn, task, tool_call), event_sink)
                    if result.status != "succeeded":
                        self._publish(runtime_events, self._event("task.failed", incident, session, turn, task, tool_call), event_sink)
                if task.status.value == "running":
                    task.succeed(session_task_summary["summary"])
                    self._publish(runtime_events, self._event("task.completed", incident, session, turn, task), event_sink)
                task_summaries.append(session_task_summary)
                turn_results.append(session_task_summary)
            if turn_had_tool_call and not turn_had_fresh_tool_call:
                duplicate_only_turns += 1
                if duplicate_only_turns >= 2:
                    return self._fail(
                        incident, session, turn, turns, tasks, tool_calls, attempts, runtime_events,
                        {"code": "model_non_convergent", "message": "model repeated only duplicate tool calls"},
                        task_summaries, evidence_refs, failure_event="runtime.non_convergent",
                        token_usage=token_usage,
                    )
            else:
                duplicate_only_turns = 0
            if use_working_set:
                outcome = summarize_turn_outcome(turn_tool_events, plan_summary=plan_summary)
                turn.complete(ModelOutputKind.TOOL_CALL, outcome)
                working_set.append_turn(
                    TurnRecord(
                        turn_index=turn_number,
                        plan_summary=plan_summary,
                        tool_events=turn_tool_events,
                        outcome=outcome,
                    )
                )
                if source_pins:
                    working_set.update_sticky(
                        source_slice_refs=refs_from_pins(source_pins, max_pins=config.max_source_pins)
                    )
                pending_results = working_set.export_task_results()
            else:
                # Legacy Phase-A-OFF behavior: overwrite with this turn only; hollow turn summary.
                turn.complete(ModelOutputKind.TOOL_CALL, "tool calls completed")
                pending_results = turn_results
            if skill_runtime is not None:
                skill_runtime.note_turn_results(successful_tool_names)
                if use_working_set:
                    usage = skill_runtime.usage()
                    skill_id = usage.get("selected_skill_id")
                    if skill_id:
                        working_set.update_sticky(
                            active_skill={
                                "id": str(skill_id),
                                "version": str(usage.get("selected_skill_version") or ""),
                            }
                        )
            if checkpoint_store is not None:
                checkpoint_store.save(RuntimeSnapshot(
                    session_id=session.session_id,
                    incident=incident.to_dict(),
                    session=session.to_dict(),
                    turns=[item.to_dict() for item in turns],
                    tasks=[item.to_dict() for item in tasks],
                    tool_calls=[item.to_dict() for item in tool_calls],
                    attempts=[item.to_dict() for item in attempts],
                    messages=request.messages,
                    source_context_refs=refs_from_pins(source_pins, max_pins=config.max_source_pins),
                    turn_count=len(turns),
                    current_task_index=None,
                    current_tool_call_index=None,
                    successful_tool_call_ids=tuple(
                        item.tool_call_id for item in tool_calls if item.status.value == "succeeded"
                    ),
                    last_error=None,
                    pending_results=pending_results,
                    completed_invocations=completed_invocations,
                    skill_state=skill_runtime.snapshot() if skill_runtime is not None else None,
                    working_set=working_set.to_dict() if use_working_set else None,
                ))

        metrics.increment("max_turn_failures")
        return self._fail(
            incident, session, turns[-1], turns, tasks, tool_calls, attempts, runtime_events,
            {"code": "max_turns_exceeded", "message": "session reached max_turns"},
            task_summaries, evidence_refs, failure_event="runtime.max_turns_exceeded", token_usage=token_usage,
        )

    def _publish(self, events, event, event_sink) -> None:
        events.append(event)
        if event_sink is not None:
            event_sink(event)

    def _event(self, event_type, incident, session, turn, task=None, tool_call=None, extra=None) -> Event:
        related = {"incident_id": str(incident.incident_id), "session_id": str(session.session_id), "turn_id": str(turn.turn_id)}
        if task is not None:
            related["task_id"] = str(task.task_id)
        if tool_call is not None:
            related["tool_call_id"] = str(tool_call.tool_call_id)
        return Event.create(
            type=event_type,
            aggregate_type="Runtime",
            aggregate_id=session.session_id,
            correlation_id=session.session_id,
            occurred_at=turn.updated_at,
            payload={**related, **(extra or {})},
            related_ids=related,
        )

    def _fail(
        self, incident, session, turn, turns, tasks, tool_calls, attempts, events, error,
        task_summaries=None, evidence_refs=None, failure_event="model.failed", token_usage=None,
    ):
        if turn.status.value in {"running", "waiting_tool"}:
            turn.fail(error)
        if session.status.value in {"active", "waiting"}:
            session.fail(error)
        events.append(self._event(failure_event, incident, session, turn))
        return self._result(
            "failed", incident, session, turns, tasks, tool_calls, attempts, events,
            task_summaries or [], evidence_refs or [], None, error,
            token_usage=token_usage,
        )

    def _result(self, status, incident, session, turns, tasks, tool_calls, attempts, runtime_events, task_summaries, evidence_refs, final, error, evidences=None, token_usage=None):
        domain_events = []
        for aggregate in [incident, session, *turns, *tasks, *tool_calls, *attempts]:
            domain_events.extend(aggregate.pending_events)
        return RuntimeResult(
            status=status,
            incident_id=incident.incident_id,
            session_id=session.session_id,
            turn_count=len(turns),
            final=final,
            evidence_refs=tuple(evidence_refs),
            task_summaries=tuple(task_summaries),
            error=error,
            events=tuple(domain_events + runtime_events),
            turns=tuple(turns),
            tasks=tuple(tasks),
            tool_calls=tuple(tool_calls),
            attempts=tuple(attempts),
            evidences=tuple(evidences or ()),
            token_usage=token_usage or TokenUsage(),
            skill_usage=_current_skill_runtime.get().usage() if _current_skill_runtime.get() is not None else None,
        )


def _invocation_key(tool_name: str, arguments: dict[str, Any], target_ref: str | None = None) -> str:
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return f"{tool_name}:{target_ref or ''}:{encoded}"


def _args_fingerprint(arguments: dict[str, Any]) -> str:
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # Short stable fingerprint without leaking large argument bodies into Working Set.
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
