from datetime import datetime, timezone

from antisentinel.memory.projection import ProjectionSource, RuleMemoryProjector
from antisentinel.memory.projection import LLMMemoryProjector
import httpx


def test_rule_projection_emits_provenance_bearing_digest_and_identifier_keys():
    source = ProjectionSource(
        operator_id="op", incident_id="inc", session_id="session-1", valid_from=datetime(2026, 9, 5, tzinfo=timezone.utc),
        turns=(("turn-1", "ERR_TIMEOUT_504 occurred for cache:tenant:1"), ("turn-2", "operator decided to inspect upstream timeout")),
    )

    projections = RuleMemoryProjector().project(source)

    assert {item.kind for item in projections} >= {"session_digest", "keyphrase", "decision"}
    assert all(item.source_refs and item.content_version == 1 for item in projections)
    assert any("ERR_TIMEOUT_504" in item.content for item in projections if item.kind == "keyphrase")


def test_projection_is_idempotent_and_marks_empty_source_as_failed():
    source = ProjectionSource("op", "inc", "session-1", datetime(2026, 9, 5, tzinfo=timezone.utc), ())
    projector = RuleMemoryProjector()

    assert projector.project(source) == projector.project(source)
    assert projector.project(source)[0].status == "failed"


def test_llm_projector_parses_fact_decision_and_turn_provenance():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices":[{"message":{"content":'{"projections":[{"kind":"diagnosis_fact","content":"上游超时","turn_ids":["turn-1"],"confidence":0.9},{"kind":"decision","content":"先检查连接池","turn_ids":["turn-2"],"confidence":0.8}]}'}}]})))
    source = ProjectionSource("op", "inc", "session", datetime(2026, 9, 5, tzinfo=timezone.utc), (("turn-1", "timeout"), ("turn-2", "check pool")))

    projections = LLMMemoryProjector(base_url="https://model.test", api_key="key", model="test", client=client).project(source)

    assert [item.kind for item in projections] == ["diagnosis_fact", "decision"]
    assert projections[0].source_refs[0].ref_id == "turn-1"


def test_llm_projector_invalid_output_is_explicitly_failed():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices":[{"message":{"content":"invalid"}}]})))
    source = ProjectionSource("op", "inc", "session", datetime(2026, 9, 5, tzinfo=timezone.utc), (("turn-1", "timeout"),))

    assert LLMMemoryProjector(base_url="https://model.test", api_key="key", model="test", client=client).project(source)[0].status == "failed"


def test_llm_projector_repairs_invalid_first_response():
    calls=[]
    def handler(_):
        calls.append(1)
        content="invalid" if len(calls)==1 else '{"projections":[{"kind":"diagnosis_fact","content":"fixed","turn_ids":["turn-1"],"confidence":0.9}]}'
        return httpx.Response(200,json={"choices":[{"message":{"content":content}}]})
    source=ProjectionSource("op","inc","session",datetime(2026,9,5,tzinfo=timezone.utc),(("turn-1","x"),))
    result=LLMMemoryProjector(base_url="https://model.test",api_key="key",model="test",client=httpx.Client(transport=httpx.MockTransport(handler))).project(source)
    assert result[0].status == "active" and len(calls) == 2


def test_llm_projector_bounds_long_turn_context_and_marks_failure_reason():
    client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={"choices":[{"message":{"content":"invalid"}}]})))
    source=ProjectionSource("op","inc","session",datetime(2026,9,5,tzinfo=timezone.utc),tuple((f"turn-{i}","x"*3000) for i in range(10)))
    result=LLMMemoryProjector(base_url="https://model.test",api_key="key",model="test",client=client,max_input_chars=4000).project(source)
    assert result[0].status == "failed"
    assert result[0].failure_reason is not None


def test_llm_projector_accepts_markdown_wrapped_json():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices":[{"message":{"content":"```json\n{\"projections\":[{\"kind\":\"keyphrase\",\"content\":\"ERR_X\",\"turn_ids\":[\"turn-1\"],\"confidence\":0.9}]}\n```"}}]})))
    source = ProjectionSource("op", "inc", "session", datetime(2026, 9, 5, tzinfo=timezone.utc), (("turn-1", "ERR_X"),))
    assert LLMMemoryProjector(base_url="https://model.test", api_key="key", model="test", client=client).project(source)[0].status == "active"
