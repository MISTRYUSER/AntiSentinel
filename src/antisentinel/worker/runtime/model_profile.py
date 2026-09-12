"""Model context-window profiles → usable input token budget."""

from __future__ import annotations

from math import floor

# Conservative known windows (tokens). Prefer override / provider discovery when available.
KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "fake": 32_000,
    "deepseek-chat": 65_536,
    "deepseek-reasoner": 65_536,
    "deepseek-v3": 65_536,
    "deepseek-v4-flash": 65_536,
    "deepseek-v4": 65_536,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "o4-mini": 200_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-sonnet-4": 200_000,
}

FALLBACK_CONTEXT_WINDOW = 32_000
DEFAULT_INPUT_RATIO = 0.85
DEFAULT_MAX_OUTPUT_RESERVE = 4_096


def lookup_context_window(model: str | None) -> int | None:
    if not model or not str(model).strip():
        return None
    key = str(model).strip().lower()
    if key in KNOWN_CONTEXT_WINDOWS:
        return KNOWN_CONTEXT_WINDOWS[key]
    # Longest-prefix / substring match for vendor variants.
    best: tuple[int, int] | None = None  # (name_len, window)
    for name, window in KNOWN_CONTEXT_WINDOWS.items():
        if key == name or key.startswith(name) or name in key:
            if best is None or len(name) > best[0]:
                best = (len(name), window)
    return best[1] if best else None


def resolve_max_context_tokens(
    *,
    model: str | None = None,
    override: int | None = None,
    context_window: int | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_RESERVE,
    input_ratio: float = DEFAULT_INPUT_RATIO,
    fallback_window: int = FALLBACK_CONTEXT_WINDOW,
) -> int:
    """Usable input budget: override > derived(window) > fallback.

    usable = floor(window * input_ratio) - max_output_tokens
    """
    if override is not None:
        if isinstance(override, bool) or not isinstance(override, int) or override < 1:
            raise ValueError("override must be a positive integer")
        return override
    if not 0.0 < float(input_ratio) <= 1.0:
        raise ValueError("input_ratio must be in (0, 1]")
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 0:
        raise ValueError("max_output_tokens must be a non-negative integer")

    window = context_window if context_window and context_window > 0 else lookup_context_window(model)
    if window is None or window < 1:
        window = fallback_window
    usable = floor(window * float(input_ratio)) - int(max_output_tokens)
    return max(1_024, usable)
