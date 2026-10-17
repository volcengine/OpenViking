# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for server CORS defaults."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from openviking.server.app import create_app
from openviking.server.config import ServerConfig


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )


def _mock_openviking_config(monkeypatch):
    monkeypatch.setattr(
        "openviking.server.app.get_openviking_config",
        lambda: SimpleNamespace(service_name="test"),
    )
    monkeypatch.setattr("openviking.server.app.create_embedded_gateway", lambda *args: None)


def test_dev_cors_rejects_non_loopback_origin_even_when_wildcard_configured(monkeypatch):
    _mock_openviking_config(monkeypatch)
    app = create_app(config=ServerConfig(cors_origins=["*"]))

    response = _preflight(TestClient(app), "https://evil.example")

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_dev_rejects_actual_non_loopback_requests_before_route_execution(monkeypatch):
    _mock_openviking_config(monkeypatch)
    client = TestClient(create_app(config=ServerConfig()))

    response = client.post(
        "/resources/temp_upload",
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Origin not allowed"}


def test_dev_cors_allows_loopback_origins_on_any_port(monkeypatch):
    _mock_openviking_config(monkeypatch)
    app = create_app(config=ServerConfig(cors_origins=["*"]))
    client = TestClient(app)

    for origin in (
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://[::1]:8080",
    ):
        response = _preflight(client, origin)

        assert response.headers["access-control-allow-origin"] == origin


def test_non_dev_cors_keeps_configured_origins(monkeypatch):
    _mock_openviking_config(monkeypatch)
    app = create_app(
        config=ServerConfig(
            root_api_key="root-key",
            cors_origins=["https://app.example"],
        )
    )
    client = TestClient(app)

    allowed = _preflight(client, "https://app.example")
    rejected = _preflight(client, "https://evil.example")

    assert allowed.headers["access-control-allow-origin"] == "https://app.example"
    assert "access-control-allow-origin" not in rejected.headers
