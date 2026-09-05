import json

import httpx


def test_tool_result_request_forces_final_json_without_more_tool_calls():
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.ports.model import ModelRequest

    seen = {}
    response = httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}})}}], "usage": {}})
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://api.deepseek.com", api_key="server-secret", model="deepseek-v4-flash",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: (seen.update(json.loads(request.content)) or response))),
    )

    adapter.complete(ModelRequest("incident-1", "session-1", "turn-2", [{"role": "tool", "task_results": [{"summary": "checked"}]}], [{"name": "read_log", "argument_schema": {"type": "object"}}]))

    assert seen["tool_choice"] == "none"
    assert seen["response_format"] == {"type": "json_object"}


def test_deepseek_requests_disable_thinking_for_deterministic_tool_json_handoff():
    import json
    import httpx
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.ports.model import ModelRequest

    seen = {}
    response = httpx.Response(200, json={"choices": [{"message": {"content": '{"final":{"summary":"done","diagnosis":"healthy","confidence":0.9,"evidence_refs":[]}}'}}], "usage": {}})
    adapter = OpenAICompatibleModelAdapter(base_url="https://api.deepseek.com", api_key="secret", model="deepseek-v4-flash", client=httpx.Client(transport=httpx.MockTransport(lambda request: (seen.update(json.loads(request.content)) or response))))
    adapter.complete(ModelRequest("i", "s", "t", [{"role":"tool","task_results":[]}], []))
    assert seen["thinking"] == {"type": "disabled"}


def test_runtime_system_prompt_explicitly_requires_json_schema():
    from antisentinel.worker.runtime.messages import build_system_message

    content = build_system_message()["content"].lower()

    assert "json" in content
    assert '"final"' in content
    assert '"tasks"' in content


def test_invalid_tool_result_content_uses_structured_repair_round():
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.ports.model import ModelRequest

    responses = iter([
        {"choices": [{"message": {"content": '{"answer":"timeout"}'}}], "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
        {"choices": [{"message": {"content": '{"final":{"summary":"fixed","diagnosis":"timeout","confidence":0.8,"evidence_refs":[]}}'}}], "usage": {"prompt_tokens": 20, "completion_tokens": 6}},
    ])
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://api.deepseek.com", api_key="server-secret", model="deepseek-v4-flash",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses)))),
    )

    result = adapter.complete(ModelRequest("incident-1", "session-1", "turn-2", [{"role": "tool", "task_results": [{"summary": "checked"}]}], [{"name": "read_log", "argument_schema": {"type": "object"}}]))

    assert result.final.diagnosis == "timeout"
    assert result.usage.input_tokens == 30
    assert result.usage.output_tokens == 10


def test_evidence_refs_accept_provider_string_shorthand():
    from antisentinel.ports.model import parse_model_response

    response = parse_model_response({"final": {"summary": "done", "diagnosis": "timeout", "confidence": 0.8, "evidence_refs": ["evidence-1"]}})

    assert response.final.evidence_refs[0].evidence_id == "evidence-1"
