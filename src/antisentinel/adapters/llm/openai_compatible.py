"""OpenAI-compatible HTTP model adapter and deterministic Fake Provider."""

from __future__ import annotations

from collections.abc import Iterable
import json
import logging
from dataclasses import replace
from uuid import uuid4
from typing import Any

import httpx

from antisentinel.ports.model import (
    ModelError,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelTimeoutError,
    parse_model_response,
    TokenUsage,
)
from antisentinel.tracing.telemetry import Telemetry
from opentelemetry import trace

logger = logging.getLogger(__name__)


class OpenAICompatibleModelAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        if not base_url.strip() or not api_key.strip() or not model.strip():
            raise ValueError("base_url, api_key, and model must be non-empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout)
        self.telemetry = telemetry or Telemetry(service_name="antisentinel.model")

    def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            payload = {
                "model": self.model,
                "messages": _provider_messages(request.messages),
                "tools": [_provider_tool(tool) for tool in request.tools],
            }
            provider_tool_names = {_provider_tool_name(tool["name"]): tool["name"] for tool in request.tools}
            has_tool_results = any(message.get("role") == "tool" for message in request.messages)
            duplicate_feedback = any("Duplicate tool call" in str(message) for message in request.messages if message.get("role") == "tool")
            if not request.tools or duplicate_feedback:
                payload["response_format"] = {"type": "json_object"}
            if duplicate_feedback:
                payload["tool_choice"] = "none"
                payload["messages"].append({"role": "user", "content": "The previous call was already completed. Return ONLY the final JSON schema using the existing result."})
            elif has_tool_results and request.tools:
                payload["messages"].append({
                    "role": "user",
                    "content": "Review the tool results. If another distinct tool call is necessary, return the tasks schema; otherwise return ONLY the final JSON schema. Never repeat the same tool and arguments.",
                })
            if "deepseek" in self.base_url.lower():
                payload["thinking"] = {"type": "disabled"}
            current = trace.get_current_span()
            if current.get_span_context().is_valid:
                response = self.client.post(self._completion_url(), headers={"Authorization": f"Bearer {self.api_key}"}, json=payload, timeout=self.timeout)
            else:
                with self.telemetry.span("model.complete", request_id=f"model-{uuid4()}", model=self.model):
                    response = self.client.post(self._completion_url(), headers={"Authorization": f"Bearer {self.api_key}"}, json=payload, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError("model request timed out") from exc
        except httpx.RequestError as exc:
            raise ModelError(f"model request failed: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise ModelError(_provider_http_error(response))
        try:
            body = response.json()
            message = body["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("invalid_provider_response") from exc
        if message.get("tool_calls"):
            logger.info("provider_response_shape", extra={"response_keys": sorted(message.keys()), "has_tool_calls": True, "content_length": len(message.get("content") or "")})
            usage = _parse_usage(body.get("usage"))
            _set_usage_attributes(current, usage)
            return _parse_native_tool_calls(message["tool_calls"], usage, provider_tool_names)
        content = message.get("content")
        if content is None:
            raise ModelError("invalid_provider_response")
        logger.info("provider_response_shape", extra={"response_keys": sorted(message.keys()), "has_tool_calls": False, "content_length": len(content) if isinstance(content, str) else None})
        if isinstance(content, str):
            try:
                content = _parse_json_content(content)
            except ValueError as exc:
                raise ModelError("invalid_provider_json") from exc
        try:
            usage = _parse_usage(body.get("usage"))
            _set_usage_attributes(current, usage)
            return parse_model_response(content, usage=usage)
        except Exception as exc:  # noqa: BLE001 - normalize parser boundary errors
            if has_tool_results and content:
                try:
                    repaired, repair_usage = self._repair_structured_response(str(content))
                    return replace(repaired, usage=usage.add(repair_usage))
                except Exception:
                    pass
            raise ModelError("invalid_model_output") from exc

    def _repair_structured_response(self, content: str) -> tuple[ModelResponse, TokenUsage]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Return only one valid JSON object: {\"final\":{\"summary\":\"short\",\"diagnosis\":\"short\",\"confidence\":0.0,\"evidence_refs\":[]}}. Convert the supplied answer into this exact schema."},
                {"role": "user", "content": content[:4000]},
            ],
            "tools": [], "tool_choice": "none", "response_format": {"type": "json_object"},
        }
        response = self.client.post(self._completion_url(), headers={"Authorization": f"Bearer {self.api_key}"}, json=payload, timeout=self.timeout)
        if response.status_code >= 400:
            raise ModelError(_provider_http_error(response))
        body = response.json(); raw = body["choices"][0]["message"].get("content")
        return parse_model_response(_parse_json_content(raw)), _parse_usage(body.get("usage"))

    def _completion_url(self) -> str:
        return self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"


class FakeProviderModel:
    def __init__(self, responses: Iterable[ModelResponse | dict[str, Any]]) -> None:
        self._responses = iter(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        try:
            response = next(self._responses)
        except StopIteration as exc:
            raise ModelError("fake provider has no response") from exc
        return response if isinstance(response, ModelResponse) else parse_model_response(
            response,
            usage=_parse_usage(response.get("usage") if isinstance(response, dict) else None),
        )


def _provider_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Map AntiSentinel's manifest shape to OpenAI-compatible function tools."""
    return {
        "type": "function",
        "function": {
            "name": _provider_tool_name(tool["name"]),
            "description": tool.get("description", ""),
            "parameters": tool.get("argument_schema", {"type": "object", "properties": {}}),
        },
    }


def _provider_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert AntiSentinel's structured context messages to chat messages."""
    result: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        if isinstance(message.get("content"), str):
            result.append({"role": role, "content": message["content"]})
            continue
        structured = {key: value for key, value in message.items() if key != "role"}
        prefix = "[AntiSentinel tool results]\n" if role == "tool" else "[AntiSentinel context]\n"
        provider_role = role if role in {"system", "assistant", "user"} else "user"
        result.append({"role": provider_role, "content": prefix + json.dumps(structured, ensure_ascii=False)})
    return result


def _provider_tool_name(name: str) -> str:
    return {"skill.load": "antisentinel_skill_load", "skill.read_reference": "antisentinel_skill_read_reference"}.get(name, name)


def _parse_native_tool_calls(tool_calls: object, usage: TokenUsage | None = None, provider_tool_names: dict[str, str] | None = None) -> ModelResponse:
    if not isinstance(tool_calls, list) or not tool_calls:
        raise ModelError("invalid_provider_tool_calls")
    planned_calls = []
    for item in tool_calls:
        if not isinstance(item, dict):
            raise ModelError("invalid_provider_tool_call")
        function = item.get("function")
        if not isinstance(function, dict):
            raise ModelError("invalid_provider_tool_call")
        try:
            arguments = function["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            planned_calls.append({
                "tool_name": (provider_tool_names or {}).get(function["name"], function["name"]),
                "arguments": arguments,
                "target_ref": None,
            })
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelError("invalid_provider_tool_call") from exc
    return parse_model_response({
        "tasks": [{
            "task_id": "provider-tool-task",
            "objective": "Execute the model-requested diagnostic tools",
            "tool_calls": planned_calls,
        }]
    }, usage=usage)


def _parse_usage(value: object) -> TokenUsage:
    if not isinstance(value, dict):
        return TokenUsage()
    details = value.get("completion_tokens_details")
    if not isinstance(details, dict):
        details = {}
    return TokenUsage(
        input_tokens=int(value.get("prompt_tokens", value.get("input_tokens", 0)) or 0),
        output_tokens=int(value.get("completion_tokens", value.get("output_tokens", 0)) or 0),
        cached_input_tokens=int(value.get("prompt_cache_hit_tokens", value.get("cached_tokens", value.get("cache_read_input_tokens", 0))) or 0),
        reasoning_tokens=int(value.get("reasoning_tokens", details.get("reasoning_tokens", 0)) or 0),
        tool_tokens=int(value.get("tool_tokens", 0) or 0),
    )


def _set_usage_attributes(span: object, usage: TokenUsage) -> None:
    if hasattr(span, "set_attribute"):
        for key, value in {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "tool_tokens": usage.tool_tokens,
        }.items():
            span.set_attribute(key, value)


def _parse_json_content(content: str) -> object:
    """Parse plain JSON or one complete Markdown JSON fence, never free text."""
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) < 3 or lines[0].strip() not in {"```", "```json"}:
            raise ValueError("unsupported fenced provider output")
        candidate = "\n".join(lines[1:-1]).strip()
    return json.loads(candidate)


def _provider_http_error(response: httpx.Response) -> str:
    """Keep provider diagnostics bounded and never include request credentials."""
    detail = response.text.replace("\n", " ").strip()[:500]
    return f"provider_http_{response.status_code}: {detail}" if detail else f"provider_http_{response.status_code}"
