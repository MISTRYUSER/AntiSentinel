import httpx

from antisentinel.memory.candidates import MemoryCandidate
from antisentinel.memory.llm_classifier import MemoryLLMClassifier


def candidate():
    return MemoryCandidate("candidate-1", "operator-1", "session-1", "logs_before_metrics", "user_input")


def test_memory_llm_classifier_parses_strict_json_and_maps_decision():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": '{"should_remember":true,"memory_type":"preference","confidence":0.93,"reason_code":"explicit_signal"}'}}]
    })))
    classifier = MemoryLLMClassifier(base_url="https://deepseek.test", api_key="key", model="deepseek-chat", client=client)

    result = classifier.classify(candidate())

    assert result.candidate_id == "candidate-1"
    assert result.should_remember is True
    assert result.memory_type == "preference"
    assert result.confidence == 0.93


def test_memory_llm_classifier_rejects_invalid_output_without_fabricating_memory():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": "not-json"}}]
    })))
    classifier = MemoryLLMClassifier(base_url="https://deepseek.test", api_key="key", model="deepseek-chat", client=client)

    result = classifier.classify(candidate())

    assert result.candidate_id == "candidate-1"
    assert result.should_remember is False
    assert result.memory_type == "discard"
    assert result.reason_code == "memory_model_invalid_output"


def test_memory_llm_classifier_accepts_json_fenced_by_provider_markdown():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": '```json\n{"should_remember":true,"memory_type":"semantic","confidence":0.8,"reason_code":"stable_fact"}\n```'}}]
    })))
    classifier = MemoryLLMClassifier(base_url="https://deepseek.test", api_key="key", model="deepseek-chat", client=client)

    result = classifier.classify(candidate())

    assert result.memory_type == "semantic"
    assert result.should_remember is True
