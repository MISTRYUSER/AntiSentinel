"""Complete-history candidate inventory for LongMemEval evaluation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable, Iterator

from .longmemeval import LongMemEvalCase


@dataclass(frozen=True)
class HistoryCandidate:
    candidate_id: str
    question_id: str
    session_id: str
    turn_id: str
    role: str
    text: str
    proxy_evidence_label: bool


@dataclass(frozen=True)
class HistoryScanSummary:
    total_cases: int
    total_turns: int
    proxy_positive_turns: int
    unique_content_count: int
    duplicate_content_groups: int
    duplicate_turns: int


class LongMemEvalHistoryScanner:
    def scan(self, cases: Iterable[LongMemEvalCase]) -> Iterator[HistoryCandidate]:
        for case in cases:
            for session in case.sessions:
                for turn in session.turns:
                    yield HistoryCandidate(
                        candidate_id=f"{case.question_id}:{session.session_id}:{turn.turn_id.rsplit(':', 1)[-1]}",
                        question_id=case.question_id, session_id=session.session_id, turn_id=turn.turn_id,
                        role=turn.role, text=turn.content, proxy_evidence_label=turn.has_answer,
                    )

    def summarize(self, cases: Iterable[LongMemEvalCase]) -> HistoryScanSummary:
        rows = list(self.scan(cases))
        groups = Counter(_normalize(row.text) for row in rows if _normalize(row.text))
        duplicates = [count for count in groups.values() if count > 1]
        return HistoryScanSummary(
            total_cases=len({row.question_id for row in rows}), total_turns=len(rows),
            proxy_positive_turns=sum(row.proxy_evidence_label for row in rows),
            unique_content_count=len(groups), duplicate_content_groups=len(duplicates),
            duplicate_turns=sum(duplicates),
        )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
