"""Storage-layer namespace filters (isolation before retrieval/trust)."""

from __future__ import annotations

from typing import Any

from .models import DEFAULT_AGENT_ID, DEFAULT_TENANT_ID, MemoryNamespace, require_scope_id


def matches_namespace(record: dict[str, Any], scope: MemoryNamespace, *, include_operator_wide: bool = True) -> bool:
    if record.get("operator_id") != scope.operator_id:
        return False
    record_tenant = record.get("tenant_id") or DEFAULT_TENANT_ID
    if record_tenant != scope.tenant_id:
        return False
    record_agent = record.get("agent_id") or DEFAULT_AGENT_ID
    if record_agent != scope.agent_id:
        return False
    if scope.memory_type is not None and record.get("memory_type") != scope.memory_type:
        return False
    if scope.visibility is not None and (record.get("visibility") or "operator") != scope.visibility:
        return False
    if scope.incident_id is not None:
        incident = record.get("incident_id")
        if incident not in {None, scope.incident_id} if include_operator_wide else {scope.incident_id}:
            return False
    if scope.session_id is not None and record.get("session_id") not in {None, scope.session_id}:
        return False
    return True


def filter_records(
    records: list[dict[str, Any]],
    scope: MemoryNamespace,
    *,
    include_operator_wide: bool = True,
) -> list[dict[str, Any]]:
    require_scope_id("operator_id", scope.operator_id)
    return [item for item in records if matches_namespace(item, scope, include_operator_wide=include_operator_wide)]
