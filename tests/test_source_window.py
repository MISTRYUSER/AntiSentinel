"""Source window truncation tests."""

from antisentinel.worker.runtime.budget import SOURCE_TRUNCATION_LINE, ContextBudget
from antisentinel.worker.runtime.source_window import window_source_content


def test_window_source_no_op_when_under_budget():
    budget = ContextBudget(max_context_tokens=50_000)
    content = "def f():\n    return 1\n"
    result = window_source_content(content, budget=budget, path="a.py", token_limit=1000)
    assert result.truncated is False
    assert result.content == content


def test_window_applies_structure_even_when_tokens_fit():
    """Anti-S2 / anti-midfile-leak: generous token budget must not skip windowing."""
    budget = ContextBudget(max_context_tokens=50_000, source_window_radius=5)
    lines = [f"line-{index}\n" for index in range(1, 201)]
    lines[100] = "# SECRET_SHOULD_NOT_LEAK\n"
    content = "".join(lines)
    result = window_source_content(content, budget=budget, path="a.py", token_limit=50_000)
    assert result.truncated is True
    assert "SECRET_SHOULD_NOT_LEAK" not in result.content
    assert SOURCE_TRUNCATION_LINE in result.content


def test_window_source_inserts_model_readable_marker():
    budget = ContextBudget(max_context_tokens=50_000, source_window_radius=2)
    lines = [f"line-{index}\n" for index in range(1, 101)]
    content = "".join(lines)
    result = window_source_content(content, budget=budget, path="a.py", token_limit=80, hit_line=50)
    assert result.truncated is True
    assert SOURCE_TRUNCATION_LINE in result.content or "[truncated]" in result.content
    assert result.after_tokens <= 80
    assert result.after_tokens < result.before_tokens


def test_json_suffix_uses_smaller_default_radius_via_budget_map():
    budget = ContextBudget(
        max_context_tokens=50_000,
        source_window_radius=40,
        source_window_by_suffix={".json": 5},
    )
    assert budget.window_radius_for_path("x.json") == 5
    lines = [f'{{"k": {index}}}\n' for index in range(100)]
    result = window_source_content("".join(lines), budget=budget, path="x.json", token_limit=200, hit_line=50)
    assert result.truncated is True
    # Tight radius → fewer lines than py default would keep.
    assert result.content.count("\n") < 40
