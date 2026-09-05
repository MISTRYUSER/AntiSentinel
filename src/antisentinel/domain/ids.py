"""Stable identifiers used across AntiSentinel aggregates."""

from typing import NewType

IncidentId = NewType("IncidentId", str)
SessionId = NewType("SessionId", str)
TaskId = NewType("TaskId", str)
StepId = NewType("StepId", str)
AttemptId = NewType("AttemptId", str)
TurnId = NewType("TurnId", str)
ToolCallId = NewType("ToolCallId", str)
EvidenceId = NewType("EvidenceId", str)
EventId = NewType("EventId", str)
