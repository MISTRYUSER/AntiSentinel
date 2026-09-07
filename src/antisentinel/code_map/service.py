"""Application boundary for administrative code-map actions."""

from __future__ import annotations

from .models import RepositoryRegistration, ScanJob
from .store import SQLiteCodeMapStore


class CodeMapService:
    def __init__(self, store: SQLiteCodeMapStore) -> None:
        self.store = store

    def register(self, registration: RepositoryRegistration) -> RepositoryRegistration:
        return self.store.register(registration)

    def pause(self, repository_id: str) -> None:
        self.store.pause(repository_id)

    def resume(self, repository_id: str) -> None:
        self.store.resume(repository_id)

    def submit_build(self, repository_id: str, commit_sha: str) -> ScanJob:
        return self.store.enqueue(repository_id, commit_sha, "deployment")
