"""Application service for the in-process diagnostic API."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from threading import Thread
from typing import Any, Callable
import os
from pathlib import Path

from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
from antisentinel.domain.evidence import Evidence
from antisentinel.domain.event import Event
from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult
from antisentinel.tools.registry import ToolRegistry
from antisentinel.worker.runtime.engine import RuntimeConfig, RuntimeEngine
from antisentinel.worker.runtime.loop import RuntimeResult
from antisentinel.ports.model import ModelRequest
from antisentinel.runtime.events import RuntimeEventBus
from antisentinel.memory.recorder import MemoryRecorder
from antisentinel.adapters.cache.redis import RedisMemoryCache
from antisentinel.tools.health import build_redis_health_tool
from antisentinel.tracing.logging import configure_runtime_logging
from antisentinel.tracing.telemetry import Telemetry
from antisentinel.tracing.metrics import MemoryMetrics
from antisentinel.persistence.application_store import FileApplicationStore
from antisentinel.entry.conversation_store import FileConversationStore


@dataclass
class DiagnosisApplicationService:
    engine: RuntimeEngine
    model_factories: dict[str, Callable[[], object]]
    registry_factory: Callable[[], ToolRegistry]
    model_mode: str = "fake"
    incidents: dict[str, Incident] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    results: dict[str, RuntimeResult] = field(default_factory=dict)
    _running_incidents: set[str] = field(default_factory=set)
    event_bus: RuntimeEventBus = field(default_factory=RuntimeEventBus)
    _lock: Lock = field(default_factory=Lock, repr=False)
    memory_recorder: MemoryRecorder | None = None
    memory_metrics: MemoryMetrics | None = None
    model_provider: str = "fake"
    model_name: str | None = None
    application_store: Any | None = None
    conversation_store: Any | None = None
    sqlite_database: Any | None = None
    audit_degraded: list[str] = field(default_factory=list)
    skill_runtime_factory: Callable[[ToolRegistry, str, str, str | None], object] | None = None

    @classmethod
    def default_fake(cls) -> "DiagnosisApplicationService":
        def build_runtime():
            model = FakeProviderModel([
                {"tasks": [{"task_id": "smoke-task", "objective": "inspect the mock service", "tool_calls": [
                    {"tool_name": "read_health", "arguments": {"service": "redis"}},
                    {"tool_name": "read_health", "arguments": {"service": "redis"}},
                ]}]},
                {"final": {"summary": "Redis health probe completed", "diagnosis": "Redis is reachable", "confidence": 0.9, "evidence_refs": []}},
            ])
            registry = ToolRegistry(auto_discover=False)
            registry.register(build_redis_health_tool(
                redis_url=os.getenv("ANTISENTINEL_REDIS_URL", "").strip() or "redis://127.0.0.1:6379/0",
            ))
            return model, registry

        service = cls(
            engine=RuntimeEngine(),
            model_factories={"fake": lambda: build_runtime()[0]},
            registry_factory=lambda: build_runtime()[1],
            model_mode="fake",
        )
        service._runtime_builder = build_runtime  # type: ignore[attr-defined]
        from antisentinel.capabilities.loader import SkillLoader
        from antisentinel.capabilities.package import build_local_package
        from antisentinel.capabilities.runtime_state import SkillRuntimeState
        from antisentinel.capabilities.tools import InMemorySkillStateStore, SkillRuntime

        capability_source = Path(__file__).resolve().parents[1] / "capabilities" / "diagnosis"

        def build_skill_runtime(registry, run_id, skill_id, skill_version):
            capability_release_root = Path(os.getenv("ANTISENTINEL_STORAGE_ROOT", "storage")) / "capability-releases"
            published_capability = build_local_package(capability_source, capability_release_root)
            skill = next((item for item in published_capability.plugin.skills if item.skill_id == skill_id), None)
            if skill is None:
                raise InvalidInputError("unknown_skill")
            if skill_version is not None and skill.version != skill_version:
                raise InvalidInputError("skill_version_not_found")
            allowed = frozenset(item["name"] for item in registry.manifests())
            return SkillRuntime(
                loader=SkillLoader(published_capability),
                state=SkillRuntimeState(run_id=run_id, release_id=published_capability.release_id),
                state_store=InMemorySkillStateStore(),
                allowed_tool_names=allowed,
                base_tool_names=frozenset(),
            )

        service.skill_runtime_factory = build_skill_runtime
        return service

    @classmethod
    def from_environment(cls) -> "DiagnosisApplicationService":
        service = cls.default_fake()
        storage_root = os.getenv("ANTISENTINEL_STORAGE_ROOT", "").strip()
        redis_url = os.getenv("ANTISENTINEL_REDIS_URL", "").strip()
        if storage_root or redis_url:
            resolved_storage_root = storage_root or "storage"
            persistence_mode = os.getenv("ANTISENTINEL_PERSISTENCE_MODE", "sqlite").strip().lower()
            if persistence_mode not in {"sqlite", "legacy"}:
                raise InvalidInputError("unsupported persistence mode")
            if persistence_mode == "sqlite":
                from antisentinel.memory.operator_graph import MemoryPreferenceStore
                from antisentinel.persistence.audited_stores import AuditedEventStore, AuditedEvidenceStore, AuditedMemoryStore
                from antisentinel.persistence.event_store import FileEventStore
                from antisentinel.persistence.evidence_store import FileEvidenceStore
                from antisentinel.persistence.memory_store import FileMemoryStore
                from antisentinel.persistence.sqlite_database import SQLiteDatabase
                from antisentinel.persistence.sqlite_stores import (
                    SQLiteApplicationStore, SQLiteConversationStore, SQLiteEventStore,
                    SQLiteEvidenceStore, SQLiteMemoryStore, SQLiteStateStore, SQLiteMemoryCandidateStore,
                )
                from antisentinel.memory.retrieval import HybridMemoryRetriever
                from antisentinel.persistence.local_vector_memory import LocalVectorMemory, QwenFlashEmbedder

                database_path = os.getenv("ANTISENTINEL_SQLITE_PATH", "").strip() or str(Path(resolved_storage_root) / "antisentinel.db")
                service.sqlite_database = SQLiteDatabase(database_path)
                service.sqlite_database.initialize()
                service.application_store = SQLiteApplicationStore(service.sqlite_database)
                service.conversation_store = SQLiteConversationStore(service.sqlite_database)
                primary_memory = SQLiteMemoryStore(service.sqlite_database)
                memory_store = AuditedMemoryStore(primary_memory, FileMemoryStore(resolved_storage_root), service.audit_degraded.append)
                event_store = AuditedEventStore(SQLiteEventStore(service.sqlite_database), FileEventStore(resolved_storage_root), service.audit_degraded.append)
                evidence_store = AuditedEvidenceStore(SQLiteEvidenceStore(service.sqlite_database), FileEvidenceStore(resolved_storage_root), service.audit_degraded.append)
                state_store = SQLiteStateStore(service.sqlite_database)
                preference_store = MemoryPreferenceStore(memory_store)
                vector_memory = None
                if os.getenv("ANTISENTINEL_MEMORY_VECTOR_ENABLED", "0") == "1":
                    vector_memory = LocalVectorMemory(service.sqlite_database, QwenFlashEmbedder.from_env())
                candidate_retriever = HybridMemoryRetriever(SQLiteMemoryCandidateStore(service.sqlite_database), vector_memory=vector_memory)
            else:
                service.application_store = FileApplicationStore(resolved_storage_root)
                service.conversation_store = FileConversationStore(resolved_storage_root)
                event_store = evidence_store = state_store = memory_store = preference_store = None
                candidate_retriever = None
            telemetry = Telemetry(
                service_name="antisentinel.runtime",
                trace_path=Path(resolved_storage_root) / "observability" / "traces.jsonl",
                database=service.sqlite_database,
            )
            service.engine.telemetry = telemetry
            service.engine.loop.telemetry = telemetry
            service.memory_metrics = MemoryMetrics()
            service._restore_application_state()
            configure_runtime_logging(Path(resolved_storage_root) / "observability" / "runtime.jsonl")
            redis_prefix = os.getenv("ANTISENTINEL_REDIS_PREFIX", "antisentinel:memory")
            memory_cache = RedisMemoryCache.from_url(redis_url, namespace=redis_prefix) if redis_url else None
            if memory_cache is not None:
                from antisentinel.memory.jobs import RedisMemoryJobQueue
                if not hasattr(memory_cache, "client"):
                    raise InvalidInputError("redis cache adapter must expose client for memory queue")
                job_queue = RedisMemoryJobQueue(
                    memory_cache.client,
                    redis_prefix,
                )
            else:
                job_queue = None
            service.memory_recorder = MemoryRecorder(
                resolved_storage_root if persistence_mode == "legacy" else None,
                event_store=event_store,
                evidence_store=evidence_store,
                state_store=state_store,
                memory_store=memory_store,
                preference_store=preference_store,
                job_queue=job_queue,
                cache=memory_cache,
                start_worker=bool(redis_url),
                telemetry=telemetry,
                candidate_retriever=candidate_retriever,
            )
        mode = os.getenv("ANTISENTINEL_MODEL_MODE", "fake").strip().lower()
        if mode not in {"fake", "real"}:
            raise InvalidInputError("unsupported model mode")
        service.model_mode = mode
        if mode == "real":
            from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter

            api_key = os.getenv("ANTISENTINEL_MODEL_API_KEY", "").strip()
            base_url = os.getenv("ANTISENTINEL_MODEL_BASE_URL", "https://api.openai.com/v1")
            model_name = os.getenv("ANTISENTINEL_MODEL_NAME", "gpt-4o-mini")
            timeout = float(os.getenv("ANTISENTINEL_MODEL_TIMEOUT_SECONDS", "30"))
            service.model_provider = "deepseek" if "deepseek" in base_url.lower() else "openai"
            service.model_name = model_name

            def real_model():
                if not api_key:
                    raise InvalidInputError("model_api_key_missing")
                return OpenAICompatibleModelAdapter(
                    base_url=base_url,
                    api_key=api_key,
                    model=model_name,
                    timeout=timeout,
                    telemetry=service.engine.telemetry,
                )

            service.model_factories["real"] = real_model
            if service.memory_recorder is not None:
                from antisentinel.memory.llm_classifier import MemoryLLMClassifier
                service.memory_recorder.memory_classifier = MemoryLLMClassifier(
                    base_url=os.getenv("ANTISENTINEL_MEMORY_MODEL_BASE_URL", base_url),
                    api_key=os.getenv("ANTISENTINEL_MEMORY_MODEL_API_KEY", api_key),
                    model=os.getenv("ANTISENTINEL_MEMORY_MODEL_NAME", model_name),
                    timeout=timeout,
                )
        return service

    def create_incident(self, *, title: str, summary: str | None, source: str) -> Incident:
        incident = Incident.create(title=title, source=source, summary=summary)
        self.incidents[str(incident.incident_id)] = incident
        if self.application_store is not None:
            self.application_store.save_incident(incident.to_dict())
        return incident

    def start_session(self, *, incident_id: str, participant_ids: list[str], model_mode: str, api_key: str | None = None, model_name: str | None = None, skill_id: str | None = None, skill_version: str | None = None) -> dict[str, object]:
        incident = self.incidents.get(incident_id)
        if incident is None:
            raise KeyError(incident_id)
        with self._lock:
            active_session = any(
                self.sessions.get(session_id) is not None
                and self.sessions[session_id].status.value in {"active", "waiting"}
                for session_id in incident.session_ids
            )
            if incident_id in self._running_incidents or active_session:
                raise RuntimeError("incident already has a running session")
            self._running_incidents.add(incident_id)
        try:
            if model_mode == "fake" and hasattr(self, "_runtime_builder"):
                model, registry = self._runtime_builder()  # type: ignore[attr-defined]
            else:
                if model_mode in {"openrouter", "deepseek"} and not api_key and self.model_mode == "real":
                    model = self.model_factories["real"]()
                elif model_mode in {"openrouter", "deepseek"} and api_key:
                    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter

                    defaults = {"openrouter": ("https://openrouter.ai/api/v1", "openrouter/free"), "deepseek": ("https://api.deepseek.com", "deepseek-v4-flash")}
                    base_url, default_model = defaults[model_mode]
                    selected_model = model_name or default_model
                    if model_mode == "deepseek" and selected_model.startswith("openrouter/"):
                        selected_model = default_model
                    if model_mode == "openrouter" and selected_model.startswith("deepseek-"):
                        selected_model = default_model
                    model = OpenAICompatibleModelAdapter(base_url=base_url, api_key=api_key, model=selected_model)
                else:
                    model_factory = self.model_factories.get(model_mode)
                    if model_factory is None:
                        raise InvalidInputError(f"unsupported model mode: {model_mode}")
                    model = model_factory()
                registry = self.registry_factory()
            session = Session.create(incident_id=incident.incident_id, participant_ids=participant_ids)
            session_id = str(session.session_id)
            skill_runtime = None
            if skill_id is not None:
                if self.skill_runtime_factory is None:
                    raise InvalidInputError("skill_runtime_unavailable")
                skill_runtime = self.skill_runtime_factory(registry, session_id, skill_id, skill_version)
                selected = skill_runtime.select(skill_id)
                if selected.status != "succeeded":
                    raise InvalidInputError((selected.error or {}).get("code", "skill_load_failed"))
            incident.add_session(session.session_id)
            self.sessions[str(session.session_id)] = session
            if self.application_store is not None:
                self.application_store.save_incident(incident.to_dict())
                self.application_store.save_session(session.to_dict())
            self.event_bus.publish(session_id, _lifecycle_event("session.started", incident, session))
            Thread(
                target=self._run_session,
                args=(incident, session, model, registry, skill_runtime),
                name=f"antisentinel-session-{session_id[:8]}",
                daemon=True,
            ).start()
            return {"session_id": session_id, "incident_id": str(incident.incident_id), "status": "running"}
        finally:
            self._running_incidents.discard(incident_id)

    def _run_session(self, incident, session, model, registry, skill_runtime=None) -> None:
        session_id = str(session.session_id)
        try:
            result = self.engine.run(
                incident, session, model, registry=registry,
                config=RuntimeConfig(),
                event_sink=lambda event: self.event_bus.publish(session_id, event),
                observability_metrics=self.memory_metrics,
                skill_runtime=skill_runtime,
                memory_context_provider=(
                    (lambda _incident, _session, _turn: self.memory_recorder.recall_context(
                        session_id=session_id,
                        operator_id=session.participant_ids[0],
                        incident_id=str(incident.incident_id),
                        query=f"{incident.title} {incident.summary or ''}",
                    )) if self.memory_recorder is not None else None
                ),
            )
            if self.memory_recorder is not None:
                self.memory_recorder.record(
                    result,
                    operator_id=session.participant_ids[0],
                    user_input=f"{incident.title} {incident.summary or ''}",
                )
            self.results[session_id] = result
            if self.application_store is not None:
                self.application_store.save_session(session.to_dict())
                self.application_store.save_result(session_id, _result_view(result, session))
            terminal_type = "diagnosis.completed" if result.status == "completed" else "runtime.failed"
            self.event_bus.publish(session_id, _lifecycle_event(terminal_type, incident, session, result.error))
        except Exception as exc:  # noqa: BLE001 - keep background failures observable
            error = {"code": "runtime_background_failed", "message": str(exc)}
            self.results[session_id] = None  # type: ignore[assignment]
            session.fail(error)
            self.event_bus.publish(session_id, _lifecycle_event("runtime.failed", incident, session, error))

    def get_session(self, session_id: str) -> dict[str, object]:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        result = self.results.get(session_id)
        if result is None:
            if self.application_store is not None:
                persisted = self.application_store.load_result(session_id)
                if persisted is not None:
                    persisted["messages"] = self.get_messages(session_id)
                    return persisted
            return {
                "session_id": session_id,
                "status": "running",
                "session_status": session.status.value,
            }
        view = _result_view(result, session)
        view["messages"] = self.get_messages(session_id)
        return view

    def get_messages(self, session_id: str) -> list[dict[str, object]]:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.conversation_store.load(session_id) if self.conversation_store is not None else []

    def send_message(self, session_id: str, content: str) -> dict[str, object]:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        if not isinstance(content, str) or not content.strip():
            raise InvalidInputError("content must be non-empty")
        history = self.get_messages(session_id)
        if self.conversation_store is not None:
            self.conversation_store.append(session_id, "user", content.strip())
        if self.model_mode == "fake":
            answer = f"已收到：{content.strip()}"
        else:
            model = self.model_factories["real"]()
            response = model.complete(ModelRequest(
                incident_id=self.sessions[session_id].incident_id,
                session_id=session_id,
                turn_id=f"conversation-{len(history) + 1}",
                messages=[{"role": "system", "content": "请使用简体中文回复日常对话。"}, *[{"role": item["role"], "content": item["content"]} for item in history], {"role": "user", "content": content.strip()}],
                tools=[],
            ))
            answer = response.final.summary if response.final is not None else response.raw_summary
        message = self.conversation_store.append(session_id, "assistant", answer) if self.conversation_store is not None else {"role": "assistant", "content": answer}
        return message

    def _restore_application_state(self) -> None:
        if self.application_store is None:
            return
        for raw in self.application_store.load_incidents():
            incident = Incident.from_dict(raw)
            self.incidents[str(incident.incident_id)] = incident
        for raw in self.application_store.load_sessions():
            session = Session.from_dict(raw)
            self.sessions[str(session.session_id)] = session
            incident = self.incidents.get(str(session.incident_id))
            if incident is not None:
                incident.add_session(session.session_id)


def _result_view(result: RuntimeResult, session: Session) -> dict[str, object]:
    return {
        "session_id": result.session_id,
        "incident_id": result.incident_id,
        "status": result.status,
        "session_status": session.status.value,
        "turns": [turn.to_dict() for turn in result.turns],
        "tasks": [task.to_dict() for task in result.tasks],
        "tool_calls": [call.to_dict() for call in result.tool_calls],
        "attempts": [_attempt_view(attempt) for attempt in result.attempts],
        "final": result.final.to_dict() if result.final else None,
        "evidence_refs": [{"evidence_id": ref.evidence_id, "role": ref.role} for ref in result.evidence_refs],
        "task_summaries": list(result.task_summaries),
        "error": result.error,
        "token_usage": {
            "input_tokens": result.token_usage.input_tokens,
            "output_tokens": result.token_usage.output_tokens,
            "cached_input_tokens": result.token_usage.cached_input_tokens,
            "reasoning_tokens": result.token_usage.reasoning_tokens,
            "tool_tokens": result.token_usage.tool_tokens,
        },
        "skill_usage": result.skill_usage,
    }


def _lifecycle_event(event_type: str, incident: Incident, session: Session, error=None) -> Event:
    related = {"incident_id": str(incident.incident_id), "session_id": str(session.session_id)}
    return Event.create(
        type=event_type,
        aggregate_type="Runtime",
        aggregate_id=session.session_id,
        correlation_id=session.session_id,
        occurred_at=session.updated_at,
        payload={**related, **({"error": error} if error else {})},
        related_ids=related,
    )


def _attempt_view(attempt) -> dict[str, object]:
    """Expose execution metadata without returning raw tool output."""
    return {
        "attempt_id": attempt.attempt_id,
        "tool_call_id": attempt.tool_call_id,
        "retry_index": attempt.retry_index,
        "status": attempt.status.value,
        "result_ref": attempt.result_ref,
        "result_summary": attempt.result_summary,
        "error": attempt.error,
        "evidence_refs": [
            {"evidence_id": ref.evidence_id, "role": ref.role}
            for ref in attempt.evidence_refs
        ],
    }
