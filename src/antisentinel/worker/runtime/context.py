"""Build the redacted model context for one runtime turn."""

from __future__ import annotations

from typing import Any

from antisentinel.domain.incident import Incident
from antisentinel.domain.session import Session
from antisentinel.domain.turn import Turn
from antisentinel.ports.model import ModelRequest
from antisentinel.memory.models import MemoryContextView

from .messages import build_system_message, build_task_results_message


class ContextBuilder:
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
    ) -> ModelRequest:
        messages: list[dict[str, Any]] = [build_system_message()]
        messages.append(
            {
                "role": "user",
                "incident": {
                    "incident_id": incident.incident_id,
                    "title": incident.title,
                    "source": incident.source,
                    "status": incident.status.value,
                    "summary": incident.summary,
                },
                "session": {
                    "session_id": session.session_id,
                    "incident_id": session.incident_id,
                    "status": session.status.value,
                    "summary": session.summary,
                },
                "turn": {"turn_id": turn.turn_id, "status": turn.status.value},
                "prior_turns": [
                    {
                        "turn_id": prior.turn_id,
                        "status": prior.status.value,
                        "summary": prior.output_summary or prior.context_summary,
                    }
                    for prior in prior_turns
                ],
            }
        )
        if task_results:
            messages.append(build_task_results_message(task_results))
        if memory_context is not None and (memory_context.digest or memory_context.evidence_refs):
            messages.append(
                {
                    "role": "memory",
                    "session_digest": memory_context.digest,
                    "evidence_refs": list(memory_context.evidence_refs),
                }
            )
        return ModelRequest(
            incident_id=incident.incident_id,
            session_id=session.session_id,
            turn_id=turn.turn_id,
            messages=messages,
            tools=tools,
        )
