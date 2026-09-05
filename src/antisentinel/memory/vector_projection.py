"""Bounded, provenance-preserving text projection for vector indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAX_BLOCK_CHARS = 2000
MAX_BLOCKS_PER_SESSION = 8
MAX_BATCH_CHARS = 20000
MAX_BATCH_ITEMS = 20


@dataclass(frozen=True)
class VectorProjectionBlock:
    session_id: str
    turn_ids: tuple[str, ...]
    content: str
    content_version: int = 1
    truncated: bool = False


def project_session(session) -> tuple[VectorProjectionBlock, ...]:
    blocks: list[VectorProjectionBlock] = []
    content, turn_ids = "", []
    turns = tuple(session.turns)
    for turn_index, turn in enumerate(turns):
        text = str(turn.content).strip()
        if not text:
            continue
        if content and len(content) + 1 + len(text) > MAX_BLOCK_CHARS:
            blocks.append(VectorProjectionBlock(str(session.session_id), tuple(turn_ids), content))
            if len(blocks) == MAX_BLOCKS_PER_SESSION:
                return tuple(blocks[:-1] + [VectorProjectionBlock(str(session.session_id), tuple(turn_ids), content, truncated=True)])
            content, turn_ids = "", []
        remaining = MAX_BLOCK_CHARS - len(content) - (1 if content else 0)
        if remaining <= 0:
            continue
        content = f"{content} {text[:remaining]}".strip(); turn_ids.append(str(turn.turn_id))
        if len(text) > remaining:
            blocks.append(VectorProjectionBlock(str(session.session_id), tuple(turn_ids), content, truncated=True))
            return tuple(blocks)
    if content and len(blocks) < MAX_BLOCKS_PER_SESSION:
        blocks.append(VectorProjectionBlock(str(session.session_id), tuple(turn_ids), content, truncated=False))
    return tuple(blocks)


def projection_batches(blocks: Iterable[VectorProjectionBlock]):
    batch, chars = [], 0
    for block in blocks:
        if batch and (len(batch) >= MAX_BATCH_ITEMS or chars + len(block.content) > MAX_BATCH_CHARS):
            yield tuple(batch); batch, chars = [], 0
        batch.append(block); chars += len(block.content)
    if batch: yield tuple(batch)
