# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for server infrastructure: health, system status, middleware, error handling."""

import asyncio
import time

import httpx

from openviking.server.app import create_app
from openviking.server.config import ServerConfig


async def test_health_endpoint(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_system_status(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["initialized"] is True


async def test_process_time_header(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert "x-process-time" in resp.headers
    value = float(resp.headers["x-process-time"])
    assert value >= 0


async def test_openviking_error_handler(client: httpx.AsyncClient):
    """Requesting a non-existent resource should return structured error."""
    resp = await client.get("/api/v1/fs/stat", params={"uri": "viking://nonexistent/path"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] is not None


async def test_404_for_unknown_route(client: httpx.AsyncClient):
    resp = await client.get("/this/route/does/not/exist")
    assert resp.status_code == 404


async def test_lifespan_shutdown_ignores_cancelled_service_close():
    class _Service:
        async def close(self):
            raise asyncio.CancelledError("shutdown")

    app = create_app(config=ServerConfig(), service=_Service())

    async with app.router.lifespan_context(app):
        pass


async def test_health_responds_during_initialization(monkeypatch):
    """Health endpoint responds 200 even during phased service initialization."""

    # Service is "initializing" — _initialized is False
    class MockService:
        _initialized = False

    service = MockService()
    monkeypatch.setattr("openviking.server.dependencies._service", service)

    app = create_app(config=ServerConfig(), service=service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


async def test_ready_returns_503_before_initialized(monkeypatch):
    """Ready returns 503 when service._initialized is False."""

    class MockService:
        _initialized = False

    service = MockService()
    monkeypatch.setattr("openviking.server.dependencies._service", service)

    app = create_app(config=ServerConfig(), service=service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["reason"] == "initializing"


async def test_ready_returns_200_after_initialized(monkeypatch):
    """Ready returns 200 when service is fully initialized and subsystems are healthy."""

    class MockVikingFS:
        async def ls(self, path, ctx=None):
            return []

        def _get_vector_store(self):
            class MockVectorStore:
                async def health_check(self):
                    return True

            return MockVectorStore()

    class MockService:
        _initialized = True

    service = MockService()
    monkeypatch.setattr("openviking.server.dependencies._service", service)
    monkeypatch.setattr("openviking.server.routers.system.get_viking_fs", lambda: MockVikingFS())
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config",
        lambda config: (False, None, None),
    )

    app = create_app(config=ServerConfig(), service=service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"


async def test_slow_init_does_not_block_health(monkeypatch):
    """Health endpoint responds quickly even when initialization is slow."""

    # Health is stateless — it doesn't call get_service() or depend on _initialized
    class MockService:
        _initialized = False

    service = MockService()
    monkeypatch.setattr("openviking.server.dependencies._service", service)

    app = create_app(config=ServerConfig(), service=service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = time.perf_counter()
        resp = await client.get("/health")
        elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        # Health responds instantly since it's stateless (no service dependency)
        assert elapsed < 0.5
