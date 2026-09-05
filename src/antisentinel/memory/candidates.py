"""Candidate extraction and classification ports for asynchronous memory work."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class CandidateSource:
    operator_id: str
    session_id: str
    incident_id: str | None = None
    user_input: str = ""
    tool_summaries: tuple[str, ...] = ()
    agent_conclusion: str | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    operator_id: str
    session_id: str
    text: str
    source_kind: str
    incident_id: str | None = None
    turn_id: str | None = None
    source_version: int = 1

    @classmethod
    def create(
        cls, *, operator_id: str, incident_id: str | None, session_id: str,
        turn_id: str | None, text: str, source_kind: str, source_version: int,
    ) -> "MemoryCandidate":
        if not all(isinstance(value, str) and value.strip() for value in (operator_id, session_id, text, source_kind)):
            raise ValueError("candidate operator_id, session_id, text, and source_kind are required")
        if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version < 1:
            raise ValueError("candidate source_version must be a positive integer")
        identity = {
            "operator_id": operator_id,
            "incident_id": incident_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "text": " ".join(text.strip().split()),
            "source_kind": source_kind,
            "source_version": source_version,
        }
        candidate_id = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return cls(candidate_id, operator_id, session_id, text, source_kind, incident_id, turn_id, source_version)


@dataclass(frozen=True)
class MemoryClassification:
    candidate_id: str
    should_remember: bool
    memory_type: str
    confidence: float
    reason_code: str


class RuleCandidateExtractor:
    def extract(self, source: CandidateSource) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        if "日志" in source.user_input or "log" in source.user_input.lower():
            candidates.append(MemoryCandidate.create(
                operator_id=source.operator_id, incident_id=source.incident_id, session_id=source.session_id,
                turn_id=None, text="logs_before_metrics", source_kind="user_input", source_version=1,
            ))
        if source.agent_conclusion:
            candidates.append(MemoryCandidate.create(
                operator_id=source.operator_id, incident_id=source.incident_id, session_id=source.session_id,
                turn_id=None, text=source.agent_conclusion, source_kind="agent_conclusion", source_version=1,
            ))
        return candidates


class RuleMemoryClassifier:
    def classify(self, candidate: MemoryCandidate) -> MemoryClassification:
        if candidate.source_kind == "user_input" and candidate.text == "logs_before_metrics":
            return MemoryClassification(candidate.candidate_id, True, "preference", 0.8, "explicit_operator_signal")
        if candidate.source_kind == "agent_conclusion":
            return MemoryClassification(candidate.candidate_id, True, "episodic", 0.75, "completed_rollout_conclusion")
        return MemoryClassification(candidate.candidate_id, False, "discard", 0.2, "low_signal")
