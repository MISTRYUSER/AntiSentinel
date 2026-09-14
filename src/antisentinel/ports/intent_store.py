"""Storage boundary: authorized snapshots and atomic revision publication."""
from typing import Protocol

from antisentinel.domain.intent import ResolvedIntent


class IntentConflict(ValueError):
    """Idempotency payload or expected revision disagrees with stored state."""


class IntentAccessDenied(ValueError):
    """Intent is unavailable in this server-authorized scope."""


class IntentStore(Protocol):
    def replay(self, ctx: dict, input_hash: str) -> ResolvedIntent | None: ...
    def prior_message(self, ctx: dict) -> str | None: ...
    def get(self, intent_id: str, actor: str, session: str, incident: str) -> ResolvedIntent: ...
    def question(self, intent_id: str, actor: str, session: str, incident: str, *, now: float) -> dict | None: ...
    def commit(self, intent: ResolvedIntent, ctx: dict, input_hash: str, expected_revision: int,
               *, now: float) -> ResolvedIntent: ...
