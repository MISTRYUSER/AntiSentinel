import pytest
from typing import get_type_hints

from antisentinel.domain.errors import InvalidInputError
from antisentinel.ports.model import (
    ModelError,
    ModelPort,
    ModelTimeoutError,
    parse_model_response,
)


def test_parse_final_response():
    response = parse_model_response(
        {
            "final": {
                "summary": "Database connection pool is exhausted",
                "diagnosis": "Traffic spike caused pool saturation",
                "confidence": 0.9,
                "evidence_refs": [{"evidence_id": "ev-1", "role": "supporting"}],
            }
        }
    )

    assert response.final is not None
    assert response.final.summary == "Database connection pool is exhausted"
    assert response.final.confidence == 0.9
    assert response.final.evidence_refs[0].evidence_id == "ev-1"
    assert response.tasks == ()


def test_parse_multiple_tasks_and_multiple_tool_calls():
    response = parse_model_response(
        {
            "tasks": [
                {
                    "task_id": "task-1",
                    "objective": "Inspect service health",
                    "tool_calls": [
                        {"tool_name": "read_health", "arguments": {"service": "api"}},
                        {"tool_name": "read_logs", "arguments": {"service": "api", "limit": 20}, "target_ref": "pod/api-1"},
                    ],
                },
                {"task_id": "task-2", "objective": "Inspect database", "tool_calls": []},
            ]
        }
    )

    assert len(response.tasks) == 2
    assert response.tasks[0].tool_calls[1].target_ref == "pod/api-1"
    assert response.tasks[0].tool_calls[1].arguments["limit"] == 20


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"final": {}, "tasks": []},
        {"final": {"summary": "x", "diagnosis": "y", "confidence": 2, "evidence_refs": []}},
        {"tasks": [{"task_id": "t", "objective": "inspect", "tool_calls": [{"tool_name": "", "arguments": {}}]}]},
        {"tasks": [{"task_id": "t", "objective": "inspect", "tool_calls": [{"tool_name": "x", "arguments": {"bad": object()}}]}]},
    ],
)
def test_invalid_model_output_is_rejected(payload):
    with pytest.raises(InvalidInputError):
        parse_model_response(payload)


def test_duplicate_task_ids_and_limits_are_rejected():
    duplicate = {"tasks": [{"task_id": "same", "objective": "one", "tool_calls": []}, {"task_id": "same", "objective": "two", "tool_calls": []}]}
    with pytest.raises(InvalidInputError):
        parse_model_response(duplicate)

    too_many = {"tasks": [{"task_id": str(i), "objective": "inspect", "tool_calls": []} for i in range(3)]}
    with pytest.raises(InvalidInputError):
        parse_model_response(too_many, max_tasks=2)


def test_model_port_is_protocol_and_errors_are_provider_neutral():
    assert get_type_hints(getattr(ModelPort, "complete"))["return"].__name__ == "ModelResponse"
    assert issubclass(ModelTimeoutError, ModelError)
