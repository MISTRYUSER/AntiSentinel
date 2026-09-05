from datetime import datetime, timezone

import pytest

from antisentinel.memory.candidates import MemoryCandidate
from antisentinel.memory.models import MemoryRecord, MemorySourceRef


FIXED_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def test_candidate_id_includes_all_source_identity_fields():
    first = MemoryCandidate.create(
        operator_id="operator-a", incident_id="incident-a", session_id="session-a",
        turn_id="turn-a", text="upstream timeout", source_kind="agent_conclusion",
        source_version=1,
    )
    second = MemoryCandidate.create(
        operator_id="operator-b", incident_id="incident-b", session_id="session-b",
        turn_id="turn-b", text="upstream timeout", source_kind="agent_conclusion",
        source_version=1,
    )

    assert first.candidate_id != second.candidate_id


def test_evidence_source_requires_hash_and_content_version():
    with pytest.raises(ValueError, match="evidence source requires content_hash and content_version"):
        MemorySourceRef(ref_type="evidence", ref_id="evidence-1")


def test_memory_record_round_trip_preserves_typed_provenance():
    record = MemoryRecord.create(
        memory_id="memory-1", memory_type="episodic", operator_id="operator-1",
        incident_id="incident-1", session_id="session-1", content="upstream timeout",
        source_refs=(MemorySourceRef("event", "event-1"),),
        extraction_confidence=0.75, valid_from=FIXED_NOW, outcome="completed",
        extractor_revision="rule-v1", reason_code="completed_rollout_conclusion",
    )

    assert MemoryRecord.from_dict(record.to_dict()) == record


@pytest.mark.parametrize("status", ["completed", "", "untrusted"])
def test_memory_record_rejects_invalid_lifecycle(status):
    with pytest.raises(ValueError, match="invalid memory lifecycle"):
        MemoryRecord.create(
            memory_id="memory-1", memory_type="episodic", operator_id="operator-1",
            incident_id="incident-1", session_id="session-1", content="timeout",
            source_refs=(MemorySourceRef("event", "event-1"),),
            extraction_confidence=0.5, valid_from=FIXED_NOW, status=status,
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, True])
def test_memory_record_rejects_invalid_extraction_confidence(confidence):
    with pytest.raises(ValueError, match="extraction_confidence must be between 0 and 1"):
        MemoryRecord.create(
            memory_id="memory-1", memory_type="episodic", operator_id="operator-1",
            incident_id="incident-1", session_id="session-1", content="timeout",
            source_refs=(MemorySourceRef("event", "event-1"),),
            extraction_confidence=confidence, valid_from=FIXED_NOW,
        )
