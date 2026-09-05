"""LongMemEval-S dataset mapping and retrieval-only baseline."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator, Sequence


@dataclass(frozen=True)
class LongMemEvalTurn:
    turn_id: str
    role: str
    content: str
    has_answer: bool = False


@dataclass(frozen=True)
class LongMemEvalSession:
    session_id: str
    date: str
    turns: tuple[LongMemEvalTurn, ...]

    @property
    def text(self) -> str:
        return " ".join(turn.content for turn in self.turns)


@dataclass(frozen=True)
class LongMemEvalCase:
    question_id: str
    question_type: str
    question: str
    answer: str
    question_date: str
    sessions: tuple[LongMemEvalSession, ...]
    answer_session_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalResult:
    question_id: str
    session_ids: tuple[str, ...]
    scores: tuple[float, ...]
    recall_at_k: float
    precision_at_k: float
    reciprocal_rank: float


@dataclass(frozen=True)
class RetrievalSummary:
    total_cases: int
    evaluated_cases: int
    skipped_abstentions: int
    error_count: int
    metrics: dict[str, float]


class LongMemEvalLoader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._iterator: Iterator[LongMemEvalCase] | None = None

    def __iter__(self) -> Iterator[LongMemEvalCase]:
        yield from self.from_records(json.loads(self.path.read_text(encoding="utf-8")))

    def __next__(self) -> LongMemEvalCase:
        if self._iterator is None:
            self._iterator = iter(self)
        return next(self._iterator)

    @staticmethod
    def from_records(records: Sequence[dict[str, Any]]) -> Iterator[LongMemEvalCase]:
        for record in records:
            sessions = tuple(
                LongMemEvalSession(
                    session_id=str(session_id), date=str(date),
                    turns=tuple(
                        LongMemEvalTurn(turn_id=f"{session_id}:{turn_index}", role=str(turn["role"]), content=str(turn["content"]), has_answer=bool(turn.get("has_answer", False)))
                        for turn_index, turn in enumerate(turns)
                    ),
                )
                for session_id, date, turns in zip(
                    record["haystack_session_ids"], record["haystack_dates"], record["haystack_sessions"], strict=True,
                )
            )
            yield LongMemEvalCase(
                question_id=str(record["question_id"]), question_type=str(record["question_type"]),
                question=str(record["question"]), answer=str(record["answer"]),
                question_date=str(record["question_date"]), sessions=sessions,
                answer_session_ids=tuple(str(item) for item in record.get("answer_session_ids", ())),
            )


class KeywordMemoryRetriever:
    """Deterministic baseline used to validate retrieval plumbing before BM25/vector search."""

    def retrieve(self, case: LongMemEvalCase, *, top_k: int = 5) -> RetrievalResult:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query_tokens = set(_tokens(case.question))
        scored = []
        for session in case.sessions:
            session_tokens = set(_tokens(session.text))
            overlap = len(query_tokens & session_tokens)
            score = overlap / len(query_tokens) if query_tokens else 0.0
            scored.append((score, session.session_id))
        ranked = sorted(scored, key=lambda item: (-item[0], item[1]))[:top_k]
        session_ids = tuple(item[1] for item in ranked)
        scores = tuple(item[0] for item in ranked)
        expected = set(case.answer_session_ids)
        hits = expected & set(session_ids)
        recall = len(hits) / len(expected) if expected else 0.0
        precision = len(hits) / len(session_ids) if session_ids else 0.0
        reciprocal_rank = next((1 / index for index, session_id in enumerate(session_ids, 1) if session_id in expected), 0.0)
        return RetrievalResult(case.question_id, session_ids, scores, recall, precision, reciprocal_rank)


def evaluate_retrieval(
    cases: Iterable[LongMemEvalCase], retriever: KeywordMemoryRetriever, *, top_ks: Sequence[int] = (1, 5, 10)
) -> RetrievalSummary:
    metrics: dict[str, float] = {}
    buckets: dict[int, list[RetrievalResult]] = {top_k: [] for top_k in top_ks}
    total = evaluated = skipped = errors = 0
    for case in cases:
        total += 1
        if case.question_id.endswith("_abs"):
            skipped += 1
            continue
        evaluated += 1
        for top_k in top_ks:
            try:
                buckets[top_k].append(retriever.retrieve(case, top_k=top_k))
            except Exception:
                errors += 1
    for top_k, results in buckets.items():
        if not results:
            continue
        metrics[f"recall@{top_k}"] = sum(item.recall_at_k for item in results) / len(results)
        metrics[f"precision@{top_k}"] = sum(item.precision_at_k for item in results) / len(results)
        metrics[f"mrr@{top_k}"] = sum(item.reciprocal_rank for item in results) / len(results)
    return RetrievalSummary(total, evaluated, skipped, errors, metrics)


def _tokens(text: str) -> Iterable[str]:
    yield from re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())
