"""Auditable metric definitions for memory evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .models import AccuracyMetric, EvaluationSampleCounts, EvaluationSummary, GateState, RateMetric


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(Decimal(numerator) / Decimal(denominator))


def calculate_cache_hit_rate(outcomes: Iterable[str]) -> RateMetric:
    values = tuple(outcomes)
    hits = sum(item == "hit" for item in values)
    eligible = sum(item in {"hit", "miss"} for item in values)
    return RateMetric(value=_ratio(hits, eligible), numerator=hits, denominator=eligible, excluded=len(values) - eligible)


def calculate_token_reduction(pairs: Iterable[tuple[int, int]]) -> RateMetric:
    values = tuple(pairs)
    baseline = sum(item[0] for item in values)
    optimized = sum(item[1] for item in values)
    return RateMetric(
        value=(float(Decimal(1) - Decimal(optimized) / Decimal(baseline)) if baseline > 0 else None),
        numerator=baseline - optimized,
        denominator=baseline,
        excluded=0,
    )


def calculate_fact_accuracy(*, tp: int, fp: int, fn: int, ground_truth_sources: tuple[str, ...]) -> AccuracyMetric:
    denominator = tp + fp + fn
    return AccuracyMetric(
        value=_ratio(tp, denominator), numerator=tp, denominator=denominator,
        excluded=0, ground_truth_sources=tuple(dict.fromkeys(ground_truth_sources)),
    )


def evaluate_gates(summary: EvaluationSummary, counts: EvaluationSampleCounts) -> dict[str, GateState]:
    enough_common = counts.cases >= 30 and counts.sessions >= 5 and counts.incidents >= 3
    if not enough_common:
        return {name: "INSUFFICIENT_DATA" for name in ("cache_hit_rate", "token_reduction", "accuracy")}
    return {
        "cache_hit_rate": _gate(summary.cache_hit_rate.value, 0.85, counts.eligible_cache_lookups >= 10),
        "token_reduction": _gate(summary.token_reduction.value, 0.30, summary.paired_success_count >= 30),
        "accuracy": _gate(summary.accuracy.value, 0.90, summary.accuracy.denominator > 0),
    }


def _gate(value: float | None, threshold: float, enough: bool) -> GateState:
    if value is None or not enough:
        return "INSUFFICIENT_DATA"
    return "PASS" if value >= threshold else "FAIL"
