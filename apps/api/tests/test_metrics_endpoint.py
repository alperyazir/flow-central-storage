"""Tests for Prometheus metrics exposure."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_metrics_endpoint_exposes_prometheus_data() -> None:
    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    content = response.text
    assert "fcs_requests_total" in content
    assert "fcs_request_duration_seconds" in content
    assert response.headers["content-type"].startswith("text/plain")


def test_metrics_counts_increment_on_request() -> None:
    client.get("/health")
    content = client.get("/metrics").text

    assert 'method="GET",path="/health"' in content
