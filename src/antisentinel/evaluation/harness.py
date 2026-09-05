"""Paired baseline/optimized replay orchestration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Sequence

from .metrics import calculate_cache_hit_rate, calculate_fact_accuracy, calculate_token_reduction
from .models import EvaluationCase, EvaluationConfig, EvaluationSummary, ReplayOutput


Runner = Callable[[EvaluationCase, EvaluationConfig], ReplayOutput]


class MemoryEvaluationHarness:
    def __init__(self, baseline_runner: Runner, optimized_runner: Runner, store) -> None:
        self.baseline_runner = baseline_runner
        self.optimized_runner = optimized_runner
        self.store = store

    def run(self, cases: Sequence[EvaluationCase], config: EvaluationConfig) -> EvaluationSummary:
        self.store.save_run(config.run_id, asdict(config))
        valid_pairs: list[tuple[EvaluationCase, ReplayOutput, ReplayOutput]] = []
        execution_errors = 0
        for case in cases:
            self.store.save_case(config.run_id, asdict(case))
            baseline = self._execute(self.baseline_runner, case, config)
            optimized = self._execute(self.optimized_runner, case, config)
            self.store.save_result(config.run_id, case.case_id, "baseline", asdict(baseline))
            self.store.save_result(config.run_id, case.case_id, "optimized", asdict(optimized))
            if baseline.status == "completed" and optimized.status == "completed":
                valid_pairs.append((case, baseline, optimized))
            else:
                execution_errors += 1

        token_pairs = [(baseline.input_tokens, optimized.input_tokens) for _, baseline, optimized in valid_pairs]
        cache_outcomes = [outcome for _, _, optimized in valid_pairs for outcome in optimized.cache_outcomes]
        tp = fp = fn = 0
        sources: list[str] = []
        for case, _, optimized in valid_pairs:
            eligible = {fact.fact_id for fact in case.facts if fact.source in {"human", "rule"} and not fact.forbidden}
            forbidden = {fact.fact_id for fact in case.facts if fact.source in {"human", "rule"} and fact.forbidden}
            draft = {fact.fact_id for fact in case.facts if fact.source == "llm_draft"}
            predicted = set(optimized.answer_fact_ids) - draft
            tp += len(predicted & eligible)
            fn += len(eligible - predicted)
            fp += len((predicted - eligible) | (predicted & forbidden))
            sources.extend(fact.source for fact in case.facts if fact.source in {"human", "rule"})

        summary = EvaluationSummary(
            cache_hit_rate=calculate_cache_hit_rate(cache_outcomes),
            token_reduction=calculate_token_reduction(token_pairs),
            accuracy=calculate_fact_accuracy(tp=tp, fp=fp, fn=fn, ground_truth_sources=tuple(sources)),
            paired_success_count=len(valid_pairs),
            execution_error_count=execution_errors,
        )
        self.store.save_run(config.run_id, asdict(config), status="completed", summary=asdict(summary))
        return summary

    @staticmethod
    def _execute(runner: Runner, case: EvaluationCase, config: EvaluationConfig) -> ReplayOutput:
        try:
            return runner(case, config)
        except Exception as exc:  # noqa: BLE001 - provider errors are persisted, not accuracy failures
            return ReplayOutput(status="error", error=f"{type(exc).__name__}: {exc}")
