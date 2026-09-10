"""Budgeted assembly of hash-verified source Evidence for Context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from antisentinel.code_map.source_context import SourceBudgetExceeded
from .fusion import _overlapping_source


@dataclass(frozen=True)
class EvidenceContext:
    slices: tuple[Any, ...]
    total_bytes: int
    truncated: bool


class CodeEvidenceAssembler:
    def __init__(self, source_evidence_service) -> None:
        self.source_evidence_service = source_evidence_service

    def select(self, incident_id: str, candidates: list[dict[str, Any]] | tuple[dict[str, Any], ...], *, max_fragments: int = 4, max_bytes: int = 32 * 1024) -> EvidenceContext:
        if not 1 <= max_fragments <= 4 or not 1 <= max_bytes <= 32 * 1024:
            raise ValueError("evidence budget exceeds maximum")
        selected: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        total_bytes = 0
        truncated = False
        accepted = []
        for candidate in candidates:
            key = (str(candidate.get("repository_id", "")), str(candidate.get("snapshot_id", "")), str(candidate.get("chunk_id", "")))
            unique_key = key + ((candidate.get('byte_start'), candidate.get('byte_end')) if candidate.get('parent_source_hash') is not None else ())
            if unique_key in seen:
                continue
            seen.add(unique_key)
            if candidate.get('parent_source_hash') is not None and any(_overlapping_source(candidate, item) for item in accepted):
                continue
            if len(selected) >= max_fragments:
                truncated = True
                continue
            try:
                source = self.source_evidence_service.read_source(incident_id, *key,
                    max_bytes=max_bytes - total_bytes,
                    expected_generation=candidate.get('published_generation'),
                    expected_commit=candidate.get('commit_sha'),
                    expected_hash=candidate.get('source_hash'),expected_node_id=candidate.get('node_id'),expected_path=candidate.get('path'),
                    expected_byte_start=candidate.get('byte_start'),expected_byte_end=candidate.get('byte_end'),
                    expected_parent_hash=candidate.get('parent_source_hash'))
            except SourceBudgetExceeded:
                truncated = True
                continue
            content = source["content"] if isinstance(source, dict) else source.content
            byte_count = len(str(content).encode("utf-8"))
            if total_bytes + byte_count > max_bytes:
                truncated = True
                continue
            selected.append(source)
            accepted.append(candidate)
            total_bytes += byte_count
        return EvidenceContext(tuple(selected), total_bytes, truncated)
