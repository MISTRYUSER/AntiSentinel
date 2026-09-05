"""Immutable types for paired memory evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


GroundTruthSource = Literal["human", "rule", "llm_draft"]
GateState = Literal["PASS", "FAIL", "INSUFFICIENT_DATA"]


@dataclass(frozen=True)
class RateMetric:
    value: float | None
    numerator: int
    denominator: int
    excluded: int = 0


@dataclass(frozen=True)
class AccuracyMetric(RateMetric):
    ground_truth_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationSummary:
    cache_hit_rate: RateMetric
    token_reduction: RateMetric
    accuracy: AccuracyMetric
    paired_success_count: int
    execution_error_count: int


@dataclass(frozen=True)
class EvaluationSampleCounts:
    cases: int
    sessions: int
    incidents: int
    eligible_cache_lookups: int


@dataclass(frozen=True)
class GroundTruthFact:
    fact_id: str
    text: str
    source: GroundTruthSource
    forbidden: bool = False


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    incident_id: str
    session_id: str
    query: str
    cutoff_at: str
    facts: tuple[GroundTruthFact, ...]


@dataclass(frozen=True)
class EvaluationConfig:
    run_id: str
    model: str
    prompt_revision: str
    provider: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ReplayOutput:
    status: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_outcomes: tuple[str, ...] = ()
    selected_memory_ids: tuple[str, ...] = ()
    answer_fact_ids: tuple[str, ...] = ()
    latency_ms: int = 0
    error: str | None = None

