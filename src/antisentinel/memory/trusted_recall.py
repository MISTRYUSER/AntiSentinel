"""Trust gates for derived memories before model-context injection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import AuthorizedMemoryScope, MemoryContextView, MemoryRecord


@dataclass(frozen=True)
class TrustedRecallResult:
    view: MemoryContextView
    rejected: dict[str, str]
    injected_tiers: dict[str, str]


class TrustedMemoryRecall:
    def recall(self, *, scope: AuthorizedMemoryScope, query: str, token_budget: int, now: datetime, records: tuple[MemoryRecord, ...], evidence_lookup, digest: str) -> TrustedRecallResult:
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        rejected: dict[str, str] = {}
        selected: list[tuple[MemoryRecord, str]] = []
        for record in records:
            reason = self._reject_reason(record, scope, now, evidence_lookup)
            if reason:
                rejected[record.memory_id] = reason
                continue
            source_trust = 1.0 if any(ref.ref_type == "evidence" for ref in record.source_refs) else 0.7
            score = 0.6 * source_trust + 0.4 * record.extraction_confidence
            if score < 0.5:
                rejected[record.memory_id] = "trust_blocked"
                continue
            selected.append((record, "verified" if score >= 0.8 else "hypothesis"))
        used = _tokens(digest)
        if used > token_budget:
            from antisentinel.worker.runtime.budget import apply_truncation_marker, estimate_tokens

            # Approximate byte budget from unified estimator (ceil(utf8/3)).
            max_bytes = max(0, token_budget * 3)
            bounded_digest = apply_truncation_marker(
                digest.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").rstrip()
            )
            used = estimate_tokens(bounded_digest)
        else:
            bounded_digest = digest
        used = min(used, token_budget)
        texts: list[str] = []
        ids: list[str] = []
        refs: list[str] = []
        tiers: dict[str, str] = {}
        for record, tier in selected:
            text = record.content if tier == "verified" else f"[候选假设] {record.content}"
            cost = _tokens(text)
            if used + cost > token_budget:
                continue
            used += cost; texts.append(text); ids.append(record.memory_id); tiers[record.memory_id] = tier
            refs.extend(ref.ref_id for ref in record.source_refs if ref.ref_type == "evidence")
        return TrustedRecallResult(MemoryContextView(scope.session_id, "\n".join(item for item in (bounded_digest, *texts) if item), tuple(dict.fromkeys(refs)), tuple(ids), used), rejected, tiers)

    @staticmethod
    def _reject_reason(record, scope, now, evidence_lookup):
        if record.operator_id != scope.operator_id: return "operator_mismatch"
        if getattr(record, "tenant_id", scope.tenant_id) != scope.tenant_id: return "tenant_mismatch"
        if getattr(record, "agent_id", scope.agent_id) != scope.agent_id: return "agent_mismatch"
        if record.incident_id not in {None, scope.incident_id}: return "incident_mismatch"
        if record.status != "active": return "status_inactive"
        if record.valid_from > now: return "not_yet_valid"
        if record.valid_to is not None and record.valid_to <= now: return "expired"
        for ref in record.source_refs:
            if ref.ref_type == "unknown": return "provenance_unknown"
            if ref.ref_type == "evidence":
                evidence = evidence_lookup(ref.ref_id) if evidence_lookup else None
                if evidence is None or evidence.content_hash != ref.content_hash or ref.content_version != 1: return "provenance_invalid"
        return None


def _tokens(text: str) -> int:
    from antisentinel.worker.runtime.budget import estimate_tokens

    return estimate_tokens(text or "")
