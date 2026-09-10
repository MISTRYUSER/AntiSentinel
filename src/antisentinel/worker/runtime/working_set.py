"""Session-scoped Working Set: short-term continuity for the model (summaries + refs)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .budget import estimate_tokens as _budget_estimate_tokens


def _estimate_tokens(text: str) -> int:
    """Delegate to unified Phase B estimator."""
    return _budget_estimate_tokens(text)


def _format_turn_digest(turn: "TurnRecord") -> str:
    tools = []
    for event in turn.tool_events:
        refs = ",".join(event.evidence_refs[:8])
        short = (event.result_summary or "")[:120]
        tools.append(f"{event.tool_name}:{event.status}:{short}|refs={refs}")
    tool_part = "[" + ";".join(tools) + "]" if tools else "[]"
    return (
        f"turn={turn.turn_index} plan={turn.plan_summary}; "
        f"tools={tool_part}; outcome={turn.outcome}"
    )


@dataclass
class ToolEvent:
    tool_name: str
    args_fingerprint: str
    status: str
    result_summary: str
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args_fingerprint": self.args_fingerprint,
            "status": self.status,
            "result_summary": self.result_summary,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ToolEvent":
        return cls(
            tool_name=str(value.get("tool_name") or ""),
            args_fingerprint=str(value.get("args_fingerprint") or ""),
            status=str(value.get("status") or ""),
            result_summary=str(value.get("result_summary") or ""),
            evidence_refs=[str(item) for item in value.get("evidence_refs") or []],
        )


@dataclass
class TurnRecord:
    turn_index: int
    plan_summary: str
    tool_events: list[ToolEvent] = field(default_factory=list)
    outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "plan_summary": self.plan_summary,
            "tool_events": [event.to_dict() for event in self.tool_events],
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TurnRecord":
        return cls(
            turn_index=int(value.get("turn_index") or 0),
            plan_summary=str(value.get("plan_summary") or ""),
            tool_events=[ToolEvent.from_dict(item) for item in value.get("tool_events") or []],
            outcome=str(value.get("outcome") or ""),
        )


@dataclass
class StickyState:
    active_skill: dict[str, str] | None = None
    source_slice_refs: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    active_hypotheses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_skill": deepcopy(self.active_skill),
            "source_slice_refs": deepcopy(self.source_slice_refs),
            "open_questions": list(self.open_questions),
            "active_hypotheses": list(self.active_hypotheses),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "StickyState":
        if not value:
            return cls()
        skill = value.get("active_skill")
        return cls(
            active_skill=dict(skill) if isinstance(skill, dict) else None,
            source_slice_refs=deepcopy(value.get("source_slice_refs") or []),
            open_questions=[str(item) for item in value.get("open_questions") or []],
            active_hypotheses=[str(item) for item in value.get("active_hypotheses") or []],
        )


@dataclass
class WorkingSet:
    recent_turn_limit: int = 3
    sticky: StickyState = field(default_factory=StickyState)
    recent_turns: list[TurnRecord] = field(default_factory=list)
    older_digest: str = ""
    compacted_turns: int = 0
    memory_view: Any | None = None
    # Soft cap for folded history (Phase B replaces with global ContextBudget).
    max_older_digest_tokens: int = 2048

    def __post_init__(self) -> None:
        if (
            isinstance(self.recent_turn_limit, bool)
            or not isinstance(self.recent_turn_limit, int)
            or self.recent_turn_limit < 1
        ):
            raise ValueError("recent_turn_limit must be a positive integer")
        if (
            isinstance(self.max_older_digest_tokens, bool)
            or not isinstance(self.max_older_digest_tokens, int)
            or self.max_older_digest_tokens < 0
        ):
            raise ValueError("max_older_digest_tokens must be a non-negative integer")

    def _fold_oldest(self) -> None:
        oldest = self.recent_turns.pop(0)
        fragment = _format_turn_digest(oldest)
        if self.older_digest:
            self.older_digest = f"{self.older_digest}\n{fragment}"
        else:
            self.older_digest = fragment
        self.compacted_turns += 1
        self.compact_older(self.max_older_digest_tokens)

    def append_turn(self, turn: TurnRecord) -> None:
        self.recent_turns.append(turn)
        while len(self.recent_turns) > self.recent_turn_limit:
            self._fold_oldest()

    def set_recent_turn_limit(self, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("recent_turn_limit must be a positive integer")
        self.recent_turn_limit = limit
        while len(self.recent_turns) > self.recent_turn_limit:
            self._fold_oldest()

    def compact_older(self, max_digest_tokens: int) -> None:
        if max_digest_tokens < 0:
            raise ValueError("max_digest_tokens must be non-negative")
        if not self.older_digest:
            return
        if max_digest_tokens == 0:
            self.older_digest = ""
            return
        if _estimate_tokens(self.older_digest) <= max_digest_tokens:
            return
        # Keep the *tail* (more recent folded turns), drop oldest digest prefix.
        max_bytes = max_digest_tokens * 3
        encoded = self.older_digest.encode("utf-8")
        if len(encoded) <= max_bytes:
            return
        truncated = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        # Avoid starting mid-line when possible.
        newline = truncated.find("\n")
        if newline != -1 and newline < len(truncated) - 1:
            truncated = truncated[newline + 1 :]
        self.older_digest = "…" + truncated.lstrip()

    def update_sticky(
        self,
        *,
        active_skill: dict[str, str] | None = None,
        source_slice_refs: list[dict[str, Any]] | None = None,
        open_questions: list[str] | None = None,
        active_hypotheses: list[str] | None = None,
    ) -> None:
        if active_skill is not None:
            self.sticky.active_skill = dict(active_skill)
        if source_slice_refs is not None:
            self.sticky.source_slice_refs = deepcopy(source_slice_refs)
        if open_questions is not None:
            self.sticky.open_questions = list(open_questions)
        if active_hypotheses is not None:
            self.sticky.active_hypotheses = list(active_hypotheses)

    def export_task_results(self) -> list[dict[str, Any]]:
        """Flatten recent tool events into legacy task_results-shaped entries (no raw payloads)."""
        results: list[dict[str, Any]] = []
        for turn in self.recent_turns:
            for index, event in enumerate(turn.tool_events):
                results.append(
                    {
                        "task_id": f"turn-{turn.turn_index}-tool-{index}",
                        "status": event.status,
                        "summary": event.result_summary,
                        "evidence_refs": [{"evidence_id": ref, "role": "tool_result"} for ref in event.evidence_refs],
                        "tool_name": event.tool_name,
                        "args_fingerprint": event.args_fingerprint,
                        "turn_index": turn.turn_index,
                    }
                )
        return results

    def tool_event_count(self) -> int:
        return sum(len(turn.tool_events) for turn in self.recent_turns)

    def digest_token_estimate(self) -> int:
        return _estimate_tokens(self.older_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recent_turn_limit": self.recent_turn_limit,
            "max_older_digest_tokens": self.max_older_digest_tokens,
            "sticky": self.sticky.to_dict(),
            "recent_turns": [turn.to_dict() for turn in self.recent_turns],
            "older_digest": self.older_digest,
            "compacted_turns": self.compacted_turns,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkingSet":
        if not isinstance(value, dict):
            raise ValueError("working_set must be an object")
        return cls(
            recent_turn_limit=int(value.get("recent_turn_limit") or 3),
            max_older_digest_tokens=int(value["max_older_digest_tokens"])
            if "max_older_digest_tokens" in value
            else 2048,
            sticky=StickyState.from_dict(value.get("sticky")),
            recent_turns=[TurnRecord.from_dict(item) for item in value.get("recent_turns") or []],
            older_digest=str(value.get("older_digest") or ""),
            compacted_turns=int(value.get("compacted_turns") or 0),
        )


def create_empty(*, recent_turn_limit: int = 3, max_older_digest_tokens: int = 2048) -> WorkingSet:
    return WorkingSet(recent_turn_limit=recent_turn_limit, max_older_digest_tokens=max_older_digest_tokens)
