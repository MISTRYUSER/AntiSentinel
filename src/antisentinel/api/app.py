"""FastAPI HTTP adapter for the diagnostic application service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from asyncio import to_thread

from fastapi import FastAPI, Header, HTTPException
from pathlib import Path
import json
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from time import time
from uuid import uuid4

from antisentinel.entry.application import DiagnosisApplicationService, _result_view
from antisentinel.tracing.metrics import MemoryMetrics
from antisentinel.worker.runtime.messages import build_system_message


class CreateIncidentRequest(BaseModel):
    title: str = Field(min_length=1)
    summary: str | None = None
    source: str = Field(min_length=1)


class StartSessionRequest(BaseModel):
    participant_ids: list[str] = Field(min_length=1)
    model_mode: str = "fake"
    api_key: str | None = Field(default=None, min_length=1)
    model_name: str | None = Field(default=None, min_length=1)
    skill_id: str | None = Field(default=None, min_length=1)
    skill_version: str | None = Field(default=None, min_length=1)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


def _chat_content(result) -> str:
    """Render runtime-native structured output as readable chat text."""
    if result.final is not None:
        return result.final.summary
    if result.tasks:
        lines = ["我先处理以下事项："]
        for index, task in enumerate(result.tasks, start=1):
            tools = "、".join(call.tool_name for call in task.tool_calls)
            suffix = f"（调用：{tools}）" if tools else ""
            lines.append(f"{index}. {task.objective}{suffix}")
        return "\n".join(lines)
    return "我暂时没有生成可执行的处理计划。"


def create_app(service: DiagnosisApplicationService | None = None) -> FastAPI:
    resolved_service = service or DiagnosisApplicationService.from_environment()
    @asynccontextmanager
    async def lifespan(app):
        await to_thread(resolved_service.start_background_services)
        try:
            yield
        finally:
            await to_thread(resolved_service.stop_background_services)

    app = FastAPI(title="AntiSentinel Runtime API", lifespan=lifespan)
    app.state.service = resolved_service
    app.state.memory_metrics = app.state.service.memory_metrics or MemoryMetrics()
    @app.get('/api/retrieval/status')
    def retrieval_status():
        coordinator = app.state.service.retrieval_coordinator
        return {'state': 'disabled'} if coordinator is None else coordinator.health()

    frontend_dir = Path(__file__).resolve().parents[3] / "frontend"
    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

        @app.get("/dashboard", include_in_schema=False)
        def dashboard():
            return FileResponse(frontend_dir / "dashboard.html", media_type="text/html")

        @app.get("/dashboard.css", include_in_schema=False)
        def dashboard_styles():
            return FileResponse(frontend_dir / "dashboard.css", media_type="text/css")

        @app.get("/dashboard.js", include_in_schema=False)
        def dashboard_script():
            return FileResponse(frontend_dir / "dashboard.js", media_type="application/javascript")

        @app.get("/", include_in_schema=False)
        def index():
            mode = app.state.service.model_mode
            html = (frontend_dir / "index.html").read_text(encoding="utf-8")
            html = html.replace("<body>", f'<body data-model-mode="{mode}">', 1)
            html = html.replace("DETECTING PROVIDER", f"{mode.upper()} PROVIDER", 1)
            html = html.replace("Detecting provider…", f"{'Real' if mode == 'real' else 'Fake'} provider · read-only tools", 1)
            return HTMLResponse(html)

    @app.post("/api/incidents", status_code=201)
    def create_incident(payload: CreateIncidentRequest):
        try:
            incident = app.state.service.create_incident(**payload.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_incident", "message": str(exc)}) from exc
        return {
            "incident_id": incident.incident_id,
            "status": incident.status.value,
            "created_at": incident.created_at.isoformat(),
        }

    @app.post("/api/incidents/{incident_id}/sessions", status_code=202)
    def start_session(incident_id: str, payload: StartSessionRequest):
        try:
            start = app.state.service.start_session(
                incident_id=incident_id,
                participant_ids=payload.participant_ids,
                model_mode=payload.model_mode,
                api_key=payload.api_key,
                model_name=payload.model_name,
                skill_id=payload.skill_id,
                skill_version=payload.skill_version,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "incident_not_found", "id": str(exc)}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "session_already_started", "message": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"code": "session_start_failed", "message": str(exc)}) from exc
        return start

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        try:
            return app.state.service.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "session_not_found", "id": str(exc)}) from exc

    @app.get("/api/sessions/{session_id}/messages")
    def get_messages(session_id: str):
        try:
            return app.state.service.get_messages(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "session_not_found", "id": str(exc)}) from exc

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, payload: SendMessageRequest):
        try:
            return app.state.service.send_message(session_id, payload.content)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "session_not_found", "id": str(exc)}) from exc
        except InvalidInputError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_message", "message": str(exc)}) from exc

    @app.get("/api/runtime/config")
    def runtime_config():
        from antisentinel.worker.runtime.budget import ESTIMATE_VERSION
        from antisentinel.worker.runtime.engine import RuntimeConfig

        from antisentinel.worker.runtime.model_profile import lookup_context_window

        defaults = RuntimeConfig()
        model_name = getattr(app.state.service, "model_name", None)
        resolved = defaults.resolved_max_context_tokens(model=model_name if isinstance(model_name, str) else None)
        return {
            "model_mode": app.state.service.model_mode,
            "model_provider": getattr(app.state.service, "model_provider", "fake"),
            "model_name": model_name,
            "max_context_tokens": resolved,
            "max_context_tokens_mode": "override" if defaults.max_context_tokens > 0 else "auto",
            "model_context_window": defaults.model_context_window or lookup_context_window(model_name if isinstance(model_name, str) else None),
            "recent_turn_limit": defaults.recent_turn_limit,
            "estimate_version": ESTIMATE_VERSION,
            "context_strict": defaults.context_strict,
            "packing_mode": "floors_degrade",
            "source_pin_policy": defaults.source_pin_policy,
            "target_fill_ratio": defaults.target_fill_ratio,
        }

    @app.post("/v1/chat/completions")
    def chat_completions(payload: dict):
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(status_code=422, detail={"code": "messages_required"})
        metadata = payload.get("metadata") or {}
        model = app.state.service.model_factories.get("real", lambda: None)()
        if model is None:
            raise HTTPException(status_code=503, detail={"code": "model_unavailable"})
        from antisentinel.ports.model import ModelRequest
        request = ModelRequest(
            incident_id=str(metadata.get("incident_id", "chat-project")),
            session_id=str(metadata.get("session_id", "chat-session")),
            turn_id=f"chat-turn-{uuid4()}",
            messages=[build_system_message(), *messages],
            tools=[],
        )
        try:
            result = model.complete(request)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"code": "model_completion_failed", "message": str(exc)}) from exc
        content = _chat_content(result)
        usage = result.usage
        completion_id = f"chatcmpl-{uuid4()}"
        model_name = payload.get("model") or getattr(app.state.service, "model_name", "antisentinel")
        usage_view = {"prompt_tokens": usage.input_tokens, "completion_tokens": usage.output_tokens, "total_tokens": usage.input_tokens + usage.output_tokens}
        if payload.get("stream"):
            def events():
                yield "data: " + json.dumps({"id": completion_id, "object": "chat.completion.chunk", "created": int(time()), "model": model_name, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"id": completion_id, "object": "chat.completion.chunk", "created": int(time()), "model": model_name, "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"id": completion_id, "object": "chat.completion.chunk", "created": int(time()), "model": model_name, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage_view}, ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})
        return {"id": completion_id, "object": "chat.completion", "created": int(time()), "model": model_name, "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}], "usage": usage_view}

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(app.state.memory_metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/api/observability/tree")
    def observability_tree():
        result = []
        for incident_id, incident in app.state.service.incidents.items():
            sessions = []
            for session_id in incident.session_ids:
                session = app.state.service.sessions.get(session_id)
                runtime = app.state.service.results.get(session_id)
                if session is None:
                    continue
                sessions.append({"session_id": session_id, "status": runtime.status if runtime else "running", "turn_count": len(runtime.turns) if runtime else 0, "input_tokens": runtime.token_usage.input_tokens if runtime else 0, "output_tokens": runtime.token_usage.output_tokens if runtime else 0, "turns": [{"status": turn.status.value, "task_count": len(turn.task_ids)} for turn in runtime.turns] if runtime else []})
            result.append({"incident_id": incident_id, "title": incident.title, "status": incident.status.value, "sessions": sessions})
        return result

    @app.get("/api/observability/sessions/{session_id}")
    def observability_session(session_id: str):
        session = app.state.service.sessions.get(session_id)
        runtime = app.state.service.results.get(session_id)
        if session is None or runtime is None:
            raise HTTPException(status_code=404, detail={"code": "session_not_ready", "id": session_id})
        telemetry = getattr(app.state.service.engine, "telemetry", None)
        spans = [{"name": span.name, "request_id": span.attributes.get("request_id"), "turn_id": span.attributes.get("turn_id"), "parent_request_id": span.attributes.get("parent_request_id")} for span in telemetry.finished_spans if span.attributes.get("session_id") == session_id] if telemetry is not None else []
        groups = {"context": [item for item in spans if item["name"].startswith("context.")], "memory": [item for item in spans if item["name"].startswith("memory.")], "rag": [item for item in spans if item["name"].startswith("rag.") or item["name"].startswith("retrieval.")], "model": [item for item in spans if item["name"] == "model.complete"], "tools": [item for item in spans if item["name"].startswith("tool_call.") or item["name"].startswith("attempt.")], "worker": [item for item in spans if item["name"].startswith("worker.")]}
        return {"session_id": session_id, "incident_id": runtime.incident_id, "status": runtime.status, "trace_id": runtime.trace_id, "event_count": len(runtime.events), "evidence_ref_count": len(runtime.evidence_refs), "skill_usage": runtime.skill_usage, "token_usage": {"input_tokens": runtime.token_usage.input_tokens, "output_tokens": runtime.token_usage.output_tokens, "cached_input_tokens": runtime.token_usage.cached_input_tokens, "reasoning_tokens": runtime.token_usage.reasoning_tokens, "tool_tokens": runtime.token_usage.tool_tokens}, "trace": {"spans": spans, "groups": groups}}

    @app.get("/api/sessions/{session_id}/events")
    def session_events(session_id: str, last_event_id: str | None = Header(default=None)):
        if session_id not in app.state.service.sessions:
            raise HTTPException(status_code=404, detail={"code": "session_not_found", "id": session_id})
        return StreamingResponse(
            app.state.service.event_bus.stream(session_id, last_event_id=last_event_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    if frontend_dir.exists():
        @app.get("/styles.css", include_in_schema=False)
        def styles():
            return FileResponse(frontend_dir / "styles.css", media_type="text/css")

        @app.get("/app.js", include_in_schema=False)
        def script():
            return FileResponse(frontend_dir / "app.js", media_type="application/javascript")

        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app


app = create_app()
