"""Source slice window truncation for context pack (PRD-002B Phase B)."""

from __future__ import annotations

from dataclasses import dataclass

from .budget import SOURCE_TRUNCATION_LINE, ContextBudget, estimate_tokens


@dataclass
class WindowedSource:
    content: str
    truncated: bool
    before_tokens: int
    after_tokens: int


def window_source_content(
    content: str,
    *,
    budget: ContextBudget,
    path: str,
    token_limit: int,
    hit_line: int | None = None,
) -> WindowedSource:
    """Fit source text into token_limit with structural windowing.

    Always applies hit±radius or head+tail when the file is longer than the
    structural window — even if the raw file would fit the token budget. This
    prevents mid-file bloat / secret leakage when soft caps are generous.
    """
    before = estimate_tokens(content, version=budget.estimate_version)
    if token_limit <= 0:
        return WindowedSource(content="", truncated=True, before_tokens=before, after_tokens=0)

    lines = content.splitlines(keepends=True)
    if not lines:
        return WindowedSource(content=content, truncated=False, before_tokens=before, after_tokens=before)

    radius = budget.window_radius_for_path(path)
    structural_truncated = False

    if hit_line is not None and 1 <= hit_line <= len(lines):
        start = max(0, hit_line - 1 - radius)
        end = min(len(lines), hit_line + radius)
        chunk = "".join(lines[start:end])
        if start > 0 or end < len(lines):
            prefix = SOURCE_TRUNCATION_LINE + "\n" if start > 0 else ""
            suffix = "\n" + SOURCE_TRUNCATION_LINE if end < len(lines) else ""
            chunk = prefix + chunk + suffix
            structural_truncated = True
    else:
        # Default head+tail window (≈ radius lines each side, capped).
        head_n = min(max(radius, 1), len(lines))
        tail_n = min(max(radius, 1), max(0, len(lines) - head_n))
        if head_n + tail_n >= len(lines):
            chunk = "".join(lines)
        else:
            chunk = "".join(lines[:head_n]) + SOURCE_TRUNCATION_LINE + "\n" + "".join(lines[-tail_n:])
            structural_truncated = True

    token_truncated = False
    if estimate_tokens(chunk, version=budget.estimate_version) > token_limit:
        chunk = _fit_tokens(chunk, token_limit, version=budget.estimate_version)
        token_truncated = True

    after = estimate_tokens(chunk, version=budget.estimate_version)
    truncated = structural_truncated or token_truncated or after < before
    return WindowedSource(content=chunk, truncated=truncated, before_tokens=before, after_tokens=after)


def _fit_tokens(text: str, token_limit: int, *, version: str) -> str:
    if estimate_tokens(text, version=version) <= token_limit:
        return text
    # Leave room for a trailing truncation marker line when needed.
    marker = "\n" + SOURCE_TRUNCATION_LINE
    marker_tokens = estimate_tokens(marker, version=version)
    body_limit = max(0, token_limit - marker_tokens)
    max_bytes = max(0, body_limit * 3)
    encoded = text.encode("utf-8")
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    if not truncated:
        # Extremely tight budget: prefer marker-only signal over silent empty drop upstream.
        return SOURCE_TRUNCATION_LINE if estimate_tokens(SOURCE_TRUNCATION_LINE, version=version) <= token_limit else ""
    if SOURCE_TRUNCATION_LINE not in truncated:
        truncated = truncated + marker
    # Final safety clip if marker push still drifts over (UTF-8 boundary).
    if estimate_tokens(truncated, version=version) > token_limit:
        max_bytes = max(0, token_limit * 3)
        truncated = truncated.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    return truncated
