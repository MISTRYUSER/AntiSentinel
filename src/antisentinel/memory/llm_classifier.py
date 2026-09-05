"""OpenAI-compatible asynchronous classifier for long-term memory candidates."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .candidates import MemoryCandidate, MemoryClassification


class MemoryLLMClassifier:
    def __init__(self, *, base_url: str, api_key: str, model: str, client: httpx.Client | None = None, timeout: float = 30.0) -> None:
        if not base_url.strip() or not api_key.strip() or not model.strip():
            raise ValueError("base_url, api_key, and model must be non-empty")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.Client(timeout=timeout)

    def classify(self, candidate: MemoryCandidate) -> MemoryClassification:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是长期记忆分类器。只返回JSON，不要解释。字段必须是 should_remember(bool), memory_type(preference|semantic|episodic|discard), confidence(0到1), reason_code。"},
                {"role": "user", "content": json.dumps({"candidate_id": candidate.candidate_id, "text": candidate.text, "source_kind": candidate.source_kind}, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        if "deepseek" in self.base_url.lower():
            payload["thinking"] = {"type": "disabled"}
        try:
            response = self.client.post(
                self._url(), headers={"Authorization": f"Bearer {self.api_key}"}, json=payload,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            return _parse_classification(candidate.candidate_id, raw)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            try:
                repair = dict(payload)
                repair["messages"] = [
                    {"role": "system", "content": "将候选记忆转换为严格 JSON。只允许字段 should_remember(boolean), memory_type(preference|semantic|episodic|discard), confidence(number 0到1), reason_code(string)。禁止 Markdown 和额外文本。"},
                    payload["messages"][1],
                ]
                response = self.client.post(self._url(), headers={"Authorization": f"Bearer {self.api_key}"}, json=repair)
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"]
                return _parse_classification(candidate.candidate_id, raw)
            except Exception:  # noqa: BLE001 - invalid memory is safely discarded
                return MemoryClassification(candidate.candidate_id, False, "discard", 0.0, "memory_model_invalid_output")

    def _url(self) -> str:
        return self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"


def _extract_json_object(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("memory model content must be a string")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start:end + 1]
    raise ValueError("memory model did not return a JSON object")


def _parse_classification(candidate_id: str, raw: Any) -> MemoryClassification:
    value = json.loads(_extract_json_object(raw))
    should_remember = value["should_remember"]
    memory_type = value["memory_type"]
    confidence = value["confidence"]
    reason_code = value["reason_code"]
    if not isinstance(should_remember, bool) or memory_type not in {"preference", "semantic", "episodic", "discard"} or isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1 or not isinstance(reason_code, str) or not reason_code.strip():
        raise ValueError("invalid memory classification schema")
    if memory_type == "discard":
        should_remember = False
    return MemoryClassification(candidate_id, should_remember, memory_type, float(confidence), reason_code)
