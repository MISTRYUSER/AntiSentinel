"""Retryable in-process queue used as the first async memory pipeline adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import deque
import json
from time import time
from typing import Any, Callable, Protocol

from .candidates import CandidateSource, MemoryCandidate


@dataclass(frozen=True)
class MemoryJob:
    job_id: str
    source: CandidateSource
    candidates: tuple[MemoryCandidate, ...]
    attempts: int = 0
    next_attempt_at: float = 0.0
    status: str = "pending"
    last_error: str = ""


class MemoryJobQueue(Protocol):
    def enqueue(self, job: MemoryJob) -> None: ...
    def claim(self) -> MemoryJob | None: ...
    def ack(self, job_id: str) -> None: ...
    def retry(self, job_id: str, error: str) -> None: ...
    def fail(self, job_id: str, error: str) -> None: ...


class InMemoryMemoryJobQueue:
    def __init__(self, *, clock: Callable[[], float] = time) -> None:
        self._pending: deque[MemoryJob] = deque()
        self._jobs: dict[str, MemoryJob] = {}
        self.clock = clock

    def enqueue(self, job: MemoryJob) -> None:
        if job.job_id in self._jobs:
            return
        self._jobs[job.job_id] = job
        self._pending.append(job)

    def claim(self) -> MemoryJob | None:
        for _ in range(len(self._pending)):
            job = self._pending.popleft()
            if job.next_attempt_at <= self.clock():
                return job
            self._pending.append(job)
        return None

    def ack(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def retry(self, job_id: str, error: str) -> None:
        job = self._jobs[job_id]
        retried = replace(job, attempts=job.attempts + 1, next_attempt_at=self.clock() + 2 ** job.attempts, status="pending", last_error=error)
        self._jobs[job_id] = retried
        self._pending.append(retried)

    def fail(self, job_id: str, error: str) -> None:
        job = self._jobs[job_id]
        self._jobs[job_id] = replace(job, status="failed", last_error=error)
        self._pending = deque(item for item in self._pending if item.job_id != job_id)

    def get(self, job_id: str) -> MemoryJob | None:
        return self._jobs.get(job_id)

    def has_inflight(self) -> bool:
        return any(job.status == "pending" for job in self._jobs.values())


class RedisMemoryJobQueue:
    CLAIM_SCRIPT = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #expired > 0 then
  redis.call('ZREM', KEYS[2], expired[1])
  redis.call('ZADD', KEYS[1], ARGV[1], expired[1])
end
local ready = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 1)
if #ready == 0 then return nil end
redis.call('ZREM', KEYS[1], ready[1])
redis.call('ZADD', KEYS[2], ARGV[2], ready[1])
return redis.call('HGET', KEYS[3] .. ready[1], 'payload')
"""

    def __init__(self, client: Any, prefix: str, *, lease_seconds: int = 30, clock: Callable[[], float] = time) -> None:
        self.client = client
        self.prefix = prefix.rstrip(":")
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.pending_key = f"{self.prefix}:pending"
        self.processing_key = f"{self.prefix}:processing"
        self.job_prefix = f"{self.prefix}:job:"

    def enqueue(self, job: MemoryJob) -> None:
        self.client.hset(self.job_prefix + job.job_id, mapping={"payload": self._encode(job), "last_error": "", "status": "pending"})
        self.client.zadd(self.pending_key, {job.job_id: self.clock()}, nx=True)

    def claim(self) -> MemoryJob | None:
        now = self.clock()
        raw = self.client.eval(
            self.CLAIM_SCRIPT, 3, self.pending_key, self.processing_key,
            self.job_prefix, now, now + self.lease_seconds,
        )
        if not raw:
            return None
        job = self._decode(raw)
        self.client.hset(self.job_prefix + job.job_id, mapping={"status": "processing"})
        return job

    def ack(self, job_id: str) -> None:
        self.client.zrem(self.processing_key, job_id)
        self.client.zrem(self.pending_key, job_id)
        self.client.hset(self.job_prefix + job_id, mapping={"status": "completed"})
        if hasattr(self.client, "expire"):
            self.client.expire(self.job_prefix + job_id, 3600)

    def retry(self, job_id: str, error: str) -> None:
        raw = self.client.hget(self.job_prefix + job_id, "payload")
        if raw is None:
            raise KeyError(job_id)
        retried = replace(self._decode(raw), attempts=self._decode(raw).attempts + 1, next_attempt_at=self.clock() + 2 ** self._decode(raw).attempts, status="pending", last_error=error)
        self.client.hset(self.job_prefix + job_id, mapping={"payload": self._encode(retried), "last_error": error, "status": "pending"})
        self.client.zrem(self.processing_key, job_id)
        self.client.zadd(self.pending_key, {job_id: retried.next_attempt_at})

    def fail(self, job_id: str, error: str) -> None:
        self.client.zrem(self.processing_key, job_id)
        self.client.zrem(self.pending_key, job_id)
        self.client.hset(self.job_prefix + job_id, mapping={"status": "failed", "last_error": error})

    def has_inflight(self) -> bool:
        return bool(self.client.zcard(self.pending_key) or self.client.zcard(self.processing_key))

    @staticmethod
    def _encode(job: MemoryJob) -> str:
        return json.dumps({
            "job_id": job.job_id,
            "source": {
                "operator_id": job.source.operator_id, "session_id": job.source.session_id,
                "incident_id": job.source.incident_id,
                "user_input": job.source.user_input, "tool_summaries": list(job.source.tool_summaries),
                "agent_conclusion": job.source.agent_conclusion,
            },
            "candidates": [candidate.__dict__ for candidate in job.candidates],
            "attempts": job.attempts,
            "next_attempt_at": job.next_attempt_at,
            "status": job.status,
            "last_error": job.last_error,
        }, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode(raw: str) -> MemoryJob:
        value = json.loads(raw)
        return MemoryJob(
            job_id=value["job_id"],
            source=CandidateSource(
                operator_id=value["source"]["operator_id"], session_id=value["source"]["session_id"],
                incident_id=value["source"].get("incident_id"),
                user_input=value["source"].get("user_input", ""),
                tool_summaries=tuple(value["source"].get("tool_summaries", ())),
                agent_conclusion=value["source"].get("agent_conclusion"),
            ),
            candidates=tuple(MemoryCandidate(**item) for item in value.get("candidates", ())),
            attempts=int(value.get("attempts", 0)),
            next_attempt_at=float(value.get("next_attempt_at", 0)),
            status=str(value.get("status", "pending")), last_error=str(value.get("last_error", "")),
        )
