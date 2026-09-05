import json

import httpx


def test_tool_result_request_keeps_distinct_followup_tools_available():
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.ports.model import ModelRequest

    seen = {}
    response = httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}})}}], "usage": {}})
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://api.deepseek.com", api_key="server-secret", model="deepseek-v4-flash",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: (seen.update(json.loads(request.content)) or response))),
    )

    adapter.complete(ModelRequest("incident-1", "session-1", "turn-2", [{"role": "tool", "task_results": [{"summary": "checked"}]}], [{"name": "read_log", "argument_schema": {"type": "object"}}]))

    assert "tool_choice" not in seen
    assert "response_format" not in seen
    assert seen["tools"][0]["function"]["name"] == "read_log"


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


def test_skill_context_keeps_tools_available_after_skill_control_result():
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.ports.model import ModelRequest

    seen = {}
    response = httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"final": {"summary": "done", "diagnosis": "healthy", "confidence": 0.9, "evidence_refs": []}})}}], "usage": {}})
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://api.deepseek.com", api_key="server-secret", model="deepseek-v4-flash",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: (seen.update(json.loads(request.content)) or response))),
    )

    adapter.complete(ModelRequest("incident-1", "session-1", "turn-2", [
        {"role": "system", "content": "skill runtime"},
        {"role": "skill", "skill_id": "local/diagnosis", "instructions": "inspect evidence", "allow_followup_tools": True},
        {"role": "tool", "task_results": [{"summary": "loaded skill local/diagnosis"}]},
    ], [{"name": "read_health", "argument_schema": {"type": "object"}}]))

    assert "tool_choice" not in seen
    assert seen["tools"][0]["function"]["name"] == "read_health"
    assert {message["role"] for message in seen["messages"]} <= {"system", "user", "assistant"}


def test_skill_control_tool_names_are_mapped_for_deepseek_and_restored_on_response():
    from antisentinel.adapters.llm.openai_compatible import OpenAICompatibleModelAdapter
    from antisentinel.ports.model import ModelRequest

    seen = {}
    response = httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"function": {"name": "antisentinel_skill_load", "arguments": "{\"skill_id\":\"local/diagnosis\"}"}}]}}], "usage": {}})
    adapter = OpenAICompatibleModelAdapter(
        base_url="https://api.deepseek.com", api_key="server-secret", model="deepseek-chat",
        client=httpx.Client(transport=httpx.MockTransport(lambda request: (seen.update(json.loads(request.content)) or response))),
    )

    result = adapter.complete(ModelRequest("i", "s", "t", [{"role": "system", "content": "x"}], [
        {"name": "skill.load", "argument_schema": {"type": "object"}},
        {"name": "read_health", "argument_schema": {"type": "object"}},
    ]))

    assert [item["function"]["name"] for item in seen["tools"]] == ["antisentinel_skill_load", "read_health"]
    assert result.tasks[0].tool_calls[0].tool_name == "skill.load"


def test_dotted_sandbox_tool_name_is_mapped_and_restored():
    from antisentinel.adapters.llm.openai_compatible import _provider_tool_name
    assert _provider_tool_name("sandbox.read_file") == "antisentinel_sandbox_read_file"


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
