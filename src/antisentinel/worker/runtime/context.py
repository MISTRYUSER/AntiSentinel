"""Build the redacted model context for one runtime turn via shared pack."""

from __future__ import annotations

from typing import Any, Callable

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.turn import Turn
from antisentinel.ports.model import ModelRequest
from antisentinel.memory.models import MemoryContextView

from .budget import BudgetReport, ContextBudget, default_budget
from .pack import PackInput, SoftBudget, pack_context
from .working_set import WorkingSet


class ContextBuilder:
    def __init__(
        self,
        *,
        system_override: str | None = None,
        benchmark_user_prompt: bool = False,
        budget: ContextBudget | None = None,
        soft_budget: SoftBudget | None = None,
    ) -> None:
        self.system_override = system_override
        self.benchmark_user_prompt = benchmark_user_prompt
        self.budget = budget
        self.soft_budget = soft_budget or SoftBudget()

    def build(
        self,
        incident: Incident,
        session: Session,
        turn: Turn,
        *,
        prior_turns: list[Turn],
        task_results: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        memory_context: MemoryContextView | None = None,
        skill_context: dict[str, Any] | None = None,
        source_context: list[Any] | None = None,
        working_set: WorkingSet | None = None,
        budget: ContextBudget | None = None,
        on_budget_report: Callable[[BudgetReport], None] | None = None,
    ) -> ModelRequest:
        resolved = budget or self.budget or default_budget(max_context_tokens=0, fake=False)
        result = pack_context(
            PackInput(
                incident=incident,
                session=session,
                turn=turn,
                working_set=working_set,
                tools=tools,
                prior_turns=prior_turns,
                task_results=task_results,
                memory_context=memory_context,
                skill_context=skill_context,
                source_context=source_context,
                system_override=self.system_override,
                benchmark_user_prompt=self.benchmark_user_prompt,
                budget=resolved,
                soft_budget=self.soft_budget,
            )
        )
        if on_budget_report is not None:
            on_budget_report(result.report)
        return ModelRequest(
            incident_id=incident.incident_id,
            session_id=session.session_id,
            turn_id=turn.turn_id,
            messages=result.messages,
            tools=result.tools,
        )
