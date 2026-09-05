"""Replaceable cache port."""

from __future__ import annotations

from typing import Any, Protocol


class CacheEntry(Protocol):
    value: Any
    version: int


class MemoryCache(Protocol):
    def get(self, key: str, version: int | None = None) -> Any | None:
        """Return a non-expired value matching the optional version."""

    def set(self, key: str, value: Any, *, ttl_seconds: int, version: int) -> None:
        """Set a versioned value with TTL."""

    def delete(self, key: str) -> None:
        """Remove a cached value."""
