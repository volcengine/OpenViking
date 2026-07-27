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


# ---------------------------------------------------------------------------
# GET /api/v1/system/idle (issue #3488)
#
# Light fixture (create_app + SimpleNamespace service) is used so the tests do
# not need to boot a full OpenVikingService (which requires the ragfs native
# binding). Route-level behaviour is covered here; the aggregation logic of
# ResourceService.get_idle_status is covered by the unit tests below.
# ---------------------------------------------------------------------------


def _idle_app(get_idle_status):
    """Build an app whose service.resources.get_idle_status is the given awaitable.

    Uses dev auth (no API key required) so we can drive the route without
    initialising the APIKeyManager (the ASGI transport does not run lifespan).
    """
    from openviking.server.auth.plugins import DevAuthPlugin
    from openviking.server.auth.registry import get_registry
    from openviking.server.dependencies import set_service

    service = SimpleNamespace(
        resources=SimpleNamespace(get_idle_status=get_idle_status),
    )
    app = create_app(config=ServerConfig(), service=service)
    set_service(service)
    registry = get_registry()
    if registry.get("dev") is None:
        registry.register(DevAuthPlugin)
    app.state.auth_plugin = DevAuthPlugin()
    return app


async def test_system_idle_route_returns_true():
    """Route forwards the ResourceService result and returns 200 when idle."""

    async def _idle_true():
        return {
            "idle": True,
            "pending": 0,
            "breakdown": {
                "queue": {"pending": 0, "by_queue": {}},
                "tasks": {"pending": 0, "by_type": {}},
            },
        }

    app = _idle_app(_idle_true)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/system/idle")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["idle"] is True
    assert body["result"]["pending"] == 0


async def test_system_idle_route_returns_false():
    """Route propagates idle=false when the service reports pending work."""

    async def _idle_false():
        return {
            "idle": False,
            "pending": 3,
            "breakdown": {
                "queue": {"pending": 2, "by_queue": {"Embedding": {"pending": 0, "in_progress": 2}}},
                "tasks": {"pending": 1, "by_type": {"session_commit": 1}},
            },
        }

    app = _idle_app(_idle_false)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/system/idle")

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["idle"] is False
    assert body["result"]["pending"] == 3


# ---------------------------------------------------------------------------
# ResourceService.get_idle_status aggregation logic (unit-level, no HTTP)
# ---------------------------------------------------------------------------


async def test_get_idle_status_when_idle(monkeypatch):
    """Both sources at zero => idle=True, pending=0."""
    from openviking.service.resource_service import ResourceService
    from openviking.storage.queuefs.named_queue import QueueStatus

    class _FakeQM:
        async def check_status(self):
            return {"Embedding": QueueStatus(pending=0, in_progress=0)}

    monkeypatch.setattr(
        "openviking.service.resource_service.get_queue_manager", lambda: _FakeQM()
    )

    class _FakeTracker:
        def count_active(self):
            return 0

        def snapshot_active_counts_by_type(self):
            return {}

    import openviking.service.task_tracker as tt

    monkeypatch.setattr(tt, "get_task_tracker", lambda: _FakeTracker())

    result = await ResourceService().get_idle_status()

    assert result["idle"] is True
    assert result["pending"] == 0
    assert result["breakdown"]["queue"]["pending"] == 0
    assert result["breakdown"]["queue"]["by_queue"]["Embedding"] == {
        "pending": 0,
        "in_progress": 0,
    }
    assert result["breakdown"]["tasks"]["pending"] == 0
    assert result["breakdown"]["tasks"]["by_type"] == {}


async def test_get_idle_status_with_queue_work(monkeypatch):
    """Queue with in-progress items => idle=False, pending reflects queue work."""
    from openviking.service.resource_service import ResourceService
    from openviking.storage.queuefs.named_queue import QueueStatus

    class _FakeQM:
        async def check_status(self):
            return {
                "Embedding": QueueStatus(pending=0, in_progress=2),
                "Semantic": QueueStatus(pending=1, in_progress=0),
            }

    monkeypatch.setattr(
        "openviking.service.resource_service.get_queue_manager", lambda: _FakeQM()
    )

    class _FakeTracker:
        def count_active(self):
            return 0

        def snapshot_active_counts_by_type(self):
            return {}

    import openviking.service.task_tracker as tt

    monkeypatch.setattr(tt, "get_task_tracker", lambda: _FakeTracker())

    result = await ResourceService().get_idle_status()

    assert result["idle"] is False
    # 2 in_progress + 1 pending across the two queues.
    assert result["pending"] == 3
    assert result["breakdown"]["queue"]["by_queue"]["Embedding"]["in_progress"] == 2
    assert result["breakdown"]["queue"]["by_queue"]["Semantic"]["pending"] == 1


async def test_get_idle_status_with_active_task(monkeypatch):
    """Central TaskTracker with a running task => idle=False."""
    from openviking.service.resource_service import ResourceService
    from openviking.storage.queuefs.named_queue import QueueStatus

    class _FakeQM:
        async def check_status(self):
            return {"Embedding": QueueStatus(pending=0, in_progress=0)}

    monkeypatch.setattr(
        "openviking.service.resource_service.get_queue_manager", lambda: _FakeQM()
    )

    class _FakeTracker:
        def count_active(self):
            return 1

        def snapshot_active_counts_by_type(self):
            return {"session_commit": 1}

    import openviking.service.task_tracker as tt

    monkeypatch.setattr(tt, "get_task_tracker", lambda: _FakeTracker())

    result = await ResourceService().get_idle_status()

    assert result["idle"] is False
    assert result["pending"] == 1
    assert result["breakdown"]["tasks"]["by_type"] == {"session_commit": 1}


async def test_get_idle_status_conservative_on_queue_error(monkeypatch):
    """If a source raises, idle must be forced to False (never claim idle)."""
    from openviking.service.resource_service import ResourceService

    def _boom():
        raise RuntimeError("QueueManager is not initialized")

    monkeypatch.setattr(
        "openviking.service.resource_service.get_queue_manager", _boom
    )

    class _FakeTracker:
        def count_active(self):
            return 0

        def snapshot_active_counts_by_type(self):
            return {}

    import openviking.service.task_tracker as tt

    monkeypatch.setattr(tt, "get_task_tracker", lambda: _FakeTracker())

    result = await ResourceService().get_idle_status()

    # Uncertain source => never report idle=True even though pending==0.
    assert result["idle"] is False
    assert "error" in result["breakdown"]["queue"]


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
