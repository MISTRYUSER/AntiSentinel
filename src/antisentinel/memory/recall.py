"""Scope-limited recall for the short-term Session Tree."""

from __future__ import annotations

from antisentinel.memory.models import MemoryContextView, MemoryScope
from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord
from antisentinel.memory.session_tree import SessionTimelineTree
from antisentinel.memory.trusted_recall import TrustedMemoryRecall
from datetime import datetime, timezone


class MemoryRecall:
    def __init__(self, trees: dict[str, SessionTimelineTree], records_provider=None, evidence_lookup=None, clock=None, candidate_retriever=None) -> None:
        self.trees = trees
        self.records_provider = records_provider
        self.evidence_lookup = evidence_lookup
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.candidate_retriever = candidate_retriever

    def recall(self, scope: MemoryScope, *, query: str, token_budget: int, operator_id: str | None = None, incident_id: str | None = None) -> MemoryContextView:
        if scope.kind != "session":
            return self.empty_view()
        tree = self.trees.get(scope.scope_id)
        if tree is None:
            return self.empty_view()
        digest = tree.digest(token_budget=token_budget)
        if self.records_provider is None or operator_id is None:
            return MemoryContextView(session_id=scope.scope_id, digest=digest["summary"])
        records = tuple(MemoryRecord.from_legacy_or_dict(record) for record in self.records_provider(operator_id))
        if self.candidate_retriever is not None:
            authorized = AuthorizedMemoryScope(operator_id, incident_id or "unknown", scope.scope_id)
            ranks = self.candidate_retriever.retrieve(query, authorized, limit=10)
            by_id = {record.memory_id: record for record in records}
            records = tuple(by_id[item.memory_id] for item in ranks if item.memory_id in by_id)
        return TrustedMemoryRecall().recall(scope=AuthorizedMemoryScope(operator_id, incident_id or "unknown", scope.scope_id), query=query, token_budget=token_budget, now=self.clock(), records=records, evidence_lookup=self.evidence_lookup, digest=digest["summary"]).view

    @staticmethod
    def empty_view(*, session_id: str | None = None) -> MemoryContextView:
        return MemoryContextView(session_id=session_id)
