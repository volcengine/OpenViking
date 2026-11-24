# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for server infrastructure: health, system status, middleware, error handling."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from openviking.server.app import _initialize_auth_plugin, _initialize_runtime_state, create_app
from openviking.server.config import ServerConfig


async def test_health_endpoint(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_health_endpoint_checks_supplied_api_key_without_requiring_it(service):
    key = "test-root-key"
    config = ServerConfig(auth_mode="api_key", host="127.0.0.1", root_api_key=key)
    app = create_app(config=config, service=service)
    await _initialize_auth_plugin(app, service, config)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            anonymous = await client.get("/health")
            assert anonymous.status_code == 200
            assert anonymous.json()["auth_mode"] == "api_key"
            assert anonymous.json()["healthy"] is True
            assert {"account_id", "user_id", "role"}.isdisjoint(anonymous.json())

            authenticated = await client.get("/health", headers={"X-API-Key": key})
            assert authenticated.status_code == 200
            identity = authenticated.json()
            assert (identity["account_id"], identity["user_id"], identity["role"]) == (
                "default",
                "default",
                "root",
            )

            rejected = await client.get("/health", headers={"X-API-Key": "stale-key"})
            assert rejected.status_code == 401
            assert rejected.json()["error"] == {
                "code": "UNAUTHENTICATED",
                "message": "Invalid API key format",
                "details": {},
            }
    finally:
        await app.state.auth_plugin.shutdown()


@pytest.mark.parametrize(
    "credentials",
    [
        "anonymous",
        "partial",
        "trusted",
        "keyed",
        "malformed",
        "asserted-root",
        "root-only",
        "invalid-root-only",
    ],
)
async def test_health_trusted_identity_contract(credentials, service):
    app = create_app(
        config=ServerConfig(auth_mode="trusted", host="127.0.0.1", root_api_key="test-root-key"),
        service=service,
    )
    await _initialize_auth_plugin(app, service, app.state.config)
    headers = {}
    if credentials not in ("anonymous", "root-only", "invalid-root-only"):
        headers["X-OpenViking-Account"] = "account-a"
    if credentials in ("trusted", "keyed", "malformed", "asserted-root"):
        headers["X-OpenViking-User"] = "alice"
    if credentials in ("keyed", "malformed", "asserted-root", "root-only"):
        headers["X-API-Key"] = "test-root-key"
    if credentials == "invalid-root-only":
        headers["X-API-Key"] = "invalid-key"
    if credentials == "malformed":
        headers["X-OpenViking-User"] = "alice/../root"
    if credentials == "asserted-root":
        headers["X-OpenViking-Role"] = "root"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health", headers=headers)
    finally:
        await app.state.auth_plugin.shutdown()
    body = response.json()
    expected_status = 401 if credentials in ("trusted", "invalid-root-only") else 200
    assert response.status_code == expected_status
    if expected_status == 401:
        assert body["error"]["code"] == "UNAUTHENTICATED"
        return
    if credentials == "keyed":
        assert (body["account_id"], body["user_id"], body["role"]) == ("account-a", "alice", "user")
    elif credentials == "root-only":
        assert body["role"] == "root"
    else:
        assert "role" not in body
    assert body["root_api_key_required"] is True
    if credentials == "malformed":
        assert {"account_id", "user_id", "role"}.isdisjoint(body)


async def test_system_status(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["initialized"] is True


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("POST", "/api/v1/system/backend/sync-status", {"uri": "viking://resources"}),
        ("POST", "/api/v1/system/backend/sync-retry", {"uri": "viking://resources"}),
        ("GET", "/api/v1/system/sync/viking://resources", None),
        ("POST", "/api/v1/system/sync/viking://resources/retry", None),
    ],
)
async def test_removed_sync_routes_return_404(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    json: dict | None,
):
    """Removed V1 sync routes must stay unregistered."""
    resp = await client.request(method, path, json=json)
    assert resp.status_code == 404


async def test_process_time_header(client: httpx.AsyncClient):
    resp = await client.get("/health")
    assert "x-process-time" in resp.headers
    value = float(resp.headers["x-process-time"])
    assert value >= 0


async def test_openviking_error_handler(client: httpx.AsyncClient):
    """Requesting a non-existent resource should return structured error."""
    resp = await client.get(
        "/api/v1/fs/stat", params={"uri": "viking://resources/nonexistent/path"}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] is not None


async def test_404_for_unknown_route(client: httpx.AsyncClient):
    resp = await client.get("/this/route/does/not/exist")
    assert resp.status_code == 404


async def test_lifespan_shutdown_ignores_cancelled_service_close(monkeypatch):
    closing = asyncio.Event()

    class _Service:
        def __init__(self):
            self.viking_fs = object()
            self._queue_manager = None

        async def initialize(self, **kwargs):
            pass

        async def apply_agent_evolution_config(self):
            pass

        async def close(self):
            closing.set()
            raise asyncio.CancelledError("shutdown")

    monkeypatch.setattr("openviking.server.app.OpenVikingService", _Service)
    monkeypatch.setattr(
        "openviking.server.app.get_task_tracker",
        lambda: SimpleNamespace(start_cleanup_loop=lambda: None, stop_cleanup_loop=lambda: None),
    )
    app = create_app(config=ServerConfig())

    async with app.router.lifespan_context(app):
        pass
    assert closing.is_set()


async def test_readiness_tracks_startup_and_filesystem_health(monkeypatch):
    service = SimpleNamespace(_initialized=False, embedding_provider=object())
    filesystem = SimpleNamespace(
        ls=AsyncMock(return_value=[]),
        _get_vector_store=lambda: SimpleNamespace(health_check=AsyncMock(return_value=True)),
    )
    monkeypatch.setattr("openviking.server.dependencies._service", service)
    monkeypatch.setattr("openviking.server.routers.system.get_viking_fs", lambda: filesystem)
    monkeypatch.setattr(
        "openviking_cli.utils.ollama.detect_ollama_in_config",
        lambda config: (False, None, None),
    )

    app = create_app(config=ServerConfig(), service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        liveness = await client.get("/health")
        assert liveness.status_code == 200
        assert liveness.json()["healthy"] is True
        initializing = await client.get("/ready")
        assert initializing.status_code == 503
        assert initializing.json() == {"status": "not_ready", "reason": "initializing"}
        filesystem.ls.assert_not_awaited()

        service._initialized = True
        ready = await client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "checks": {
                "agfs": {"status": "ok", "checks": {"filesystem": "ok"}},
                "api_key_manager": "not_configured",
                "embedding": "ok",
                "ollama": "not_configured",
                "vectordb": "ok",
            },
        }

        filesystem.ls.side_effect = OSError("storage unavailable")
        unavailable = await client.get("/ready")
        assert unavailable.status_code == 503
        assert unavailable.json()["status"] == "not_ready"
        assert unavailable.json()["checks"]["agfs"] == {
            "status": "error",
            "checks": {"filesystem": "error: storage unavailable"},
        }
        assert (await client.get("/health")).status_code == 200


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
    """Install TTL hooks before workers; preserve system-asset reconciliation."""
    events = []
    ov_config = SimpleNamespace(reconcile_system_assets_on_startup=True)
    monkeypatch.setattr("openviking.server.app.get_openviking_config", lambda: ov_config)

    class MockService:
        def __init__(self):
            self._initialized = False
            self.viking_fs = object()
            self._queue_manager = None
            self.compile = SimpleNamespace(lifecycle=SimpleNamespace(resolve_groups=None))

        async def initialize(self, **kwargs):
            await kwargs["before_background_start"]()
            events.extend(["workers", "service"])
            self._initialized = True

        async def apply_agent_evolution_config(self):
            events.append("agent-evolution")

        async def reconcile_system_assets(self, account_lister):
            assert account_lister is manager
            events.append("system-assets")
            return 1

    manager = SimpleNamespace(
        resolve_identity=Mock(), get_user_group_ids=AsyncMock(return_value=("editors",))
    )
    ttl_init = AsyncMock(side_effect=lambda *args: events.append("ttl"))
    monkeypatch.setattr("openviking.server.app._initialize_ttl_adapter", ttl_init)
    setup = AsyncMock(side_effect=lambda **kwargs: events.append("deletion"))
    monkeypatch.setattr("openviking.service.deletion.setup_deletion", setup)

    async def initialize_plugin(app, service, config):
        app.state.api_key_manager = manager
        events.append("auth")

    monkeypatch.setattr("openviking.server.app._initialize_auth_plugin", initialize_plugin)

    app = SimpleNamespace(state=SimpleNamespace(api_key_manager=None))
    service = MockService()
    service.compile_scheduler = SimpleNamespace(set_identity_resolver=Mock())
    config = ServerConfig(
        root_api_key="root-key-for-test",
        ttl_adapter={"enabled": True},
    )
    await _initialize_runtime_state(app, service, config)

    startup = ["auth", "ttl", "deletion", "workers", "service", "agent-evolution"]
    assert events == startup + ["system-assets"]
    assert service._initialized is True
    assert app.state.api_key_manager is manager
    assert service.compile.lifecycle.resolve_groups is manager.get_user_group_ids
    assert setup.await_args.kwargs["manager"] is manager
    ttl_init.assert_awaited_once_with(app, service, config)
    service.compile_scheduler.set_identity_resolver.assert_called_once_with(
        manager.resolve_identity, manager.get_user_group_ids
    )

    events.clear()
    ov_config.reconcile_system_assets_on_startup = False
    await _initialize_runtime_state(app, service, config)

    assert service._initialized is True
    assert app.state.api_key_manager is manager
    assert events == startup


async def test_initialize_runtime_state_skips_reconcile_without_account_manager(monkeypatch):
    service = SimpleNamespace(
        initialize=AsyncMock(),
        apply_agent_evolution_config=AsyncMock(),
        reconcile_system_assets=AsyncMock(),
    )
    setup = AsyncMock(return_value=None)
    monkeypatch.setattr("openviking.service.deletion.setup_deletion", setup)

    async def initialize_plugin(app, service, config):
        app.state.api_key_manager = None

    monkeypatch.setattr("openviking.server.app._initialize_auth_plugin", initialize_plugin)

    async def initialize(**kwargs):
        await kwargs["before_background_start"]()

    service.initialize.side_effect = initialize

    app = SimpleNamespace(state=SimpleNamespace(api_key_manager=None))
    await _initialize_runtime_state(app, service, ServerConfig())

    service.reconcile_system_assets.assert_not_awaited()
    assert setup.await_args.kwargs["manager"] is None
