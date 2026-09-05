import json

from fastapi.testclient import TestClient

from antisentinel.adapters.llm.openai_compatible import FakeProviderModel
from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService


def test_openai_compatible_chat_endpoint_connects_chat_frontend_to_runtime():
    service = DiagnosisApplicationService.default_fake()
    service.model_mode = "real"
    service.model_provider = "deepseek"
    service.model_name = "deepseek-v4-flash"
    service.model_factories["real"] = lambda: FakeProviderModel([
        {"final": {"summary": "你好，已收到你的日常消息。", "diagnosis": "对话已连接", "confidence": 0.9, "evidence_refs": []}, "usage": {"prompt_tokens": 12, "completion_tokens": 8}}
    ])
    client = TestClient(create_app(service))

    response = client.post("/v1/chat/completions", json={
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "你好"}],
        "metadata": {"session_id": "chat-session-1", "incident_id": "project-1"},
    })

    body = response.json()
    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "已收到" in body["choices"][0]["message"]["content"]
    assert body["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}


def test_openai_compatible_chat_endpoint_supports_librechat_streaming():
    service = DiagnosisApplicationService.default_fake()
    service.model_mode = "real"
    service.model_factories["real"] = lambda: FakeProviderModel([
        {"final": {"summary": "流式回复成功", "diagnosis": "连接正常", "confidence": 0.9, "evidence_refs": []}, "usage": {"prompt_tokens": 4, "completion_tokens": 3}}
    ])
    client = TestClient(create_app(service))

    response = client.post("/v1/chat/completions", json={
        "model": "deepseek-v4-flash",
        "stream": True,
        "messages": [{"role": "user", "content": "你好"}],
    })

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "流式回复成功" in response.text
    assert "data: [DONE]" in response.text


def test_openai_compatible_chat_endpoint_renders_task_plan_as_chat_text():
    service = DiagnosisApplicationService.default_fake()
    service.model_mode = "real"
    service.model_factories["real"] = lambda: FakeProviderModel([
        {"tasks": [{"task_id": "initial_diag", "objective": "收集系统诊断信息", "tool_calls": []}]}
    ])
    client = TestClient(create_app(service))

    response = client.post("/v1/chat/completions", json={
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "1"}],
    })

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "我先处理以下事项：\n1. 收集系统诊断信息"
