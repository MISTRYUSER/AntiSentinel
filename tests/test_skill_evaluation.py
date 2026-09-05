from pathlib import Path


def test_frozen_cases_cover_all_required_skill_categories():
    from antisentinel.evaluation.skill_integration import load_frozen_cases
    cases = load_frozen_cases(Path(__file__).parents[1] / "src" / "antisentinel" / "evaluation" / "skill_cases.json")
    assert {case.category for case in cases} == {"explicit_match", "distractor", "insufficient", "no_match", "explicit_selection", "execution", "engineering"}


def test_trial_summary_keeps_failures_and_timeouts_in_denominator():
    from antisentinel.evaluation.skill_integration import TrialRecord, summarize_trials
    records = [
        TrialRecord("case", "A", 1, "passed", 10, 2, 1.0, "trace-1"),
        TrialRecord("case", "A", 2, "failed", 11, 3, 2.0, "trace-2"),
        TrialRecord("case", "A", 3, "timeout", 12, 0, 120.0, "trace-3"),
    ]
    summary = summarize_trials(records)
    assert (summary.total_trials, summary.successful_trials, summary.timeouts) == (3, 1, 1)
    assert summary.success_rate == 1 / 3
