"""Deterministic entity and alias normalization before graph merge."""

from __future__ import annotations


class EntityResolver:
    _aliases = {
        "先看日志再看 metrics": "logs_before_metrics",
        "先看日志，再看 metrics": "logs_before_metrics",
        "logs before metrics": "logs_before_metrics",
        "metrics before logs": "metrics_before_logs",
    }

    def canonicalize(self, value: str) -> str:
        normalized = " ".join(value.strip().lower().split())
        return self._aliases.get(normalized, normalized)
