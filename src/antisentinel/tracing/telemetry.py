"""OpenTelemetry wrapper for correlated runtime spans."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from threading import Lock
from contextvars import ContextVar
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span


class Telemetry:
    def __init__(self, *, service_name: str = "antisentinel", trace_path: str | Path | None = None, database=None) -> None:
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        if trace_path is not None:
            provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(trace_path)))
        if database is not None:
            provider.add_span_processor(SimpleSpanProcessor(SQLiteSpanExporter(database)))
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)))
        self.provider = provider
        self.tracer = provider.get_tracer(service_name)

    @contextmanager
    def span(self, name: str, *, request_id: str | None = None, parent_request_id: str | None = None, context: "TraceContext | None" = None, **attributes: Any) -> Iterator[Any]:
        parent_context: Context | None = None
        if context is not None and context.traceparent:
            parent_context = extract(context.inject())
        with self.tracer.start_as_current_span(name, context=parent_context) as current:
            context_token = _current_trace_context.set(context) if context is not None else None
            if context is not None:
                attributes = {"trace_id": context.trace_id, "session_id": context.session_id, "turn_id": context.turn_id, "request_id": context.request_id, "parent_request_id": context.parent_request_id, **attributes}
            elif request_id is not None:
                attributes["request_id"] = request_id
                if parent_request_id is not None:
                    attributes["parent_request_id"] = parent_request_id
            for key, value in attributes.items():
                if isinstance(value, (str, bool, int, float)):
                    current.set_attribute(key, value)
            try:
                yield current
            finally:
                if context_token is not None:
                    _current_trace_context.reset(context_token)

    @property
    def finished_spans(self) -> list[ReadableSpan]:
        self.force_flush()
        return list(self.exporter.get_finished_spans())

    def force_flush(self) -> bool:
        result = self.provider.force_flush()
        return result is None or bool(result)

    def shutdown(self) -> bool:
        result = self.provider.shutdown()
        return result is None or bool(result)


class JsonlSpanExporter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        lines = []
        for span in spans:
            attributes = _safe_attributes(span)
            lines.append(json.dumps({
                "name": span.name,
                "trace_id": span.context.trace_id if span.context else None,
                "span_id": span.context.span_id if span.context else None,
                "start_time_ns": span.start_time,
                "end_time_ns": span.end_time,
                "attributes": attributes,
            }, ensure_ascii=False, separators=(",", ":")))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + ("\n" if lines else ""))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class SQLiteSpanExporter:
    def __init__(self, database) -> None:
        self.database = database

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            attributes = _safe_attributes(span)
            context = span.context
            trace_id = str(context.trace_id if context else "unknown")
            span_id = str(context.span_id if context else uuid4())
            parent_span_id = str(span.parent.span_id) if span.parent is not None else None
            encoded = json.dumps(attributes, ensure_ascii=False, separators=(",", ":"))
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO traces(trace_id,incident_id,session_id,started_at_ns,ended_at_ns,attributes_json)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(trace_id) DO UPDATE SET
                        ended_at_ns=MAX(COALESCE(traces.ended_at_ns,excluded.ended_at_ns),excluded.ended_at_ns)
                    """,
                    (trace_id, attributes.get("incident_id"), attributes.get("session_id"), span.start_time, span.end_time, encoded),
                )
                connection.execute(
                    """
                    INSERT INTO spans(span_id,trace_id,parent_span_id,session_id,turn_id,name,start_time_ns,end_time_ns,attributes_json)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(span_id) DO NOTHING
                    """,
                    (span_id, trace_id, parent_span_id, attributes.get("session_id"), attributes.get("turn_id"), span.name, span.start_time, span.end_time, encoded),
                )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def _safe_attributes(span: ReadableSpan) -> dict[str, Any]:
    return {
        key: value for key, value in dict(span.attributes or {}).items()
        if not any(secret in key.lower() for secret in ("api_key", "auth_token", "access_token", "password", "secret"))
    }


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    session_id: str
    turn_id: str | None = None
    request_id: str | None = None
    parent_request_id: str | None = None
    causation_id: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None

    @classmethod
    def new(cls, *, session_id: str, request_id: str | None = None) -> "TraceContext":
        trace_id = f"trace-{uuid4()}"
        trace_hex = __import__("hashlib").sha256(trace_id.encode()).hexdigest()[:32]
        span_hex = __import__("hashlib").sha256((trace_id + ":span").encode()).hexdigest()[:16]
        return cls(
            trace_id=trace_id, session_id=session_id, request_id=request_id or f"request-{uuid4()}",
            traceparent=f"00-{trace_hex}-{span_hex}-01",
        )

    def child(self, *, turn_id: str | None = None, request_id: str | None = None) -> "TraceContext":
        return replace(self, turn_id=turn_id or self.turn_id, request_id=request_id or f"request-{uuid4()}", parent_request_id=self.request_id)

    @classmethod
    def from_otel(cls, span: Span) -> "TraceContext":
        span_context = span.get_span_context()
        carrier: dict[str, str] = {}
        inject(carrier, context=trace.set_span_in_context(span))
        attributes = dict(getattr(span, "attributes", {}) or {})
        return cls(
            trace_id=str(span_context.trace_id),
            session_id=str(attributes.get("session_id", "")),
            turn_id=str(attributes["turn_id"]) if attributes.get("turn_id") is not None else None,
            request_id=str(attributes["request_id"]) if attributes.get("request_id") is not None else None,
            parent_request_id=str(attributes["parent_request_id"]) if attributes.get("parent_request_id") is not None else None,
            traceparent=carrier.get("traceparent"),
            tracestate=carrier.get("tracestate"),
        )

    def inject(self) -> dict[str, str]:
        carrier = {
            "x-antisentinel-trace-id": self.trace_id,
            "x-antisentinel-session-id": self.session_id,
        }
        if self.traceparent:
            carrier["traceparent"] = self.traceparent
        if self.tracestate:
            carrier["tracestate"] = self.tracestate
        return carrier

    @classmethod
    def extract(cls, carrier: dict[str, str]) -> "TraceContext | None":
        if not carrier:
            return None
        traceparent = carrier.get("traceparent")
        trace_id = carrier.get("x-antisentinel-trace-id", "")
        if traceparent:
            try:
                extracted = extract(carrier)
                span = trace.get_current_span(extracted)
                context = span.get_span_context()
                if not context.is_valid:
                    return None
                trace_id = str(context.trace_id)
            except Exception:
                return None
        if not trace_id:
            return None
        return cls(
            trace_id=trace_id,
            session_id=carrier.get("x-antisentinel-session-id", ""),
            traceparent=traceparent,
            tracestate=carrier.get("tracestate"),
        )


_current_trace_context: ContextVar[TraceContext | None] = ContextVar("antisentinel_trace_context", default=None)


def current_trace_context() -> TraceContext | None:
    return _current_trace_context.get()
