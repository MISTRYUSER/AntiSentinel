"""Structured long-term memory for completed diagnostic rollouts."""

from __future__ import annotations

from typing import Any

from antisentinel.persistence.memory_store import InMemoryMemoryCache
from antisentinel.worker.runtime.loop import RuntimeResult


class RolloutMemory:
    def __init__(self, durable: Any, cache: InMemoryMemoryCache) -> None:
        self.durable = durable
        self.cache = cache

    def record(self, result: RuntimeResult) -> dict:
        source_event_ids = [str(event.event_id) for event in getattr(result, "events", ())]
        source_refs = [{"type": "event", "id": event_id} for event_id in source_event_ids]
        source_refs.extend({"type": "evidence", "id": str(ref.evidence_id), "role": ref.role or "supporting"} for ref in result.evidence_refs)
        record = {
            "memory_id": f"rollout:{result.session_id}",
            "memory_type": "rollout",
            "rollout_id": result.session_id,
            "incident_id": result.incident_id,
            "status": result.status,
            "summary": result.final.summary if result.final else None,
            "diagnosis": result.final.diagnosis if result.final else None,
            "source_ids": [str(ref.evidence_id) for ref in result.evidence_refs],
            "source_event_ids": source_event_ids,
            "source_refs": source_refs,
            "content_version": 1,
        }
        self.durable.append(record)
        self.cache.set(f"rollout:{result.session_id}", record, ttl_seconds=600, version=1)
        if hasattr(self.cache, "index"):
            self.cache.index.put("memory:rollouts:index", "memory:rollout", result.session_id, 1, record, ttl_seconds=600)
        return record

    def get(self, rollout_id: str) -> dict | None:
        cached = self.cache.get(f"rollout:{rollout_id}")
        if cached is not None:
            return cached.value
        if hasattr(self.cache, "index"):
            indexed = self.cache.index.get("memory:rollout", rollout_id)
            if indexed is not None:
                return indexed
        item = self.durable.get(f"rollout:{rollout_id}")
        if item is not None:
            self.cache.set(f"rollout:{rollout_id}", item, ttl_seconds=600, version=item.get("content_version", 1))
        return item
