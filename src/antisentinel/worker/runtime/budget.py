"""Global context token budget: estimate, allocate, report (PRD-002B Phase B)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from math import ceil, floor
from pathlib import PurePosixPath
from typing import Any


ESTIMATE_VERSION = "utf8_ceil_div3_v1"
# DeepSeek Real calib (wire): ratio≈0.84 → scale ≈1.2 so pack totals are not optimistic.
DEFAULT_WIRE_SAFETY_SCALE = 1.2
TRUNCATION_MARKER = " [truncated]"
SOURCE_TRUNCATION_LINE = "# ... [truncated] ..."

# Structural floors (fractions of max). Prevent empty critical channels; not hard ceilings.
DEFAULT_FLOOR_SHARES: dict[str, float] = {
    "system": 0.05,
    "working": 0.04,
    "memory": 0.02,
    "source": 0.0,
    "skill": 0.03,
    "tools": 0.08,
}

# Soft preferences for reporting + anti-S2 source soft-cap / refill weights (NOT hard allocate).
# Source soft-cap kept modest: CodeMap-first pins pointers; bodies are ephemeral windows.
DEFAULT_SOFT_PREFS: dict[str, float] = {
    "system": 0.05,
    "working": 0.30,
    "memory": 0.12,
    "source": 0.25,
    "skill": 0.15,
    "tools": 0.15,
}

# Back-compat alias (callers may still pass shares= as soft prefs).
DEFAULT_SHARES = DEFAULT_SOFT_PREFS

BLOCK_NAMES = ("system", "working", "memory", "source", "skill", "tools")


def estimate_tokens(text: str, *, version: str = ESTIMATE_VERSION) -> int:
    if version != ESTIMATE_VERSION:
        raise ValueError(f"unsupported estimate_version: {version}")
    if not text:
        return 0
    return ceil(len(text.encode("utf-8")) / 3)


def estimate_json(value: object, *, version: str = ESTIMATE_VERSION) -> int:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return estimate_tokens(encoded, version=version)


def to_provider_wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mirror OpenAI-compatible adapter serialization used on the wire.

    Structured AntiSentinel roles are wrapped as user/system text with a prefix + JSON body.
    Budget totals must estimate this form; estimating raw structured dicts under-counts (~0.6×).
    """
    result: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        if isinstance(message.get("content"), str) and set(message.keys()) <= {"role", "content"}:
            result.append({"role": role, "content": message["content"]})
            continue
        if isinstance(message.get("content"), str) and role == "system":
            result.append({"role": "system", "content": message["content"]})
            continue
        structured = {key: value for key, value in message.items() if key != "role"}
        if list(structured.keys()) == ["content"] and isinstance(structured.get("content"), str):
            result.append({"role": role if role in {"system", "assistant", "user"} else "user", "content": structured["content"]})
            continue
        prefix = "[AntiSentinel tool results]\n" if role == "tool" else "[AntiSentinel context]\n"
        provider_role = role if role in {"system", "assistant", "user"} else "user"
        # Match adapter: json.dumps without sort_keys/compact separators.
        result.append({"role": provider_role, "content": prefix + json.dumps(structured, ensure_ascii=False)})
    return result


def to_provider_wire_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    out: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "")
        if not all(character.isalnum() or character in "_-" for character in name):
            name = "antisentinel_" + "".join(
                character if character.isalnum() or character in "_-" else "_" for character in name
            )
        aliases = {"skill.load": "antisentinel_skill_load", "skill.read_reference": "antisentinel_skill_read_reference"}
        name = aliases.get(str(tool.get("name") or ""), name)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("argument_schema", {"type": "object", "properties": {}}),
                },
            }
        )
    return out


def estimate_model_request(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    version: str = ESTIMATE_VERSION,
    safety_scale: float = DEFAULT_WIRE_SAFETY_SCALE,
) -> int:
    """Estimate tokens for the provider wire payload (messages + tools).

    Applies safety_scale so budget gates are not optimistic vs provider usage.
    """
    from math import ceil

    wire_messages = to_provider_wire_messages(messages)
    wire_tools = to_provider_wire_tools(tools)
    total = estimate_json(wire_messages, version=version)
    if wire_tools:
        total += estimate_json(wire_tools, version=version)
    if safety_scale <= 0:
        raise ValueError("safety_scale must be positive")
    if safety_scale == 1.0:
        return total
    return ceil(total * safety_scale)


def apply_truncation_marker(text: str, *, marker: str = TRUNCATION_MARKER) -> str:
    trimmed = (text or "").rstrip()
    if not trimmed:
        return marker.strip()
    if trimmed.endswith(marker.strip()) or trimmed.endswith(marker):
        return trimmed
    return trimmed + marker


@dataclass
class BlockUsage:
    used: int = 0
    limit: int = 0
    truncated: bool = False
    dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DroppedItem:
    kind: str
    reason: str
    ref: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetReport:
    estimate_version: str
    max_context_tokens: int
    strict: bool
    blocks: dict[str, BlockUsage] = field(default_factory=dict)
    truncated_slices: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)
    compacted_turns: int = 0
    recent_turn_count: int = 0
    tool_event_count: int = 0
    total_estimated: int = 0
    within_budget: bool = True
    packing_mode: str = "floors_degrade"
    degrade_steps: list[str] = field(default_factory=list)

    def to_event_payload(self) -> dict[str, Any]:
        """Desensitized summary for context.built (no payloads/secrets)."""
        return {
            "estimate_version": self.estimate_version,
            "max_context_tokens": self.max_context_tokens,
            "total_estimated": self.total_estimated,
            "within_budget": self.within_budget,
            "strict": self.strict,
            "packing_mode": self.packing_mode,
            "degrade_step_count": len(self.degrade_steps),
            "blocks": {name: usage.to_dict() for name, usage in self.blocks.items()},
            "truncated_slice_count": len(self.truncated_slices),
            "dropped_count": len(self.dropped),
            "dropped_reasons": sorted({item.reason for item in self.dropped}),
            "compacted_turns": self.compacted_turns,
            "recent_turn_count": self.recent_turn_count,
            "tool_event_count": self.tool_event_count,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.to_event_payload(),
            "truncated_slices": list(self.truncated_slices),
            "dropped": [item.to_dict() for item in self.dropped],
        }


@dataclass(frozen=True)
class ContextBudget:
    max_context_tokens: int
    recent_turn_limit: int = 3
    min_recent_turns: int = 1
    # Soft prefs (anti-S2 / reporting). Formerly hard shares; kept name for callers.
    shares: dict[str, float] | None = None
    floor_shares: dict[str, float] | None = None
    estimate_version: str = ESTIMATE_VERSION
    strict: bool = True
    max_source_slices: int = 4
    source_window_radius: int = 40
    source_window_by_suffix: dict[str, int] | None = None
    max_older_digest_tokens: int | None = None
    tools_min_share: float = 0.08
    tools_core_limit: int = 8
    truncation_marker: str = TRUNCATION_MARKER
    # After pack + degrade, refill elastic channels until wire estimate reaches this fraction of max.
    # Keep below 1.0 so we do not spend tokens just to "fill" the window.
    target_fill_ratio: float = 0.65
    # ephemeral: bodies only while body_ttl>0; sticky_bodies: keep content across turns (legacy).
    source_pin_policy: str = "ephemeral"
    source_body_ttl_turns: int = 1
    max_source_pins: int = 8

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_context_tokens, bool)
            or not isinstance(self.max_context_tokens, int)
            or self.max_context_tokens < 1
        ):
            raise ValueError("max_context_tokens must be a positive integer")
        if (
            isinstance(self.recent_turn_limit, bool)
            or not isinstance(self.recent_turn_limit, int)
            or self.recent_turn_limit < 1
        ):
            raise ValueError("recent_turn_limit must be a positive integer")
        if (
            isinstance(self.min_recent_turns, bool)
            or not isinstance(self.min_recent_turns, int)
            or self.min_recent_turns < 1
        ):
            raise ValueError("min_recent_turns must be >= 1")
        if self.min_recent_turns > self.recent_turn_limit:
            raise ValueError("min_recent_turns cannot exceed recent_turn_limit")
        if self.estimate_version != ESTIMATE_VERSION:
            raise ValueError(f"unsupported estimate_version: {self.estimate_version}")
        if not 0 <= self.tools_min_share < 1:
            raise ValueError("tools_min_share must be in [0, 1)")
        if self.tools_core_limit < 0:
            raise ValueError("tools_core_limit must be non-negative")
        if not 0.0 < self.target_fill_ratio <= 1.0:
            raise ValueError("target_fill_ratio must be in (0, 1]")
        if self.source_pin_policy not in {"ephemeral", "sticky_bodies"}:
            raise ValueError("source_pin_policy must be 'ephemeral' or 'sticky_bodies'")
        if (
            isinstance(self.source_body_ttl_turns, bool)
            or not isinstance(self.source_body_ttl_turns, int)
            or self.source_body_ttl_turns < 0
        ):
            raise ValueError("source_body_ttl_turns must be a non-negative integer")
        if (
            isinstance(self.max_source_pins, bool)
            or not isinstance(self.max_source_pins, int)
            or self.max_source_pins < 1
        ):
            raise ValueError("max_source_pins must be a positive integer")
        prefs = self.resolved_soft_prefs()
        for key in BLOCK_NAMES:
            if key not in prefs:
                raise ValueError(f"soft prefs missing block: {key}")
        floors = self.resolved_floor_shares()
        for key in BLOCK_NAMES:
            if key not in floors:
                raise ValueError(f"floor_shares missing block: {key}")
        if sum(float(floors[k]) for k in BLOCK_NAMES) >= 1.0 - 1e-9:
            raise ValueError("floor shares must sum to < 1")

    def resolved_floor_shares(self) -> dict[str, float]:
        base = dict(DEFAULT_FLOOR_SHARES)
        if self.floor_shares:
            base.update(self.floor_shares)
        base["tools"] = float(self.tools_min_share)
        return base

    def resolved_soft_prefs(self) -> dict[str, float]:
        base = dict(DEFAULT_SOFT_PREFS)
        if self.shares:
            base.update(self.shares)
        return base

    def resolved_shares(self) -> dict[str, float]:
        """Back-compat alias for soft prefs."""
        return self.resolved_soft_prefs()

    def floors(self) -> dict[str, int]:
        """Minimum tokens reserved per channel (structure guarantee)."""
        shares = self.resolved_floor_shares()
        max_tokens = self.max_context_tokens
        return {name: max(0, floor(max_tokens * float(shares[name]))) for name in BLOCK_NAMES}

    def soft_caps(self) -> dict[str, int]:
        """Soft preferred ceilings (source soft-cap prevents S2 on first pack)."""
        prefs = self.resolved_soft_prefs()
        floor_limits = self.floors()
        max_tokens = self.max_context_tokens
        caps: dict[str, int] = {}
        for name in BLOCK_NAMES:
            preferred = floor(max_tokens * float(prefs.get(name, 0.0)))
            caps[name] = max(floor_limits[name], preferred)
        return caps

    def allocate(self) -> dict[str, int]:
        """Soft caps for reporting / first-pass hints. Not hard exclusive partitions."""
        return self.soft_caps()

    def source_hard_max(self) -> int:
        """Absolute source ceiling after reserving other channel floors."""
        floor_limits = self.floors()
        reserved = sum(floor_limits[name] for name in ("system", "working", "memory", "skill", "tools"))
        return max(0, self.max_context_tokens - reserved)

    def window_radius_for_path(self, path: str) -> int:
        mapping = self.source_window_by_suffix or {}
        suffix = PurePosixPath(path or "").suffix.lower()
        if suffix in mapping:
            return int(mapping[suffix])
        return self.source_window_radius

    def preflight_system(self, system_text: str) -> None:
        limit = self.floors()["system"]
        used = estimate_tokens(system_text, version=self.estimate_version)
        if used > limit:
            raise ValueError(
                f"system prompt exceeds system budget: used={used} limit={limit} "
                f"(max_context_tokens={self.max_context_tokens}); fix config before run"
            )

    def empty_report(self) -> BudgetReport:
        limits = self.soft_caps()
        return BudgetReport(
            estimate_version=self.estimate_version,
            max_context_tokens=self.max_context_tokens,
            strict=self.strict,
            packing_mode="floors_degrade",
            blocks={name: BlockUsage(used=0, limit=limits[name]) for name in BLOCK_NAMES},
        )


def default_budget(
    *,
    max_context_tokens: int = 0,
    fake: bool = False,
    model: str | None = None,
) -> ContextBudget:
    from .model_profile import resolve_max_context_tokens

    if max_context_tokens > 0:
        resolved = max_context_tokens
    elif fake:
        resolved = 8192
    else:
        resolved = resolve_max_context_tokens(model=model)
    return ContextBudget(
        max_context_tokens=resolved,
        source_window_by_suffix={".py": 40, ".ts": 40, ".js": 40, ".json": 20, ".md": 30, ".yaml": 25, ".yml": 25},
    )


def calibrate_estimate_vs_usage(
    *,
    estimated: int,
    input_tokens: int,
) -> dict[str, Any]:
    """Single-sample calibration record (assemble estimate vs provider usage)."""
    if input_tokens < 0 or estimated < 0:
        raise ValueError("token counts must be non-negative")
    error = estimated - input_tokens
    ratio = (estimated / input_tokens) if input_tokens else None
    return {
        "estimate_version": ESTIMATE_VERSION,
        "estimated": estimated,
        "input_tokens": input_tokens,
        "error": error,
        "abs_error": abs(error),
        "ratio": ratio,
    }
