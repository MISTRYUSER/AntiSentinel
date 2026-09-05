"""The glue layer between structured model output and domain execution."""

from __future__ import annotations

from dataclasses import dataclass, field
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
from .checkpoint import CheckpointStore, RuntimeSnapshot
from antisentinel.tracing.telemetry import Telemetry, TraceContext

_current_skill_runtime: ContextVar[Any | None] = ContextVar("antisentinel_skill_runtime", default=None)


@dataclass(frozen=True)
class RuntimeConfig:
    max_turns: int = 8
    max_tasks: int = 8
    max_tool_calls: int = 8
    max_total_tool_calls: int = 8

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tasks", "max_tool_calls", "max_total_tool_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
                raise ValueError(f"{name} must be an integer between 1 and 8")


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
        pending_results: list[dict[str, Any]] = list(resume_snapshot.pending_results) if resume_snapshot else []
        completed_invocations = dict(resume_snapshot.completed_invocations) if resume_snapshot else {}
        duplicate_only_turns = 0

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
            turn_scope = ToolExecutionScope(skill_runtime.visible_tool_names()) if skill_runtime is not None else None
            visible_tools = skill_runtime.manifests(registry) if skill_runtime is not None else registry.manifests()
            request = self.context_builder.build(
                incident,
                session,
                turn,
                prior_turns=turns[:-1],
                task_results=pending_results,
                tools=visible_tools,
                memory_context=memory_context_provider(incident, session, turn) if memory_context_provider else None,
                skill_context=skill_runtime.context_payload() if skill_runtime is not None else None,
            )
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
            turn_results: list[dict[str, Any]] = []
            successful_tool_names: list[str] = []
            for task_plan in response.tasks:
                task = Task.create(turn_id=turn.turn_id, task_id=task_plan.task_id, objective=task_plan.objective)
                session_task_summary: dict[str, Any] = {
                    "task_id": task.task_id,
                    "status": "succeeded",
                    "summary": task.objective,
                    "evidence_refs": [],
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
                    cached = completed_invocations.get(invocation_key) if rejected is None else None
                    if rejected is not None:
                        result = rejected
                    elif cached is not None:
                        from antisentinel.tools.manifest import ToolExecutionResult

                        result = ToolExecutionResult(
                            status="rejected",
                            error={"code": "duplicate_tool_call", "message": "this tool call already completed in this run"},
                            result_summary=f"Duplicate tool call. Existing result summary: {cached.get('result_summary') or 'available'}",
                        )
                    else:
                        turn_had_fresh_tool_call = True
                        tool_context = turn_context.child(request_id=f"tool-{tool_call.tool_call_id}") if turn_context else None
                        if tool_context:
                            with self.telemetry.span("tool_call.execute", context=tool_context, tool_name=planned_call.tool_name):
                                result = executor.execute(planned_call.tool_name, planned_call.arguments, scope=turn_scope)
                        else:
                            result = executor.execute(planned_call.tool_name, planned_call.arguments, scope=turn_scope)
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
                        for evidence in [result.evidence] if result.evidence else []:
                            from antisentinel.domain.evidence import EvidenceRef

                            ref = EvidenceRef(evidence_id=evidence.evidence_id, role="tool_result")
                            attempt.add_evidence(ref)
                            evidence_refs.append(ref)
                            session_task_summary["evidence_refs"].append({"evidence_id": ref.evidence_id, "role": ref.role})
                            evidences.append(evidence)
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
                    self._publish(runtime_events, self._event("tool.completed", incident, session, turn, task, tool_call), event_sink)
                    self._publish(runtime_events, self._event("tool_call.result_received", incident, session, turn, task, tool_call), event_sink)
                    if result.status != "succeeded":
                        self._publish(runtime_events, self._event("task.failed", incident, session, turn, task, tool_call), event_sink)
                if task.status.value == "running":
                    task.succeed(session_task_summary["summary"])
                    self._publish(runtime_events, self._event("task.completed", incident, session, turn, task), event_sink)
                task_summaries.append(session_task_summary)
                turn_results.append(task_summaries[-1])
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
            turn.complete(ModelOutputKind.TOOL_CALL, "tool calls completed")
            pending_results = turn_results
            if skill_runtime is not None:
                skill_runtime.note_turn_results(successful_tool_names)
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
