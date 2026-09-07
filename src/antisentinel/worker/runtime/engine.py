"""Public entry point for the model runtime."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.ports.model import ModelPort
from antisentinel.tools.registry import ToolRegistry

from .checkpoint import CheckpointStore
from .loop import RuntimeConfig, RuntimeLoop, RuntimeMetrics, RuntimeResult
from antisentinel.tracing.telemetry import Telemetry, TraceContext


class RuntimeEngine:
    def __init__(self, *, loop: RuntimeLoop | None = None, telemetry: Telemetry | None = None) -> None:
        self.telemetry = telemetry or Telemetry(service_name="antisentinel.runtime")
        self.loop = loop or RuntimeLoop(telemetry=self.telemetry)

    def run(
        self,
        incident: Incident,
        session: Session,
        model: ModelPort,
        *,
        registry: ToolRegistry,
        checkpoint_store: CheckpointStore | None = None,
        config: RuntimeConfig | None = None,
        resume: bool = False,
        metrics: RuntimeMetrics | None = None,
        event_sink: Callable[[object], None] | None = None,
        observability_metrics=None,
        memory_context_provider: Callable[[Incident, Session, Any], Any] | None = None,
        source_context_rehydrator: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
        skill_runtime=None,
    ) -> RuntimeResult:
        resume_snapshot = None
        if resume and checkpoint_store is not None:
            resume_snapshot = checkpoint_store.load(session.session_id)
            if resume_snapshot is not None:
                restored_incident = Incident.from_dict(resume_snapshot.incident)
                restored_session = Session.from_dict(resume_snapshot.session)
                incident.__dict__.update(restored_incident.__dict__)
                session.__dict__.update(restored_session.__dict__)
                if skill_runtime is not None and resume_snapshot.skill_state is not None:
                    skill_runtime.restore(resume_snapshot.skill_state)
        trace_context = TraceContext.new(session_id=session.session_id, request_id=f"session-{session.session_id}")
        with self.telemetry.span("session.run", context=trace_context):
            result = self.loop.run(
                incident, session, model, registry=registry, config=config or RuntimeConfig(), metrics=metrics,
                checkpoint_store=checkpoint_store, resume_snapshot=resume_snapshot, event_sink=event_sink,
                trace_context=trace_context,
                observability_metrics=observability_metrics,
                memory_context_provider=memory_context_provider,
                source_context_rehydrator=source_context_rehydrator,
                skill_runtime=skill_runtime,
            )
            return replace(result, trace_id=trace_context.trace_id)
