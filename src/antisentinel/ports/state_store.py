"""Port for rebuildable projected state."""

from __future__ import annotations

from typing import Any, Protocol


class StateStore(Protocol):
    def save_projection(self, scope_id: str, state: dict[str, Any]) -> None:
        """Persist a query projection for one scope."""

    def load_projection(self, scope_id: str) -> dict[str, Any] | None:
        """Load a query projection, if present."""

    def mark_projection_lag(self, scope_id: str, error: str) -> None:
        """Record that projection has fallen behind its event source."""
