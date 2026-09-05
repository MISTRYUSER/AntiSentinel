from fastapi.testclient import TestClient

from antisentinel.api.app import create_app
from antisentinel.entry.application import DiagnosisApplicationService


def test_metrics_endpoint_exposes_memory_metrics():
    client = TestClient(create_app(DiagnosisApplicationService.default_fake()))

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "antisentinel_memory_cache_hits_total" in response.text
    assert "antisentinel_memory_worker_processed_total" in response.text
