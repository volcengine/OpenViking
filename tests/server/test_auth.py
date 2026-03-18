# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for multi-tenant authentication (openviking/server/auth.py)."""

import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from openviking.server.app import create_app
from openviking.server.auth import get_request_context, resolve_identity
from openviking.server.config import ServerConfig, _is_localhost, validate_server_config
from openviking.server.dependencies import set_service
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.service.core import OpenVikingService
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError
from openviking_cli.session.user_id import UserIdentifier


def _uid() -> str:
    return f"acct_{uuid.uuid4().hex[:8]}"


ROOT_KEY = "root-secret-key-for-testing-only-1234567890abcdef"


def _make_request(
    path: str,
    headers: dict[str, str] | None = None,
    auth_enabled: bool = True,
) -> Request:
    """Create a minimal Starlette request for auth dependency tests."""
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    app = FastAPI()
    if auth_enabled:
        # Non-empty api_key_manager means the server is in authenticated mode.
        app.state.api_key_manager = object()
    scope = {
        "type": "http",
        "path": path,
        "headers": raw_headers,
        "app": app,
    }
    return Request(scope)


def _build_auth_http_test_app(
    identity: ResolvedIdentity,
    auth_enabled: bool = True,
) -> FastAPI:
    """Create a lightweight app that exercises auth dependency wiring.

    The full server fixture depends on AGFS native libraries. This helper keeps
    the test focused on request auth behavior and the structured HTTP error body.
    """
    app = FastAPI()
    if auth_enabled:
        # Match production auth mode so get_request_context enters the guard path.
        app.state.api_key_manager = object()

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request: FastAPIRequest, exc: OpenVikingError):
        """Mirror the server's JSON error envelope for auth failures."""
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                ),
            ).model_dump(),
        )

    async def _resolve_identity_override() -> ResolvedIdentity:
        """Return a fixed identity so tests can isolate request header behavior."""
        return identity

    app.dependency_overrides[resolve_identity] = _resolve_identity_override

    @app.get("/api/v1/fs/ls")
    async def fs_ls(ctx=Depends(get_request_context)):
        """Expose a tenant-scoped route for auth regression tests."""
        return {
            "status": "ok",
            "result": {
                "account_id": ctx.user.account_id,
                "user_id": ctx.user.user_id,
            },
        }

    @app.get("/api/v1/observer/system")
    async def observer_system(ctx=Depends(get_request_context)):
        """Expose a monitoring route that should keep implicit ROOT behavior."""
        return {"status": "ok", "result": {"role": ctx.role.value}}

    @app.post("/api/v1/system/wait")
    async def system_wait(ctx=Depends(get_request_context)):
        """Expose a non-tenant system route for auth regression tests."""
        return {"status": "ok", "result": {"role": ctx.role.value}}

    @app.get("/api/v1/debug/vector/scroll")
    async def debug_vector_scroll(ctx=Depends(get_request_context)):
        """Expose a tenant-scoped debug route for auth regression tests."""
        return {"status": "ok", "result": {"role": ctx.role.value}}

    return app


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
    manager = APIKeyManager(root_key=ROOT_KEY, agfs_client=auth_service._agfs)
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
    key = await manager.create_account(_uid(), "test_admin")
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
    """Protected endpoints should require auth before any role-specific checks."""
    endpoints = [
        ("GET", "/api/v1/system/status"),
        ("GET", "/api/v1/observer/system"),
        ("GET", "/api/v1/debug/health"),
        ("GET", "/api/v1/fs/ls?uri=viking://"),
    ]
    for method, url in endpoints:
        resp = await auth_client.request(method, url)
        assert resp.status_code == 401, f"{method} {url} should require auth"

    for method, url in endpoints[:3]:
        resp = await auth_client.request(method, url, headers={"X-API-Key": ROOT_KEY})
        assert resp.status_code == 200, f"{method} {url} should succeed with root key"

    tenant_resp = await auth_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": ROOT_KEY},
    )
    assert tenant_resp.status_code == 400
    assert tenant_resp.json()["error"]["code"] == "INVALID_ARGUMENT"

    tenant_resp = await auth_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={
            "X-API-Key": ROOT_KEY,
            "X-OpenViking-Account": "default",
            "X-OpenViking-User": "default",
        },
    )
    assert tenant_resp.status_code == 200


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


async def test_cross_tenant_session_get_returns_not_found(auth_client: httpx.AsyncClient, auth_app):
    """A user must not access another tenant's session by session_id."""
    manager = auth_app.state.api_key_manager
    alice_key = await manager.create_account(_uid(), "alice")
    bob_key = await manager.create_account(_uid(), "bob")

    create_resp = await auth_client.post(
        "/api/v1/sessions", json={}, headers={"X-API-Key": alice_key}
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["result"]["session_id"]

    add_resp = await auth_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "hello from alice"},
        headers={"X-API-Key": alice_key},
    )
    assert add_resp.status_code == 200

    own_get = await auth_client.get(
        f"/api/v1/sessions/{session_id}", headers={"X-API-Key": alice_key}
    )
    assert own_get.status_code == 200
    assert own_get.json()["result"]["message_count"] == 1

    cross_get = await auth_client.get(
        f"/api/v1/sessions/{session_id}", headers={"X-API-Key": bob_key}
    )
    assert cross_get.status_code == 404
    assert cross_get.json()["error"]["code"] == "NOT_FOUND"


async def test_root_tenant_scoped_requests_require_explicit_identity():
    """ROOT must specify account/user headers on tenant-scoped APIs."""
    request = _make_request("/api/v1/resources", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    with pytest.raises(InvalidArgumentError, match="X-OpenViking-Account"):
        await get_request_context(request, identity)


async def test_root_system_status_allows_implicit_default_identity():
    """ROOT may call status endpoints without explicit tenant headers."""
    request = _make_request("/api/v1/system/status", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ROOT
    assert ctx.user.account_id == "default"
    assert ctx.user.user_id == "default"


async def test_root_tenant_scoped_requests_allow_explicit_identity():
    """ROOT can access tenant-scoped APIs when account/user headers are present."""
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": "acme",
            "X-OpenViking-User": "alice",
        },
        auth_enabled=True,
    )
    identity = ResolvedIdentity(role=Role.ROOT, account_id="acme", user_id="alice")

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ROOT
    assert ctx.user.account_id == "acme"
    assert ctx.user.user_id == "alice"


async def test_root_monitoring_requests_allow_implicit_default_identity():
    """Observer/debug endpoints keep the existing ROOT monitoring flow."""
    observer_request = _make_request("/api/v1/observer/system", auth_enabled=True)
    debug_request = _make_request("/api/v1/debug/health", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    observer_ctx = await get_request_context(observer_request, identity)
    debug_ctx = await get_request_context(debug_request, identity)

    assert observer_ctx.role == Role.ROOT
    assert debug_ctx.role == Role.ROOT


async def test_root_system_wait_allows_implicit_default_identity():
    """ROOT may call system wait without explicit tenant headers."""
    request = _make_request("/api/v1/system/wait", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ROOT


async def test_root_debug_vector_requests_require_explicit_identity():
    """Tenant-scoped debug routes must not bypass explicit tenant checks."""
    request = _make_request("/api/v1/debug/vector/scroll", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    with pytest.raises(InvalidArgumentError, match="X-OpenViking-Account"):
        await get_request_context(request, identity)


async def test_dev_mode_root_tenant_scoped_requests_allow_implicit_identity():
    """Dev mode should keep the existing implicit ROOT/default behavior."""
    request = _make_request("/api/v1/resources", auth_enabled=False)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ROOT
    assert ctx.user.account_id == "default"
    assert ctx.user.user_id == "default"


async def test_root_tenant_scoped_requests_return_structured_400_via_http():
    """Tenant-scoped HTTP routes should reject implicit ROOT tenant fallback."""
    app = _build_auth_http_test_app(
        ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default"),
        auth_enabled=True,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/fs/ls")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_root_monitoring_requests_keep_200_via_http():
    """Monitoring HTTP routes should still work with implicit ROOT identity."""
    app = _build_auth_http_test_app(
        ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default"),
        auth_enabled=True,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/observer/system")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_root_system_wait_keeps_200_via_http():
    """System wait should keep working for ROOT without tenant headers."""
    app = _build_auth_http_test_app(
        ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default"),
        auth_enabled=True,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/system/wait")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_root_debug_vector_requests_return_structured_400_via_http():
    """Tenant-scoped debug routes should reject implicit ROOT tenant fallback."""
    app = _build_auth_http_test_app(
        ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default"),
        auth_enabled=True,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/debug/vector/scroll")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_dev_mode_root_tenant_scoped_requests_keep_200_via_http():
    """Dev mode HTTP routes should keep the existing implicit ROOT/default behavior."""
    app = _build_auth_http_test_app(
        ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default"),
        auth_enabled=False,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/fs/ls")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---- _is_localhost tests ----


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_is_localhost_true(host: str):
    assert _is_localhost(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.1", "10.0.0.1"])
def test_is_localhost_false(host: str):
    assert _is_localhost(host) is False


# ---- validate_server_config tests ----


def test_validate_no_key_localhost_passes():
    """No root_api_key + localhost should pass validation."""
    for host in ("127.0.0.1", "localhost", "::1"):
        config = ServerConfig(host=host, root_api_key=None)
        validate_server_config(config)  # should not raise


def test_validate_no_key_non_localhost_raises():
    """No root_api_key + non-localhost should raise SystemExit."""
    config = ServerConfig(host="0.0.0.0", root_api_key=None)
    with pytest.raises(SystemExit):
        validate_server_config(config)


def test_validate_with_key_any_host_passes():
    """With root_api_key set, any host should pass validation."""
    for host in ("0.0.0.0", "::", "192.168.1.1", "127.0.0.1"):
        config = ServerConfig(host=host, root_api_key="some-secret-key")
        validate_server_config(config)  # should not raise
