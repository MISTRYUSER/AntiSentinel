import json
import logging


def test_structured_runtime_log_is_written_without_secret(tmp_path):
    from antisentinel.tracing.logging import configure_runtime_logging

    logger = logging.getLogger("antisentinel.test")
    configure_runtime_logging(tmp_path / "runtime.jsonl")
    logger.info("provider_response_shape", extra={"request_id": "req-1", "content_length": 12})

    record = json.loads((tmp_path / "runtime.jsonl").read_text().splitlines()[-1])
    assert record["message"] == "provider_response_shape"
    assert record["request_id"] == "req-1"
    assert "api_key" not in record
