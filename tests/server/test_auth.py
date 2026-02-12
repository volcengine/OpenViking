# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for API key authentication (openviking/server/auth.py)."""

import httpx
import pytest_asyncio

from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier

TEST_API_KEY = "test-secret-key-12345"


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
    """App with API key configured."""
    config = ServerConfig(api_key=TEST_API_KEY)
    app = create_app(config=config, service=auth_service)
    set_service(auth_service)
    return app


@pytest_asyncio.fixture(scope="function")
async def auth_client(auth_app):
    """Client bound to auth-enabled app."""
    transport = httpx.ASGITransport(app=auth_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---- Tests ----


async def test_health_no_auth_required(auth_client: httpx.AsyncClient):
    """/health should be accessible without any API key."""
    resp = await auth_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_valid_x_api_key_header(auth_client: httpx.AsyncClient):
    """Valid X-API-Key header should grant access."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert resp.status_code == 200


async def test_valid_bearer_token(auth_client: httpx.AsyncClient):
    """Valid Bearer token should grant access."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
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
    """Request with wrong API key should return 401."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"X-API-Key": "wrong-key"},
    )
    assert resp.status_code == 401


async def test_no_api_key_configured_skips_auth(client: httpx.AsyncClient):
    """When no API key is configured, all requests should pass."""
    resp = await client.get("/api/v1/system/status")
    assert resp.status_code == 200


async def test_bearer_without_prefix_fails(auth_client: httpx.AsyncClient):
    """Authorization header without 'Bearer ' prefix should fail."""
    resp = await auth_client.get(
        "/api/v1/system/status",
        headers={"Authorization": TEST_API_KEY},
    )
    assert resp.status_code == 401


async def test_auth_on_protected_endpoints(auth_client: httpx.AsyncClient):
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

    # Same endpoints with valid key should work
    for method, url in endpoints:
        resp = await auth_client.request(method, url, headers={"X-API-Key": TEST_API_KEY})
        assert resp.status_code == 200, f"{method} {url} should succeed with key"
