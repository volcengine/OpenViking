# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for server infrastructure: health, system status, middleware, error handling."""

import asyncio
import time
from types import SimpleNamespace

import httpx

from openviking.server.app import _initialize_runtime_state, create_app
from openviking.server.config import ServerConfig


async def test_health_endpoint(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_health_endpoint_resolves_identity_with_api_key(caplog):
    """When an API key is provided, /health should return identity information."""
    app = create_app(
        config=ServerConfig(
            auth_mode="api_key",
            host="127.0.0.1",
            root_api_key="test-root-key",
        ),
        service=SimpleNamespace(),
    )
    transport = httpx.ASGITransport(app=app)

    with caplog.at_level("WARNING", logger="openviking.server.routers.system"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health", headers={"X-API-Key": "test-root-key"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["auth_mode"] == "api_key"
    assert "account_id" in body
    assert "user_id" in body
    assert "role" in body
    assert body["role"] == "root"
    assert "Failed to resolve identity" not in caplog.text


async def test_health_endpoint_without_api_key():
    """Without an API key, /health should not return identity information."""
    app = create_app(
        config=ServerConfig(
            auth_mode="api_key",
            host="127.0.0.1",
            root_api_key="test-root-key",
        ),
        service=SimpleNamespace(),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "account_id" not in body
    assert "user_id" not in body
    assert "role" not in body


async def test_system_status(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["initialized"] is True


async def test_backend_sync_status_endpoint(client: httpx.AsyncClient, service):
    calls: list[str] = []

    async def _fake_system_sync_status(uri: str, ctx):
        calls.append(uri)
        assert ctx is not None
        return {"path": uri, "entry_count": 1}

    service.fs.system_sync_status = _fake_system_sync_status

    resp = await client.post(
        "/api/v1/system/backend/sync-status",
        json={"uri": "viking://resources"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == {"path": "viking://resources", "entry_count": 1}
    assert calls == ["viking://resources"]


async def test_backend_sync_retry_endpoint(client: httpx.AsyncClient, service):
    calls: list[str] = []

    async def _fake_system_sync_retry(uri: str, ctx):
        calls.append(uri)
        assert ctx is not None
        return {"path": uri, "retried": 2, "failed": 0}

    service.fs.system_sync_retry = _fake_system_sync_retry

    resp = await client.post(
        "/api/v1/system/backend/sync-retry",
        json={"uri": "viking://resources"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == {"path": "viking://resources", "retried": 2, "failed": 0}
    assert calls == ["viking://resources"]


async def test_admin_sync_status_route(client: httpx.AsyncClient, service):
    calls: list[str] = []

    async def _fake_system_sync_status(uri: str, ctx):
        calls.append(uri)
        assert ctx is not None
        return {"path": uri, "entry_count": 3}

    service.fs.system_sync_status = _fake_system_sync_status

    resp = await client.get("/api/v1/system/sync/viking://resources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == {"path": "viking://resources", "entry_count": 3}
    assert calls == ["viking://resources"]


async def test_admin_sync_retry_route(client: httpx.AsyncClient, service):
    calls: list[str] = []

    async def _fake_system_sync_retry(uri: str, ctx):
        calls.append(uri)
        assert ctx is not None
        return {"path": uri, "retried": 4, "failed": 1}

    service.fs.system_sync_retry = _fake_system_sync_retry

    resp = await client.post("/api/v1/system/sync/viking://resources/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"] == {"path": "viking://resources", "retried": 4, "failed": 1}
    assert calls == ["viking://resources"]


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
        """Mock VikingFS for readiness checks."""

        async def ls(self, path, ctx=None):
            return []

        async def system_sync_status(self, uri, ctx=None):
            return {"path": uri, "entry_count": 0}

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
        assert body["checks"]["agfs"]["status"] == "ok"
        assert body["checks"]["agfs"]["checks"]["filesystem"] == "ok"
        assert body["checks"]["agfs"]["checks"]["multiwrite_sync"] == "ok"


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


async def test_initialize_runtime_state_loads_api_key_manager(monkeypatch):
    """API key auth must finish manager loading before the app is considered ready."""

    class MockService:
        def __init__(self):
            self._initialized = False
            self.viking_fs = object()

        async def initialize(self):
            self._initialized = True

    class FakeAPIKeyManager:
        def __init__(self, root_key, viking_fs, api_key_hashing_enabled):
            self.root_key = root_key
            self.viking_fs = viking_fs
            self.api_key_hashing_enabled = api_key_hashing_enabled
            self.loaded = False

        async def load(self):
            self.loaded = True

    monkeypatch.setattr("openviking.server.app.APIKeyManager", FakeAPIKeyManager)

    app = SimpleNamespace(state=SimpleNamespace(api_key_manager=None))
    service = MockService()
    config = ServerConfig(root_api_key="root-key-for-test")

    await _initialize_runtime_state(app, service, config)

    assert service._initialized is True
    assert app.state.api_key_manager is not None
    assert app.state.api_key_manager.loaded is True
