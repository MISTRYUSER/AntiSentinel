"""Immutable executable definitions; no model, I/O, or scheduling decisions."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f'{name} must be a nonempty string of at most 256 characters')
    return value


@dataclass(frozen=True)
class PlanNode:
    step_id: str
    tool_name: str
    _arguments_json: str
    depends_on: tuple[str, ...]

    @property
    def arguments(self) -> dict[str, Any]:
        return json.loads(self._arguments_json)

    def to_dict(self) -> dict[str, Any]:
        return {'step_id': self.step_id, 'tool_name': self.tool_name,
                'arguments': self.arguments, 'depends_on': list(self.depends_on)}


@dataclass(frozen=True)
class ExecutionDAG:
    nodes: tuple[PlanNode, ...]

    @classmethod
    def from_steps(cls, steps: list[dict[str, Any]]) -> ExecutionDAG:
        if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
            raise ValueError('steps must contain 1..64 nodes')
        nodes = []
        for index, raw in enumerate(steps):
            if not isinstance(raw, dict):
                raise ValueError('step must be an object')
            sid = nonempty(raw.get('step_id'), 'step_id')
            name = nonempty(raw.get('tool_name'), 'tool_name')
            arguments = raw.get('arguments')
            if not isinstance(arguments, dict):
                raise ValueError('arguments must be an object')
            deps = raw.get('depends_on', [] if index == 0 else [nodes[-1].step_id])
            if not isinstance(deps, list) or any(not isinstance(d, str) or not d.strip() for d in deps):
                raise ValueError('depends_on must be an array of step IDs')
            if len(set(deps)) != len(deps):
                raise ValueError('duplicate dependency')
            nodes.append(PlanNode(sid, name, canonical(arguments), tuple(deps)))
        result = cls(tuple(nodes))
        ids = {n.step_id for n in nodes}
        if len(ids) != len(nodes):
            raise ValueError('duplicate step_id')
        if any(d not in ids for n in nodes for d in n.depends_on):
            raise ValueError('unknown dependency')
        result.topological_order()
        return result

    def topological_order(self) -> tuple[str, ...]:
        completed: dict[str, str] = {}
        while len(completed) < len(self.nodes):
            ready = self.ready(completed)
            if not ready:
                raise ValueError('dependency cycle')
            completed.update({sid: 'succeeded' for sid in ready})
        return tuple(completed)

    def ready(self, states: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(n.step_id for n in self.nodes
                     if states.get(n.step_id, 'pending') == 'pending'
                     and all(states.get(d) == 'succeeded' for d in n.depends_on))

    def to_dict(self) -> dict[str, Any]:
        return {'steps': [n.to_dict() for n in self.nodes]}

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(canonical(self.to_dict()).encode()).hexdigest()

