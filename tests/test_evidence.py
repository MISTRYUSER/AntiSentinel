import json
from datetime import datetime, timezone

import pytest

from antisentinel.domain.errors import InvalidInputError
from antisentinel.domain.evidence import Evidence, EvidenceRef
from antisentinel.domain.ids import EvidenceId


def test_evidence_records_immutable_metadata_and_external_content_ref():
    evidence = Evidence.create(
        kind="log",
        content_ref="s3://diagnosis/logs/abc",
        content_hash="sha256:abc",
        source="checkout-worker",
        metadata={"service": "checkout"},
    )

    assert evidence.kind == "log"
    assert evidence.content_ref == "s3://diagnosis/logs/abc"
    assert evidence.metadata == {"service": "checkout"}
    assert [event.type for event in evidence.pending_events] == ["evidence.recorded"]


def test_evidence_rejects_empty_kind_or_content_ref():
    with pytest.raises(InvalidInputError):
        Evidence.create(kind="", content_ref="s3://diagnosis/logs/abc")

    with pytest.raises(InvalidInputError):
        Evidence.create(kind="log", content_ref="")


def test_evidence_metadata_cannot_be_mutated_after_creation():
    evidence = Evidence.create(kind="metric", content_ref="metrics://checkout", metadata={"p95": 120})

    with pytest.raises(TypeError):
        evidence.metadata["p95"] = 200


def test_evidence_ref_is_immutable_and_requires_evidence_id():
    evidence_ref = EvidenceRef(evidence_id=EvidenceId("evidence-1"), role="diagnostic")

    with pytest.raises((TypeError, AttributeError)):
        evidence_ref.evidence_id = EvidenceId("evidence-2")

    with pytest.raises(InvalidInputError):
        EvidenceRef(evidence_id=EvidenceId(""))


def test_evidence_json_round_trip_preserves_metadata_and_time():
    recorded_at = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)
    evidence = Evidence.create(
        kind="trace",
        content_ref="trace://abc",
        evidence_id="evidence-1",
        recorded_at=recorded_at,
        metadata={"trace_id": "abc"},
    )

    restored = Evidence.from_dict(json.loads(json.dumps(evidence.to_dict())))

    assert restored == evidence
    assert restored.recorded_at == recorded_at
    assert restored.recorded_at.tzinfo == timezone.utc
