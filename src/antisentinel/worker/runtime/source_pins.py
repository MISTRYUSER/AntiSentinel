"""CodeMap-first source pinning: durable pointers, ephemeral bodies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from antisentinel.code_map.source_context import SourceContextSlice


@dataclass
class PinnedSource:
    """One verified source pin. body_ttl>0 keeps content for packing; else pointer-only."""

    slice: SourceContextSlice
    body_ttl: int = 0

    @property
    def evidence_id(self) -> str:
        return self.slice.evidence_id

    def as_pack_slice(self) -> SourceContextSlice:
        if self.body_ttl > 0 and self.slice.content:
            return self.slice
        return replace(self.slice, content="")

    def strip_body(self) -> None:
        if self.slice.content:
            self.slice = replace(self.slice, content="")
        self.body_ttl = 0

    def to_ref(self) -> dict[str, Any]:
        ref = {
            "evidence_id": self.slice.evidence_id,
            "incident_id": self.slice.incident_id,
            "repository_id": self.slice.repository_id,
            "snapshot_id": self.slice.snapshot_id,
            "commit_sha": self.slice.commit_sha,
            "path": self.slice.path,
            "content_hash": self.slice.content_hash,
            "pointer": True,
        }
        if self.slice.byte_start is not None:
            ref["byte_start"] = self.slice.byte_start
        if self.slice.byte_end is not None:
            ref["byte_end"] = self.slice.byte_end
        if self.slice.published_generation is not None:
            ref["published_generation"] = self.slice.published_generation
        return ref


def pin_from_slice(slice_obj: SourceContextSlice, *, body_ttl: int) -> PinnedSource:
    return PinnedSource(slice=slice_obj, body_ttl=max(0, int(body_ttl)))


def pins_from_refs(
    refs: list[dict[str, Any]] | None,
    *,
    incident_id: str,
    body_ttl: int = 0,
) -> list[PinnedSource]:
    """Build pointer pins from checkpoint/sticky refs (no body load)."""
    pins: list[PinnedSource] = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        evidence_id = str(ref.get("evidence_id") or "")
        if not evidence_id:
            continue
        byte_start = ref.get("byte_start")
        byte_end = ref.get("byte_end")
        generation = ref.get("published_generation")
        slice_obj = SourceContextSlice(
            incident_id=str(ref.get("incident_id") or incident_id),
            evidence_id=evidence_id,
            repository_id=str(ref.get("repository_id") or ""),
            snapshot_id=str(ref.get("snapshot_id") or ""),
            commit_sha=str(ref.get("commit_sha") or ""),
            path=str(ref.get("path") or ""),
            content="",
            content_hash=str(ref.get("content_hash") or ""),
            byte_start=int(byte_start) if byte_start is not None else None,
            byte_end=int(byte_end) if byte_end is not None else None,
            published_generation=int(generation) if generation is not None else None,
        )
        pins.append(PinnedSource(slice=slice_obj, body_ttl=max(0, int(body_ttl))))
    return pins


def upsert_pin(pins: list[PinnedSource], pin: PinnedSource, *, max_pins: int) -> list[PinnedSource]:
    """Replace same evidence_id or append; cap list (keep newest)."""
    out = [item for item in pins if item.evidence_id != pin.evidence_id]
    out.append(pin)
    if max_pins > 0 and len(out) > max_pins:
        out = out[-max_pins:]
    return out


def materialize_for_pack(pins: list[PinnedSource]) -> list[SourceContextSlice]:
    return [pin.as_pack_slice() for pin in pins]


def decay_packed_bodies(pins: list[PinnedSource], packed: list[SourceContextSlice]) -> None:
    """After a pack that included these slices, decrement TTL and drop bodies at 0."""
    packed_ids = {str(item.evidence_id) for item in packed}
    for pin in pins:
        if pin.evidence_id not in packed_ids:
            continue
        if pin.body_ttl <= 0:
            continue
        pin.body_ttl -= 1
        if pin.body_ttl <= 0:
            pin.strip_body()


def refs_from_pins(pins: list[PinnedSource], *, max_pins: int = 0) -> list[dict[str, Any]]:
    selected = pins[-max_pins:] if max_pins > 0 else pins
    return [pin.to_ref() for pin in selected]
