"""Immutable values shared by code-search projections and retrievers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class CodeSearchScope:
    repository_id: str
    snapshot_id: str
    published_generation: int
    commit_sha: str

    def __post_init__(self) -> None:
        for name in ("repository_id", "snapshot_id", "commit_sha"):
            _required(getattr(self, name), name)
        if not isinstance(self.published_generation, int) or self.published_generation < 1:
            raise ValueError("published_generation must be a positive integer")

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (self.repository_id, self.snapshot_id, self.published_generation, self.commit_sha)


@dataclass(frozen=True)
class CodeSearchDocument:
    repository_id: str
    snapshot_id: str
    published_generation: int
    commit_sha: str
    node_id: str
    chunk_id: str
    path: str
    symbol: str
    language: str
    source_hash: str
    embedding_input_hash: str
    projection_revision: str
    text: str
    byte_start: int | None = None
    byte_end: int | None = None
    parent_source_hash: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "repository_id", "snapshot_id", "commit_sha", "node_id", "chunk_id", "path",
            "symbol", "language", "source_hash", "embedding_input_hash", "projection_revision", "text",
        ):
            if name == 'text' and self.byte_start is not None:
                if not isinstance(self.text, str) or not self.text:
                    raise ValueError('slice text cannot be empty')
            else:
                _required(getattr(self, name), name)
        if not isinstance(self.published_generation, int) or self.published_generation < 1:
            raise ValueError("published_generation must be a positive integer")
        ranges = (self.byte_start, self.byte_end, self.parent_source_hash)
        if any(value is not None for value in ranges):
            if type(self.byte_start) is not int or type(self.byte_end) is not int or not 0 <= self.byte_start < self.byte_end or not self.parent_source_hash:
                raise ValueError('complete source range and parent hash required')
            if not isinstance(self.parent_source_hash, str) or len(self.parent_source_hash) != 64 or any(c not in '0123456789abcdef' for c in self.parent_source_hash):
                raise ValueError('invalid parent source hash')
            raw = self.text.encode('utf-8')
            if len(raw) != self.byte_end-self.byte_start or hashlib.sha256(raw).hexdigest() != self.source_hash:
                raise ValueError('slice byte range or hash mismatch')

    @property
    def document_id(self) -> str:
        identity = "\x1f".join(
            (
                self.repository_id,
                self.snapshot_id,
                str(self.published_generation),
                self.commit_sha,
                self.node_id,
                self.chunk_id,
                self.projection_revision,
            )
        )
        if self.byte_start is not None:
            identity += f'\x1fslice-v1\x1f{self.byte_start}\x1f{self.byte_end}\x1f{self.parent_source_hash}'
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @property
    def source_identity(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "snapshot_id": self.snapshot_id,
            "published_generation": self.published_generation,
            "commit_sha": self.commit_sha,
            "node_id": self.node_id,
            "chunk_id": self.chunk_id,
            "path": self.path,
            "source_hash": self.source_hash,
            **({"byte_start": self.byte_start, "byte_end": self.byte_end,
                "parent_source_hash": self.parent_source_hash} if self.byte_start is not None else {}),
        }


def normalize_document_record(value):
    """Interpret missing optional fields in legacy manifests without rewriting them."""
    return {'byte_start': None, 'byte_end': None, 'parent_source_hash': None, **dict(value)}


@dataclass(frozen=True)
class CodeSearchHit:
    document_id: str
    rank: float
    channel: str
    source_identity: dict[str, Any]
