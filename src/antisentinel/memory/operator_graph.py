"""Scoped operator preference graph with durable records and hot cache."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from antisentinel.persistence.memory_store import FileMemoryStore, InMemoryMemoryCache

from .models import MemorySourceRef
from .resolver import EntityResolver


@dataclass(frozen=True)
class PreferenceCandidate:
    operator_id: str
    subject: str
    predicate: str
    object: str
    source_ids: tuple[str, ...]
    confidence: float
    model_version: str = "rule-v1"
    source_refs: tuple[MemorySourceRef, ...] = ()


@dataclass(frozen=True)
class PreferenceRecord:
    memory_id: str
    operator_id: str
    subject: str
    predicate: str
    object: str
    source_ids: tuple[str, ...]
    confidence: float
    status: str = "active"
    content_version: int = 1
    conflict_with: str | None = None
    model_version: str = "rule-v1"
    source_refs: tuple[MemorySourceRef, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        searchable_object = {
            "logs_before_metrics": "排查时先看日志，再看指标",
        }.get(self.object, self.object)
        return {
            "memory_id": self.memory_id, "memory_type": "preference", "operator_id": self.operator_id,
            "subject": self.subject, "predicate": self.predicate, "object": self.object,
            "content": f"{self.subject} {self.predicate} {self.object}；{searchable_object}",
            "source_ids": list(self.source_ids), "confidence": self.confidence,
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "status": self.status, "content_version": self.content_version,
            "conflict_with": self.conflict_with,
            "model_version": self.model_version,
        }


class MemoryPreferenceStore:
    def __init__(self, store) -> None:
        self.store = store

    def append(self, record: PreferenceRecord) -> None:
        self.store.append(record.to_dict())

    def replace(self, record: PreferenceRecord) -> None:
        self.store.replace(record.to_dict())

    def list_by_operator(self, operator_id: str) -> list[PreferenceRecord]:
        return [
            PreferenceRecord(
                memory_id=item["memory_id"], operator_id=item["operator_id"], subject=item["subject"],
                predicate=item["predicate"], object=item["object"], source_ids=tuple(item.get("source_ids", ())),
                confidence=item["confidence"], status=item.get("status", "active"),
                content_version=item.get("content_version", 1), conflict_with=item.get("conflict_with"),
                model_version=item.get("model_version", "rule-v1"),
                source_refs=tuple(MemorySourceRef.from_dict(ref) for ref in item.get("source_refs", ())),
            )
            for item in self.store.list_by_operator(operator_id)
            if item.get("memory_type") == "preference"
        ]


class FilePreferenceStore(MemoryPreferenceStore):
    def __init__(self, root: str) -> None:
        super().__init__(FileMemoryStore(root))


class PreferenceGraph:
    def __init__(self, durable: FilePreferenceStore, cache: InMemoryMemoryCache, resolver: EntityResolver | None = None) -> None:
        self.durable = durable
        self.cache = cache
        self._records: dict[str, list[PreferenceRecord]] = {}
        self.resolver = resolver or EntityResolver()

    def upsert(self, candidate: PreferenceCandidate) -> PreferenceRecord:
        candidate = PreferenceCandidate(
            operator_id=candidate.operator_id, subject=self.resolver.canonicalize(candidate.subject),
            predicate=candidate.predicate, object=self.resolver.canonicalize(candidate.object),
            source_ids=candidate.source_ids, confidence=candidate.confidence, model_version=candidate.model_version,
            source_refs=candidate.source_refs,
        )
        records = self._load(candidate.operator_id)
        active = next((item for item in records if item.status == "active" and item.subject == candidate.subject and item.predicate == candidate.predicate), None)
        if active is not None and active.object != candidate.object and candidate.confidence < active.confidence:
            conflict = PreferenceRecord(
                memory_id=f"conflict-{len(records) + 1}", operator_id=candidate.operator_id,
                subject=candidate.subject, predicate=candidate.predicate, object=candidate.object,
                source_ids=candidate.source_ids, confidence=candidate.confidence, status="conflict",
                content_version=active.content_version + 1, conflict_with=active.memory_id,
                model_version=candidate.model_version,
                source_refs=candidate.source_refs,
            )
            records.append(conflict)
            self.durable.append(conflict)
            self._cache(candidate.operator_id, records)
            return conflict
        record = PreferenceRecord(
            memory_id=f"preference-{len(records) + 1}", operator_id=candidate.operator_id,
            subject=candidate.subject, predicate=candidate.predicate, object=candidate.object,
            source_ids=candidate.source_ids, confidence=candidate.confidence,
            content_version=(active.content_version + 1 if active else 1),
            model_version=candidate.model_version,
            source_refs=candidate.source_refs,
        )
        if active is not None:
            if active.object == candidate.object:
                merged = replace(
                    active,
                    source_ids=tuple(dict.fromkeys((*active.source_ids, *candidate.source_ids))),
                    confidence=max(active.confidence, candidate.confidence),
                    content_version=active.content_version + 1,
                    model_version=candidate.model_version,
                    source_refs=tuple(dict.fromkeys((*active.source_refs, *candidate.source_refs))),
                )
                records[records.index(active)] = merged
                self.durable.replace(merged)
                self._cache(candidate.operator_id, records)
                return merged
            superseded = replace(active, status="superseded")
            records[records.index(active)] = superseded
            self.durable.replace(superseded)
        records.append(record)
        self.durable.append(record)
        self._cache(candidate.operator_id, records)
        return record

    def list_active(self, operator_id: str) -> list[PreferenceRecord]:
        return [item for item in self._load(operator_id) if item.status == "active"]

    def conflicts(self, operator_id: str) -> list[PreferenceRecord]:
        return [item for item in self._load(operator_id) if item.status == "conflict"]

    def _load(self, operator_id: str) -> list[PreferenceRecord]:
        key = f"operator:{operator_id}:preference_graph"
        cached = self.cache.get(key)
        if cached is not None:
            self._records[operator_id] = [self._record_from_dict(item) for item in cached.value]
            return self._records[operator_id]
        records = self._records.get(operator_id) or self.durable.list_by_operator(operator_id)
        self._records[operator_id] = list(records)
        self._cache(operator_id, records)
        return self._records[operator_id]

    def _cache(self, operator_id: str, records: list[PreferenceRecord]) -> None:
        self.cache.set(
            f"operator:{operator_id}:preference_graph",
            [item.to_dict() for item in records],
            ttl_seconds=300,
            version=len(records) or 1,
        )
        if hasattr(self.cache, "index"):
            for item in records:
                self.cache.index.put(
                    f"memory:operator:{operator_id}:preferences:index",
                    "memory:preference",
                    item.memory_id,
                    item.content_version,
                    item.to_dict(),
                    ttl_seconds=300,
                )

    @staticmethod
    def _record_from_dict(item: dict[str, Any]) -> PreferenceRecord:
        return PreferenceRecord(
            memory_id=item["memory_id"], operator_id=item["operator_id"], subject=item["subject"],
            predicate=item["predicate"], object=item["object"], source_ids=tuple(item.get("source_ids", ())),
            confidence=item["confidence"], status=item.get("status", "active"),
            content_version=item.get("content_version", 1), conflict_with=item.get("conflict_with"),
            model_version=item.get("model_version", "rule-v1"),
            source_refs=tuple(MemorySourceRef.from_dict(ref) for ref in item.get("source_refs", ())),
        )
