"""Worker-facing adapter for the runtime engine."""

from __future__ import annotations

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.ports.model import ModelPort
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.checkpoint import CheckpointStore
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine, RuntimeMetrics
from antisentinel.worker.runtime.loop import RuntimeResult


class WorkerHandler:
    def __init__(self, engine: RuntimeEngine | None = None) -> None:
        self.engine = engine or RuntimeEngine()

    def handle(
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
    ) -> RuntimeResult:
        return self.engine.run(
            incident,
            session,
            model,
            registry=registry,
            checkpoint_store=checkpoint_store,
            config=config,
            resume=resume,
            metrics=metrics,
        )
