"""Prometheus metrics for memory/cache/worker observation."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, generate_latest
from antisentinel.ports.model import TokenUsage


class MemoryMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.cache_hits = Counter("antisentinel_memory_cache_hits_total", "Memory cache hits", ["memory_type", "cache_layer"], registry=self.registry)
        self.cache_misses = Counter("antisentinel_memory_cache_misses_total", "Memory cache misses", ["memory_type", "cache_layer"], registry=self.registry)
        self.cache_bypasses = Counter("antisentinel_memory_cache_bypasses_total", "Memory cache bypasses", ["memory_type", "cache_layer"], registry=self.registry)
        self.worker_jobs = Counter("antisentinel_memory_worker_processed_total", "Processed memory jobs", ["memory_type"], registry=self.registry)
        self.model_input_tokens = Counter("antisentinel_model_input_tokens_total", "Model input tokens", ["model"], registry=self.registry)
        self.model_output_tokens = Counter("antisentinel_model_output_tokens_total", "Model output tokens", ["model"], registry=self.registry)
        self.model_cached_tokens = Counter("antisentinel_model_cached_input_tokens_total", "Cached model input tokens", ["model"], registry=self.registry)
        self.model_reasoning_tokens = Counter("antisentinel_model_reasoning_tokens_total", "Reasoning tokens", ["model"], registry=self.registry)
        self.model_tool_tokens = Counter("antisentinel_model_tool_tokens_total", "Tool tokens", ["model"], registry=self.registry)

    def cache_hit(self, memory_type: str, cache_layer: str) -> None:
        self.cache_hits.labels(memory_type, cache_layer).inc()

    def cache_miss(self, memory_type: str, cache_layer: str) -> None:
        self.cache_misses.labels(memory_type, cache_layer).inc()

    def cache_bypass(self, memory_type: str, cache_layer: str) -> None:
        self.cache_bypasses.labels(memory_type, cache_layer).inc()

    def worker_processed(self, memory_type: str) -> None:
        self.worker_jobs.labels(memory_type).inc()

    def observe_tokens(self, usage: TokenUsage, *, model: str) -> None:
        self.model_input_tokens.labels(model).inc(usage.input_tokens)
        self.model_output_tokens.labels(model).inc(usage.output_tokens)
        self.model_cached_tokens.labels(model).inc(usage.cached_input_tokens)
        self.model_reasoning_tokens.labels(model).inc(usage.reasoning_tokens)
        self.model_tool_tokens.labels(model).inc(usage.tool_tokens)

    def observe_context_budget(self, report) -> None:
        """Record BudgetReport aggregates (no payloads). Safe no-op if counters missing."""
        if not hasattr(self, "context_tokens_estimated"):
            self.context_tokens_estimated = Counter(
                "antisentinel_context_tokens_estimated",
                "Estimated context tokens at pack time",
                registry=self.registry,
            )
            self.context_block_tokens = Counter(
                "antisentinel_context_block_tokens",
                "Estimated tokens per context block",
                ["block"],
                registry=self.registry,
            )
            self.context_truncated_total = Counter(
                "antisentinel_context_truncated_total",
                "Truncated context slices/fields",
                registry=self.registry,
            )
            self.context_dropped_total = Counter(
                "antisentinel_context_dropped_total",
                "Dropped context items",
                ["reason"],
                registry=self.registry,
            )
        payload = report.to_event_payload() if hasattr(report, "to_event_payload") else {}
        self.context_tokens_estimated.inc(int(payload.get("total_estimated") or 0))
        blocks = payload.get("blocks") or {}
        for name, usage in blocks.items():
            used = usage.get("used", 0) if isinstance(usage, dict) else getattr(usage, "used", 0)
            self.context_block_tokens.labels(name).inc(int(used or 0))
        self.context_truncated_total.inc(int(payload.get("truncated_slice_count") or 0))
        for reason in payload.get("dropped_reasons") or ["unspecified"]:
            # Count each dropped item by reason when detail available.
            pass
        dropped = getattr(report, "dropped", None) or []
        if dropped:
            for item in dropped:
                reason = item.reason if hasattr(item, "reason") else str(item.get("reason", "unspecified"))
                self.context_dropped_total.labels(reason).inc()
        elif int(payload.get("dropped_count") or 0):
            self.context_dropped_total.labels("unspecified").inc(int(payload["dropped_count"]))


    def render(self) -> str:
        return generate_latest(self.registry).decode("utf-8")
