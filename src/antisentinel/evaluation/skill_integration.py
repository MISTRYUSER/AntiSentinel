"""Deterministic contract verification for local Skill integration cases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


@dataclass(frozen=True)
class ContractOutcome:
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class FrozenSkillCase:
    case_id: str
    category: str
    input: str
    fixture_id: str
    verifier_id: str


@dataclass(frozen=True)
class TrialRecord:
    case_id: str
    group: str
    trial_id: int
    status: str
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    trace_id: str


@dataclass(frozen=True)
class TrialSummary:
    total_trials: int
    successful_trials: int
    timeouts: int
    success_rate: float


def summarize_trials(records: list[TrialRecord]) -> TrialSummary:
    if not records:
        raise ValueError("trial records must not be empty")
    allowed = {"passed", "failed", "timeout", "infrastructure_error"}
    if any(record.status not in allowed for record in records):
        raise ValueError("invalid trial status")
    passed = sum(record.status == "passed" for record in records)
    return TrialSummary(len(records), passed, sum(record.status == "timeout" for record in records), passed / len(records))


def load_frozen_cases(path: Path) -> tuple[FrozenSkillCase, ...]:
    values = json.loads(path.read_text(encoding="utf-8"))
    required = {"case_id", "category", "input", "fixture_id", "verifier_id"}
    if not isinstance(values, list) or any(not isinstance(item, dict) or not required <= set(item) for item in values):
        raise ValueError("invalid frozen skill cases")
    return tuple(FrozenSkillCase(item["case_id"], item["category"], item["input"], item["fixture_id"], item["verifier_id"]) for item in values)


def verify_contract(contract: dict[str, Any], observed: dict[str, Any]) -> ContractOutcome:
    failures: list[str] = []
    tool_names = set(observed.get("tool_names", []))
    if contract.get("should_load") and observed.get("loaded_skill_id") not in set(contract.get("allowed_skill_ids", [])):
        failures.append("selected_skill")
    for required in contract.get("required_tool_assertions", []):
        if required not in tool_names:
            failures.append(required)
    for forbidden in contract.get("forbidden_tool_names", []):
        if forbidden in tool_names:
            failures.append(forbidden)
    for assertion in contract.get("expected_result_assertions", []):
        if assertion == "uses_actual_health_evidence" and not observed.get("health_evidence"):
            failures.append(assertion)
        if assertion == "no_write_tool" and any(name.startswith(("restart", "write", "delete")) for name in tool_names):
            failures.append(assertion)
        if assertion == "no_skill_load" and observed.get("loaded_skill_id") is not None:
            failures.append(assertion)
    return ContractOutcome(not failures, tuple(failures))
