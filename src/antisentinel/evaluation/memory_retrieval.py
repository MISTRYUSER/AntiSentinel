"""Evidence-bearing Memory Record projection and explainable retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Any, Iterable, Sequence

from .longmemeval import LongMemEvalCase


@dataclass(frozen=True)
class FilterResult:
    records: tuple[dict[str, Any], ...]
    reasons: dict[str, str]


@dataclass(frozen=True)
class ResolvedReferences:
    session_ids: tuple[str, ...]
    turn_ids: tuple[str, ...]
    evidence_refs: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AssembledMemoryContext:
    memory_ids: tuple[str, ...]
    evidence_refs: tuple[dict[str, Any], ...]
    estimated_tokens: int
    records: tuple[dict[str, Any], ...]


class MemoryRecordProjector:
    def project(self, case: LongMemEvalCase) -> tuple[dict[str, Any], ...]:
        records: list[dict[str, Any]] = []
        for session_index, session in enumerate(case.sessions):
            internal_session_id = f"{session.session_id}#{session_index}"
            records.append({
                "memory_id": f"session_digest:{case.question_id}:{internal_session_id}",
                "memory_type": "session_digest", "owner_id": "benchmark",
                "incident_id": case.question_id, "session_id": session.session_id,
                "content": session.text, "content_version": 1, "status": "active",
                "confidence": 0.6, "valid_from": session.date, "valid_to": None,
                "source_refs": [{"type": "session", "id": session.session_id}],
            })
            for turn in session.turns:
                records.append({
                    "memory_id": f"turn_summary:{case.question_id}:{internal_session_id}:{turn.turn_id}",
                    "memory_type": "turn_summary", "owner_id": "benchmark",
                    "incident_id": case.question_id, "session_id": session.session_id,
                    "content": turn.content, "content_version": 1, "status": "active",
                    "confidence": 0.7, "valid_from": session.date, "valid_to": None,
                    "source_refs": [
                        {"type": "session", "id": session.session_id},
                        {"type": "turn", "id": turn.turn_id},
                    ],
                })
        return tuple(records)


class RetrievalFilter:
    def apply(self, records: Iterable[dict[str, Any]], *, owner_id: str, incident_id: str, query_time: str) -> FilterResult:
        accepted: list[dict[str, Any]] = []
        reasons: dict[str, str] = {}
        for record in records:
            memory_id = str(record["memory_id"])
            record_owner = record.get("owner_id") or record.get("operator_id")
            if record_owner != owner_id:
                reasons[memory_id] = "owner_mismatch"; continue
            if record.get("incident_id") is not None and record.get("incident_id") != incident_id:
                reasons[memory_id] = "incident_mismatch"; continue
            if record.get("status", "active") != "active":
                reasons[memory_id] = "status_conflict"; continue
            if str(record.get("valid_from", "")) > query_time:
                reasons[memory_id] = "not_yet_valid"; continue
            valid_to = record.get("valid_to")
            if valid_to is not None and str(valid_to) <= query_time:
                reasons[memory_id] = "expired"; continue
            accepted.append(record)
        return FilterResult(tuple(accepted), reasons)


class ExplainableRanker:
    def rank(self, records: Sequence[dict[str, Any]], *, query: str, session_id: str | None, incident_id: str | None, top_k: int) -> tuple[dict[str, Any], ...]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query_tokens = set(_tokens(query))
        ranked: list[tuple[float, dict[str, Any]]] = []
        for record in records:
            lexical = _overlap(query_tokens, set(_tokens(str(record.get("content", "")))))
            scope = 1.0 if record.get("session_id") == session_id else 0.5 if record.get("incident_id") == incident_id else 0.0
            recency = _recency(record.get("valid_from"))
            confidence = float(record.get("confidence", 0.0))
            evidence_support = 1.0 if record.get("source_refs") else 0.0
            type_score = 1.0 if record.get("memory_type") == "turn_summary" else 0.5
            breakdown = {
                "lexical": lexical, "scope": scope, "recency": recency,
                "confidence": confidence, "evidence_support": evidence_support, "type": type_score,
            }
            score = 0.35 * lexical + 0.20 * scope + 0.15 * recency + 0.15 * confidence + 0.10 * evidence_support + 0.05 * type_score
            ranked.append((score, {**record, "score": score, "score_breakdown": breakdown}))
        ranked.sort(key=lambda item: (-item[0], str(item[1]["memory_id"])))
        return tuple(record for _, record in ranked[:top_k])


class EvidenceRefResolver:
    def resolve(self, records: Sequence[dict[str, Any]]) -> ResolvedReferences:
        sessions: list[str] = []; turns: list[str] = []; evidence: list[dict[str, Any]] = []
        for record in records:
            for ref in record.get("source_refs", ()):
                ref_type = ref.get("type")
                ref_id = ref.get("id")
                if not ref_id: continue
                if ref_type == "session" and ref_id not in sessions: sessions.append(ref_id)
                elif ref_type == "turn" and ref_id not in turns: turns.append(ref_id)
                elif ref_type == "evidence" and ref not in evidence: evidence.append(dict(ref))
        return ResolvedReferences(tuple(sessions), tuple(turns), tuple(evidence))


class MemoryContextAssembler:
    """Select a bounded, non-conflicting, provenance-bearing context view."""

    def assemble(self, records: Sequence[dict[str, Any]], *, token_budget: int) -> AssembledMemoryContext:
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        selected: list[dict[str, Any]] = []
        seen_entities: set[tuple[str, str]] = set()
        used = 0
        for record in sorted(records, key=lambda item: (-float(item.get("score", 0.0)), str(item.get("memory_id")))):
            entity = (str(record.get("subject")), str(record.get("predicate"))) if record.get("subject") and record.get("predicate") else None
            if entity is not None and entity in seen_entities:
                continue
            # Conservative pre-provider estimate: CJK content is close to one
            # token per two characters; this avoids over-injecting memory.
            cost = max(1, (len(str(record.get("content", ""))) + 1) // 2)
            if used + cost > token_budget:
                if not selected and used == 0:
                    bounded = dict(record)
                    bounded["content"] = str(record.get("content", ""))[: token_budget * 2]
                    selected.append(bounded)
                    used = token_budget
                    if entity is not None:
                        seen_entities.add(entity)
                continue
            selected.append(record)
            used += cost
            if entity is not None:
                seen_entities.add(entity)
        refs: list[dict[str, Any]] = []
        for record in selected:
            for ref in record.get("source_refs", ()):
                if ref not in refs:
                    refs.append(dict(ref))
        return AssembledMemoryContext(
            memory_ids=tuple(str(record["memory_id"]) for record in selected),
            evidence_refs=tuple(refs), estimated_tokens=used, records=tuple(selected),
        )


def _tokens(text: str) -> Iterable[str]:
    yield from re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())


def _overlap(query: set[str], value: set[str]) -> float:
    return len(query & value) / len(query) if query else 0.0


def _recency(value: Any) -> float:
    if not value: return 0.0
    try:
        age = max(0.0, (datetime.now() - datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)).days)
        return math.exp(-age / 30.0)
    except ValueError:
        return 0.0
