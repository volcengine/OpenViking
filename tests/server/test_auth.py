# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for multi-tenant authentication (openviking/server/auth.py)."""

import httpx
import pytest_asyncio

from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier

ROOT_KEY = "root-secret-key-for-testing-only-1234567890abcdef"


@pytest_asyncio.fixture(scope="function")
async def auth_service(temp_dir):
    """Service for auth tests."""
    svc = OpenVikingService(
        path=str(temp_dir / "auth_data"), user=UserIdentifier.the_default_user("auth_user")
    )
    await svc.initialize()
    yield svc
    await svc.close()


@pytest_asyncio.fixture(scope="function")
async def auth_app(auth_service):
    """App with root_api_key configured and APIKeyManager loaded."""
    from openviking.server.api_keys import APIKeyManager

    config = ServerConfig(root_api_key=ROOT_KEY)
    app = create_app(config=config, service=auth_service)
    set_service(auth_service)

    # Manually initialize APIKeyManager (lifespan not triggered in ASGI tests)
    manager = APIKeyManager(root_key=ROOT_KEY, agfs_url=auth_service._agfs_url)
    await manager.load()
    app.state.api_key_manager = manager

    return app


@pytest_asyncio.fixture(scope="function")
async def auth_client(auth_app):
    """Client bound to auth-enabled app."""
    transport = httpx.ASGITransport(app=auth_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(scope="function")
async def user_key(auth_app):
    """Create a test user and return its key."""
    manager = auth_app.state.api_key_manager
    key = await manager.create_account("test_account", "test_admin")
    return key


# ---- Basic auth tests ----


async def test_health_no_auth_required(auth_client: httpx.AsyncClient):
    """/health should be accessible without any API key."""
    resp = await auth_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_root_key_via_x_api_key(auth_client: httpx.AsyncClient):
    """Root key via X-API-Key should grant ROOT access."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"X-API-Key": ROOT_KEY},
    )
    assert resp.status_code == 200


async def test_root_key_via_bearer(auth_client: httpx.AsyncClient):
    """Root key via Bearer token should grant ROOT access."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"Authorization": f"Bearer {ROOT_KEY}"},
    )
    assert resp.status_code == 200


async def test_user_key_access(auth_client: httpx.AsyncClient, user_key: str):
    """User key should grant access to regular endpoints."""
    resp = await auth_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": user_key},
    )
    assert resp.status_code == 200


async def test_missing_key_returns_401(auth_client: httpx.AsyncClient):
    """Request without API key should return 401."""
    resp = await auth_client.get("/api/v1/system/status")
    assert resp.status_code == 401
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "UNAUTHENTICATED"


async def test_wrong_key_returns_401(auth_client: httpx.AsyncClient):
    """Request with invalid key should return 401."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"X-API-Key": "definitely-wrong-key"},
    )
    assert resp.status_code == 401


async def test_bearer_without_prefix_fails(auth_client: httpx.AsyncClient):
    """Authorization header without 'Bearer ' prefix should fail."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"Authorization": ROOT_KEY},
    )
    assert resp.status_code == 401


async def test_dev_mode_no_auth(client: httpx.AsyncClient):
    """When no root_api_key configured (dev mode), all requests pass as ROOT."""
    resp = await client.get("/api/v1/system/status")
    assert resp.status_code == 200


async def test_auth_on_multiple_endpoints(auth_client: httpx.AsyncClient):
    """Multiple protected endpoints should require auth."""
    endpoints = [
        ("GET", "/api/v1/system/status"),
        ("GET", "/api/v1/fs/ls?uri=viking://"),
        ("GET", "/api/v1/observer/system"),
        ("GET", "/api/v1/debug/health"),
    ]
    for method, url in endpoints:
        resp = await auth_client.request(method, url)
        assert resp.status_code == 401, f"{method} {url} should require auth"

    for method, url in endpoints:
        resp = await auth_client.request(method, url, headers={"X-API-Key": ROOT_KEY})
        assert resp.status_code == 200, f"{method} {url} should succeed with root key"


# ---- Role-based access tests ----


async def test_user_key_cannot_access_admin_api(auth_client: httpx.AsyncClient, user_key: str):
    """User key (ADMIN role) should NOT access ROOT-only admin endpoints."""
    # list accounts is ROOT-only
    resp = await auth_client.get(
        "/api/v1/admin/accounts",
        headers={"X-API-Key": user_key},
    )
    # ADMIN can't list all accounts (ROOT only)
    assert resp.status_code == 403


async def test_agent_id_header_forwarded(auth_client: httpx.AsyncClient):
    """X-OpenViking-Agent header should be captured in identity."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"X-API-Key": ROOT_KEY, "X-OpenViking-Agent": "my-agent"},
    )
    assert resp.status_code == 200
