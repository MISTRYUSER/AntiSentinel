"""Real read-only health probes for configured local services."""

from __future__ import annotations

import time
from typing import Any

import redis

from antisentinel.domain.evidence import Evidence
from antisentinel.domain.errors import InvalidInputError
from antisentinel.tools.manifest import ToolDefinition, ToolExecutionResult


def build_redis_health_tool(*, redis_url: str = "redis://127.0.0.1:6379/0") -> ToolDefinition:
    def read_health(arguments: dict[str, Any]) -> ToolExecutionResult:
        if arguments.get("service") != "redis":
            raise InvalidInputError("only configured service 'redis' is allowed")
        started = time.monotonic()
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        response = client.ping()
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        status = "healthy" if response else "unhealthy"
        summary = f"redis PING returned {response} in {latency_ms}ms"
        evidence = Evidence.create(
            kind="health_probe",
            content_ref=f"redis://{redis_url.rsplit('@', 1)[-1]}",
            source="redis",
            metadata={"service": "redis", "status": status, "latency_ms": latency_ms},
        )
        return ToolExecutionResult(
            status="succeeded" if response else "failed",
            result={"service": "redis", "status": status, "latency_ms": latency_ms},
            result_summary=summary,
            evidence=evidence,
        )

    return ToolDefinition(
        name="read_health",
        description="Run a real read-only Redis PING health check.",
        argument_schema={"type": "object", "properties": {"service": {"type": "string", "enum": ["redis"]}}, "required": ["service"], "additionalProperties": False},
        handler=read_health,
    )
