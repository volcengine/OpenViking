# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for server infrastructure: health, system status, middleware, error handling."""

import asyncio
import time

import httpx
import pytest

from openviking.server.app import _on_deferred_init_done, create_app
from openviking.server.config import ServerConfig


class _ExitCalled(Exception):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"os._exit({code})")


async def test_deferred_init_failure_exits_process(monkeypatch):
    async def fail_init():
        raise RuntimeError("init failed")

    def fake_exit(code: int):
        raise _ExitCalled(code)

    monkeypatch.setattr("openviking.server.app.os._exit", fake_exit)
    task = asyncio.create_task(fail_init())
    with pytest.raises(RuntimeError):
        await task

    with pytest.raises(_ExitCalled) as exc_info:
        _on_deferred_init_done(task)

    assert exc_info.value.code == 1


async def test_deferred_init_success_does_not_exit(monkeypatch):
    async def complete_init():
        return None

    monkeypatch.setattr(
        "openviking.server.app.os._exit",
        lambda code: pytest.fail(f"os._exit({code}) should not be called"),
    )
    task = asyncio.create_task(complete_init())
    await task

    _on_deferred_init_done(task)


async def test_deferred_init_cancellation_does_not_exit(monkeypatch):
    monkeypatch.setattr(
        "openviking.server.app.os._exit",
        lambda code: pytest.fail(f"os._exit({code}) should not be called"),
    )
    task = asyncio.create_task(asyncio.sleep(60))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    _on_deferred_init_done(task)


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
        async def initialize(self):
            pass

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
