"""CodeMap-first source pin helpers."""

from antisentinel.code_map.source_context import SourceContextSlice
from antisentinel.worker.runtime.source_pins import (
    decay_packed_bodies,
    materialize_for_pack,
    pin_from_slice,
    pins_from_refs,
    refs_from_pins,
    upsert_pin,
)


def _slice(content: str = "body", evidence_id: str = "e1") -> SourceContextSlice:
    return SourceContextSlice("inc", evidence_id, "repo", "snap", "sha", "a.py", content, "h" * 64)


def test_ephemeral_pin_decays_to_pointer():
    pins = [pin_from_slice(_slice(), body_ttl=1)]
    packed = materialize_for_pack(pins)
    assert packed[0].content == "body"
    decay_packed_bodies(pins, packed)
    assert pins[0].body_ttl == 0
    assert pins[0].slice.content == ""
    assert materialize_for_pack(pins)[0].content == ""


def test_upsert_caps_and_refs_are_pointers():
    pins = []
    for index in range(5):
        pins = upsert_pin(pins, pin_from_slice(_slice(f"c{index}", f"e{index}"), body_ttl=1), max_pins=3)
    assert len(pins) == 3
    assert [pin.evidence_id for pin in pins] == ["e2", "e3", "e4"]
    refs = refs_from_pins(pins)
    assert all(item["pointer"] is True for item in refs)
    assert "content" not in refs[0]


def test_pins_from_refs_have_no_body():
    pins = pins_from_refs(
        [{"evidence_id": "e1", "repository_id": "r", "snapshot_id": "s", "path": "a.py", "content_hash": "h"}],
        incident_id="inc",
        body_ttl=0,
    )
    assert len(pins) == 1
    assert pins[0].slice.content == ""
