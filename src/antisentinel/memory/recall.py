"""Scope-limited recall for the short-term Session Tree."""

from __future__ import annotations

from datetime import datetime, timezone

from antisentinel.memory.models import (
    AuthorizedMemoryScope,
    MemoryContextView,
    MemoryRecord,
    MemoryScope,
    MissingMemoryScopeError,
    SessionKey,
)
from antisentinel.memory.session_tree import SessionTimelineTree
from antisentinel.memory.trusted_recall import TrustedMemoryRecall


class MemoryRecall:
    def __init__(self, trees: dict[SessionKey | str, SessionTimelineTree], records_provider=None, evidence_lookup=None, clock=None, candidate_retriever=None) -> None:
        self.trees = trees
        self.records_provider = records_provider
        self.evidence_lookup = evidence_lookup
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.candidate_retriever = candidate_retriever

    def recall(
        self,
        scope: MemoryScope,
        *,
        query: str,
        token_budget: int,
        memory_scope: AuthorizedMemoryScope | None = None,
        operator_id: str | None = None,
        incident_id: str | None = None,
    ) -> MemoryContextView:
        if scope.kind != "session":
            return self.empty_view()
        authorized = memory_scope
        if authorized is None and operator_id is not None:
            if incident_id is None:
                raise MissingMemoryScopeError("incident_id is required for durable memory recall")
            authorized = AuthorizedMemoryScope(operator_id, incident_id, scope.scope_id)
        tree = self._resolve_tree(scope.scope_id, authorized)
        if tree is None:
            return self.empty_view()
        digest = tree.digest(token_budget=token_budget)
        if self.records_provider is None or authorized is None:
            return MemoryContextView(session_id=scope.scope_id, digest=digest["summary"])
        raw_records = self.records_provider(authorized)
        records = tuple(MemoryRecord.from_legacy_or_dict(record) for record in raw_records)
        if self.candidate_retriever is not None:
            ranks = self.candidate_retriever.retrieve(query, authorized, limit=10)
            by_id = {record.memory_id: record for record in records}
            records = tuple(by_id[item.memory_id] for item in ranks if item.memory_id in by_id)
        return TrustedMemoryRecall().recall(
            scope=authorized,
            query=query,
            token_budget=token_budget,
            now=self.clock(),
            records=records,
            evidence_lookup=self.evidence_lookup,
            digest=digest["summary"],
        ).view

    def _resolve_tree(self, session_id: str, memory_scope: AuthorizedMemoryScope | None) -> SessionTimelineTree | None:
        if memory_scope is not None:
            tree = self.trees.get(memory_scope.session_key())
            if tree is not None:
                return tree
        direct = self.trees.get(session_id)
        if direct is not None:
            return direct
        for key, tree in self.trees.items():
            if isinstance(key, SessionKey) and key.session_id == session_id:
                return tree
        return None

    @staticmethod
    def empty_view(*, session_id: str | None = None) -> MemoryContextView:
        return MemoryContextView(session_id=session_id)
