import pytest

from antisentinel.evaluation.metrics import (
    calculate_cache_hit_rate,
    calculate_fact_accuracy,
    calculate_token_reduction,
    evaluate_gates,
)
from antisentinel.evaluation.models import EvaluationSampleCounts, EvaluationSummary


def test_metrics_exclude_bypass_and_use_paired_token_totals():
    cache = calculate_cache_hit_rate(["hit"] * 17 + ["miss"] * 3 + ["bypass"] * 5)
    tokens = calculate_token_reduction([(100, 60), (200, 140)])
    accuracy = calculate_fact_accuracy(tp=18, fp=1, fn=1, ground_truth_sources=("human",))

    assert cache.value == pytest.approx(0.85)
    assert cache.numerator == 17
    assert cache.denominator == 20
    assert cache.excluded == 5
    assert tokens.value == pytest.approx(1 - 200 / 300)
    assert tokens.denominator == 300
    assert accuracy.value == pytest.approx(0.90)
    assert accuracy.ground_truth_sources == ("human",)


def test_zero_denominator_is_insufficient_data_instead_of_zero():
    cache = calculate_cache_hit_rate(["bypass"])
    tokens = calculate_token_reduction([])
    accuracy = calculate_fact_accuracy(tp=0, fp=0, fn=0, ground_truth_sources=())

    assert cache.value is None
    assert tokens.value is None
    assert accuracy.value is None


def test_gates_require_minimum_samples_before_thresholds_apply():
    summary = EvaluationSummary(
        cache_hit_rate=calculate_cache_hit_rate(["hit"] * 17 + ["miss"] * 3),
        token_reduction=calculate_token_reduction([(100, 60)] * 30),
        accuracy=calculate_fact_accuracy(tp=18, fp=1, fn=1, ground_truth_sources=("human",)),
        paired_success_count=30,
        execution_error_count=0,
    )
    insufficient = EvaluationSampleCounts(cases=29, sessions=5, incidents=3, eligible_cache_lookups=20)
    sufficient = EvaluationSampleCounts(cases=30, sessions=5, incidents=3, eligible_cache_lookups=20)

    assert evaluate_gates(summary, insufficient) == {
        "cache_hit_rate": "INSUFFICIENT_DATA",
        "token_reduction": "INSUFFICIENT_DATA",
        "accuracy": "INSUFFICIENT_DATA",
    }
    assert evaluate_gates(summary, sufficient) == {
        "cache_hit_rate": "PASS",
        "token_reduction": "PASS",
        "accuracy": "PASS",
    }


def test_each_gate_fails_independently_when_samples_are_sufficient():
    summary = EvaluationSummary(
        cache_hit_rate=calculate_cache_hit_rate(["hit"] * 8 + ["miss"] * 2),
        token_reduction=calculate_token_reduction([(100, 75)] * 30),
        accuracy=calculate_fact_accuracy(tp=17, fp=2, fn=1, ground_truth_sources=("rule",)),
        paired_success_count=30,
        execution_error_count=0,
    )
    counts = EvaluationSampleCounts(cases=30, sessions=5, incidents=3, eligible_cache_lookups=10)

    assert evaluate_gates(summary, counts) == {
        "cache_hit_rate": "FAIL",
        "token_reduction": "FAIL",
        "accuracy": "FAIL",
    }


def test_harness_persists_valid_pairs_and_excludes_provider_errors(tmp_path):
    from antisentinel.evaluation.harness import MemoryEvaluationHarness
    from antisentinel.evaluation.models import (
        EvaluationCase,
        EvaluationConfig,
        GroundTruthFact,
        ReplayOutput,
    )
    from antisentinel.persistence.sqlite_database import SQLiteDatabase
    from antisentinel.persistence.sqlite_stores import SQLiteEvaluationStore

    database = SQLiteDatabase(tmp_path / "antisentinel.db")
    database.initialize()
    cases = [
        EvaluationCase(
            case_id="case-ok", incident_id="incident-1", session_id="session-1",
            query="先查什么？", cutoff_at="2026-09-04T00:00:00Z",
            facts=(GroundTruthFact("fact-logs", "先看日志", "human"),),
        ),
        EvaluationCase(
            case_id="case-error", incident_id="incident-2", session_id="session-2",
            query="原因是什么？", cutoff_at="2026-09-04T00:00:00Z",
            facts=(GroundTruthFact("fact-timeout", "上游超时", "rule"),),
        ),
    ]
    config = EvaluationConfig("run-1", "deepseek-chat", "prompt-v1", "deepseek", {"temperature": 0})

    def baseline(case, received_config):
        assert received_config == config
        if case.case_id == "case-error":
            return ReplayOutput(status="error", error="provider timeout")
        return ReplayOutput(status="completed", input_tokens=100, output_tokens=10, answer_fact_ids=("fact-logs",))

    def optimized(case, received_config):
        if case.case_id == "case-error":
            return ReplayOutput(status="completed", input_tokens=50, output_tokens=8, cache_outcomes=("miss",))
        return ReplayOutput(
            status="completed", input_tokens=60, output_tokens=10, cache_outcomes=("hit", "bypass"),
            selected_memory_ids=("memory-logs",), answer_fact_ids=("fact-logs",),
        )

    store = SQLiteEvaluationStore(database)
    summary = MemoryEvaluationHarness(baseline, optimized, store).run(cases, config)
    persisted = store.load_run("run-1")

    assert summary.paired_success_count == 1
    assert summary.execution_error_count == 1
    assert summary.token_reduction.value == pytest.approx(0.4)
    assert summary.cache_hit_rate.value == pytest.approx(1.0)
    assert summary.cache_hit_rate.excluded == 1
    assert summary.accuracy.value == pytest.approx(1.0)
    assert summary.accuracy.ground_truth_sources == ("human",)
    assert persisted["summary"]["paired_success_count"] == 1
    assert len(persisted["cases"]) == 2
    assert len(persisted["results"]) == 4


def test_llm_draft_ground_truth_does_not_enter_accuracy_gate(tmp_path):
    from antisentinel.evaluation.harness import MemoryEvaluationHarness
    from antisentinel.evaluation.models import EvaluationCase, EvaluationConfig, GroundTruthFact, ReplayOutput
    from antisentinel.persistence.sqlite_database import SQLiteDatabase
    from antisentinel.persistence.sqlite_stores import SQLiteEvaluationStore

    database = SQLiteDatabase(tmp_path / "antisentinel.db"); database.initialize()
    case = EvaluationCase(
        "case-draft", "incident-1", "session-1", "query", "2026-09-04T00:00:00Z",
        (GroundTruthFact("draft-fact", "草稿事实", "llm_draft"),),
    )
    runner = lambda case, config: ReplayOutput(status="completed", input_tokens=10, answer_fact_ids=("draft-fact",))
    summary = MemoryEvaluationHarness(runner, runner, SQLiteEvaluationStore(database)).run(
        [case], EvaluationConfig("run-draft", "model", "prompt-v1", "provider", {}),
    )

    assert summary.accuracy.value is None
    assert summary.accuracy.ground_truth_sources == ()


def test_evaluation_cli_persists_recorded_paired_outputs(tmp_path):
    import json
    from scripts.run_memory_evaluation import main
    from antisentinel.persistence.sqlite_database import SQLiteDatabase
    from antisentinel.persistence.sqlite_stores import SQLiteEvaluationStore

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps({
        "case_id": "case-1", "incident_id": "incident-1", "session_id": "session-1",
        "query": "先查什么", "cutoff_at": "2026-09-04T00:00:00Z",
        "facts": [{"fact_id": "fact-1", "text": "先看日志", "source": "human"}],
        "baseline": {"status": "completed", "input_tokens": 100, "answer_fact_ids": ["fact-1"]},
        "optimized": {"status": "completed", "input_tokens": 60, "cache_outcomes": ["hit"], "answer_fact_ids": ["fact-1"]},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    database_path = tmp_path / "antisentinel.db"

    exit_code = main([
        "--database", str(database_path), "--cases", str(cases_path), "--run-id", "run-cli",
        "--model", "deepseek-chat", "--provider", "deepseek", "--prompt-revision", "prompt-v1",
    ])

    database = SQLiteDatabase(database_path); database.initialize()
    persisted = SQLiteEvaluationStore(database).load_run("run-cli")
    assert exit_code == 0
    assert persisted["status"] == "completed"
    assert persisted["summary"]["token_reduction"]["value"] == pytest.approx(0.4)
