"""Exact identifier and safe FTS retrieval for code-search documents."""

from __future__ import annotations

import re

from .models import CodeSearchHit, CodeSearchScope
from .sqlite_store import SQLiteCodeSearchStore


class KeywordRetriever:
    def __init__(self, store: SQLiteCodeSearchStore) -> None:
        self.store = store

    def search(self, scope: CodeSearchScope, query: str, *, limit: int = 30, projection_revision=None) -> tuple[CodeSearchHit, ...]:
        if not query.strip() or limit < 1:
            return ()
        revision=self.store.resolve_projection(scope, projection_revision)
        if revision is None:return ()
        exact = self.store.query_exact(scope, query, limit=limit, projection_revision=revision)
        remaining = max(0, limit - len(exact))
        lexical = self.store.query_fts(scope, _fts_expression(query), limit=remaining or limit, projection_revision=revision)
        seen = {hit.document_id for hit in exact}
        return exact + tuple(hit for hit in lexical if hit.document_id not in seen)[:remaining]

    def is_ready(self, scope: CodeSearchScope, *, projection_revision=None) -> bool:
        return self.store.has_ready_manifest(scope, projection_revision=projection_revision)


def _fts_expression(query: str) -> str:
    """Convert arbitrary user text to a conservative FTS5 OR expression."""
    tokens = re.findall(r"[\w]+", query, flags=re.UNICODE)
    terms: list[str] = []
    for token in tokens:
        for term in (token, *re.split(r"[_\-./:]+", token)):
            if term and term not in terms:
                terms.append(term)
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
