"""Redis cache adapter without coupling memory code to redis-py types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedisCacheEntry:
    value: Any
    version: int


@dataclass(frozen=True)
class MemoryCacheLookup:
    status: str
    value: RedisCacheEntry | None = None


class RedisMemoryCache:
    def __init__(self, client: Any, *, namespace: str = "") -> None:
        self.client = client
        self.namespace = namespace.rstrip(":")
        self.index = RedisMemoryIndex(client, namespace=self.namespace)

    @classmethod
    def from_url(cls, url: str, *, namespace: str = "") -> "RedisMemoryCache":
        import redis
        return cls(redis.Redis.from_url(url, decode_responses=True), namespace=namespace)

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}" if self.namespace else key

    def get(self, key: str, version: int | None = None) -> RedisCacheEntry | None:
        return self.lookup(key, version=version).value

    def lookup(self, key: str, version: int | None = None) -> MemoryCacheLookup:
        try:
            raw = self.client.get(self._key(key))
        except Exception:  # noqa: BLE001 - durable store is the fallback
            return MemoryCacheLookup("bypass")
        if raw is None:
            return MemoryCacheLookup("miss")
        payload = json.loads(raw)
        if version is not None and payload.get("version") != version:
            return MemoryCacheLookup("miss")
        return MemoryCacheLookup("hit", RedisCacheEntry(payload.get("value"), payload.get("version", 1)))

    def set(self, key: str, value: Any, *, ttl_seconds: int, version: int) -> None:
        payload = json.dumps({"version": version, "value": value}, ensure_ascii=False, separators=(",", ":"))
        self.client.setex(self._key(key), ttl_seconds, payload)

    def delete(self, key: str) -> None:
        self.client.delete(self._key(key))


class RedisMemoryIndex:
    """Maintain a sorted ID index and a hash payload for each memory record."""

    def __init__(self, client: Any, *, namespace: str = "") -> None:
        self.client = client
        self.namespace = namespace.rstrip(":")

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}" if self.namespace else key

    def put(self, index_key: str, content_prefix: str, memory_id: str, score: float, value: dict[str, Any], *, ttl_seconds: int) -> None:
        index_key = self._key(index_key)
        content_key = self._key(f"{content_prefix}:{memory_id}")
        fields = {key: json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list, tuple)) else str(item) for key, item in {**value, "memory_id": memory_id}.items()}
        self.client.hset(content_key, mapping=fields)
        self.client.zadd(index_key, {memory_id: score})
        self.client.expire(content_key, ttl_seconds)
        self.client.expire(index_key, ttl_seconds)

    def recent(self, index_key: str, content_prefix: str, *, limit: int) -> list[dict[str, Any]]:
        index_key = self._key(index_key)
        memory_ids = self.client.zrevrange(index_key, 0, max(limit - 1, 0))
        records: list[dict[str, Any]] = []
        for memory_id in memory_ids:
            raw = self.client.hgetall(self._key(f"{content_prefix}:{memory_id}"))
            if not raw:
                continue
            record = {key: self._decode(value) for key, value in raw.items()}
            record["memory_id"] = memory_id
            records.append(record)
        return records

    def get(self, content_prefix: str, memory_id: str) -> dict[str, Any] | None:
        raw = self.client.hgetall(self._key(f"{content_prefix}:{memory_id}"))
        if not raw:
            return None
        record = {key: self._decode(value) for key, value in raw.items()}
        record["memory_id"] = memory_id
        return record

    @staticmethod
    def _decode(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
