from datetime import datetime, timezone

from antisentinel.memory.models import AuthorizedMemoryScope, MemoryRecord, MemorySourceRef
from antisentinel.memory.trusted_recall import TrustedMemoryRecall


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
SCOPE = AuthorizedMemoryScope("operator-1", "incident-1", "session-1")


def record(memory_id, *, valid_from=NOW, source_refs=(MemorySourceRef("session", "session-1"),), operator_id="operator-1", incident_id="incident-1", content="先看日志"):
    return MemoryRecord.create(memory_id=memory_id, memory_type="episodic", operator_id=operator_id, incident_id=incident_id, session_id="session-1", content=content, source_refs=source_refs, extraction_confidence=0.9, valid_from=valid_from)


def test_trusted_recall_rejects_future_wrong_scope_and_invalid_evidence():
    records = (
        record("future", valid_from=datetime(2026, 9, 6, tzinfo=timezone.utc)),
        record("other", operator_id="operator-2"),
        record("bad-evidence", source_refs=(MemorySourceRef("evidence", "e-1", content_hash="sha256:wrong", content_version=1),)),
    )
    result = TrustedMemoryRecall().recall(scope=SCOPE, query="日志", token_budget=20, now=NOW, records=records, evidence_lookup=lambda _: None, digest="")
    assert result.view.memory_ids == ()
    assert result.rejected == {"future": "not_yet_valid", "other": "operator_mismatch", "bad-evidence": "provenance_invalid"}


def test_trusted_recall_uses_one_budget_for_digest_and_memory():
    result = TrustedMemoryRecall().recall(scope=SCOPE, query="日志", token_budget=4, now=NOW, records=(record("m-1", content="日志日志日志"),), evidence_lookup=lambda _: None, digest="摘要摘要摘要摘要摘要摘要")
    assert result.view.estimated_tokens <= 4
    assert result.view.memory_ids == ()
