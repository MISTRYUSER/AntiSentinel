"""Incremental, provenance-preserving memory projection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
import httpx

from .models import MemorySourceRef


@dataclass(frozen=True)
class ProjectionSource:
    operator_id: str
    incident_id: str
    session_id: str
    valid_from: datetime
    turns: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MemoryProjection:
    projection_id: str
    kind: str
    content: str
    source_refs: tuple[MemorySourceRef, ...]
    status: str
    content_version: int = 1
    projection_revision: str = "rule-projection-v1"
    failure_reason: str | None = None
    input_truncated: bool = False


class RuleMemoryProjector:
    def project(self, source: ProjectionSource) -> tuple[MemoryProjection, ...]:
        if not source.turns:
            return (self._item(source, "session_digest", "", (), "failed"),)
        refs = tuple(MemorySourceRef("turn", turn_id) for turn_id, _ in source.turns)
        text = " ".join(content.strip() for _, content in source.turns if content.strip())
        items = [self._item(source, "session_digest", text[:600], refs, "active")]
        identifiers = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b|\b[a-zA-Z][\w-]*(?::[\w-]+)+\b", text)))
        if identifiers:
            items.append(self._item(source, "keyphrase", " ".join(identifiers), refs, "active"))
        decisions = [content.strip() for _, content in source.turns if re.search(r"\b(decided|decision|决定|回滚|先检查)\b", content, re.I)]
        if decisions:
            items.append(self._item(source, "decision", decisions[-1][:600], refs, "active"))
        return tuple(items)

    @staticmethod
    def _item(source, kind, content, refs, status):
        identity = f"{source.operator_id}|{source.incident_id}|{source.session_id}|{kind}|{content}|rule-projection-v1"
        return MemoryProjection(sha256(identity.encode()).hexdigest(), kind, content, tuple(refs), status)


class LLMMemoryProjector:
    allowed_kinds = {"session_digest", "keyphrase", "user_fact", "diagnosis_fact", "decision", "failure_barrier"}

    def __init__(self, *, base_url: str, api_key: str, model: str, client: httpx.Client | None = None, timeout: float = 30.0, max_input_chars: int = 12000) -> None:
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.client = client or httpx.Client(timeout=timeout)
        self.max_input_chars = max_input_chars

    def project(self, source: ProjectionSource) -> tuple[MemoryProjection, ...]:
        bounded, used = [], 0
        for turn_id, content in source.turns:
            available = self.max_input_chars - used
            if available <= 0: break
            value = content[:min(1000, available)]
            bounded.append({"turn_id":turn_id,"content":value}); used += len(value)
        disclosed_turns = frozenset(item["turn_id"] for item in bounded)
        input_truncated = len(bounded) != len(source.turns) or any(
            len(item["content"]) != len(content)
            for item, (_, content) in zip(bounded, source.turns)
        )
        payload = {"model": self.model, "response_format": {"type": "json_object"}, "temperature": 0, "messages": [{"role":"system","content":"提取长期记忆。只返回JSON: {projections:[{kind,content,turn_ids,confidence}]}。kind只允许session_digest,keyphrase,user_fact,diagnosis_fact,decision,failure_barrier。不得复制原文大段，不得编造。"}, {"role":"user","content":json.dumps({"session_id":source.session_id,"turns":bounded},ensure_ascii=False)}]}
        try:
            return self._request_and_parse(payload, source, disclosed_turns, input_truncated=input_truncated)
        except Exception:
            repair = dict(payload); repair["messages"] = [{"role":"system","content":"只返回严格JSON：{projections:[{kind,content,turn_ids,confidence}]}。turn_ids必须来自输入；禁止Markdown和解释。"}, payload["messages"][1]]
            try: return self._request_and_parse(repair, source, disclosed_turns, input_truncated=input_truncated)
            except Exception as exc: return (self._failed(source, f"repair_{type(exc).__name__}", input_truncated),)

    def _request_and_parse(self, payload, source, allowed_turns, *, input_truncated: bool = False):
        response = self.client.post(f"{self.base_url}/chat/completions", headers={"Authorization":f"Bearer {self.api_key}"}, json=payload); response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        matched = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S | re.I) if isinstance(raw, str) else None
        payload_text = matched.group(1) if matched else raw[raw.find("{"):raw.rfind("}")+1]
        value = json.loads(payload_text); projections=[]
        for item in value["projections"]:
            kind, content, turn_ids, confidence = item["kind"], item["content"], tuple(item["turn_ids"]), item["confidence"]
            if kind not in self.allowed_kinds or not isinstance(content,str) or not content.strip() or not turn_ids or not set(turn_ids) <= allowed_turns or isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not 0 <= confidence <= 1: raise ValueError
            projections.append(self._item(source, kind, content[:1000], tuple(MemorySourceRef("turn", turn_id) for turn_id in turn_ids), "active", input_truncated=input_truncated))
        if not projections: raise ValueError
        return tuple(projections)

    def _item(self, source, kind, content, refs, status, *, input_truncated: bool = False):
        projection_revision = f"llm:{self.model}:prompt-v1"
        identity = f"{source.operator_id}|{source.incident_id}|{source.session_id}|{kind}|{content}|{projection_revision}"
        return MemoryProjection(
            sha256(identity.encode()).hexdigest(), kind, content, tuple(refs), status,
            projection_revision=projection_revision, input_truncated=input_truncated,
        )

    @staticmethod
    def _failed(source, reason, truncated):
        item = LLMMemoryProjector._item_failed(source, reason, truncated)
        return item

    @staticmethod
    def _item_failed(source, reason, truncated):
        revision = "llm:failed:prompt-v1"
        identity = f"{source.operator_id}|{source.incident_id}|{source.session_id}|session_digest||{revision}"
        return MemoryProjection(
            sha256(identity.encode()).hexdigest(), "session_digest", "", (), "failed",
            projection_revision=revision, failure_reason=reason, input_truncated=truncated,
        )
