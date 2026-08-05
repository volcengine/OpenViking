# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Role-gating tests for the debug vector endpoints (issue #3724).

The ``/api/v1/debug/vector/scroll`` and ``/api/v1/debug/vector/count`` routes
expose raw vector-store records. They must be restricted to ROOT/ADMIN callers
so an ordinary USER cannot read co-tenant data that happens to share the same
``account_id`` filter.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse

# Ensure built-in auth plugins are registered before building requests.
import openviking.server.auth.plugins  # noqa: F401
from openviking.server.auth import resolve_identity
from openviking.server.auth.registry import get_registry
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.routers import debug as debug_router
from openviking_cli.exceptions import OpenVikingError


class _StubService:
    """Minimal service stub.

    ``vikingdb_manager`` is intentionally ``None`` so a *permitted* request
    reaches the handler and returns the structured ``NO_VECTOR_DB`` response
    without touching any native vector engine.
    """

    vikingdb_manager = None

    class _Debug:
        @staticmethod
        def is_healthy():
            return True

    debug = _Debug()


def _build_debug_test_app(identity: ResolvedIdentity, auth_enabled: bool = True) -> FastAPI:
    # When auth is disabled the server falls back to dev mode, which allows the
    # implicit ROOT/default identity to reach tenant-scoped routes.
    effective_auth_mode = "api_key" if auth_enabled else "dev"
    app = FastAPI()
    app.state.config = ServerConfig(auth_mode=effective_auth_mode)
    # Authenticated mode: presence of an api_key_manager enables the data-plane
    # guard inside get_request_context (which rejects ROOT API keys on these
    # tenant-scoped debug routes).
    if auth_enabled:
        app.state.api_key_manager = object()
    plugin_cls = get_registry().get(effective_auth_mode)
    if plugin_cls is not None:
        app.state.auth_plugin = plugin_cls()

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request: FastAPIRequest, exc: OpenVikingError):
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
        return identity

    app.dependency_overrides[resolve_identity] = _resolve_identity_override
    app.include_router(debug_router.router)
    return app


@pytest.fixture
def stub_service():
    set_service(_StubService())
    yield
    set_service(None)


def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.parametrize("path", ["/api/v1/debug/vector/scroll", "/api/v1/debug/vector/count"])
@pytest.mark.asyncio
async def test_user_rejected_from_debug_vector_endpoints(stub_service, path):
    identity = ResolvedIdentity(role=Role.USER, account_id="acme", user_id="alice")
    app = _build_debug_test_app(identity)
    async with _client(app) as client:
        response = await client.get(path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    "role",
    [Role.ADMIN],
)
@pytest.mark.parametrize("path", ["/api/v1/debug/vector/scroll", "/api/v1/debug/vector/count"])
@pytest.mark.asyncio
async def test_admin_reaches_debug_vector_endpoints_in_api_key_mode(stub_service, role, path):
    """In authenticated api_key mode an ADMIN caller passes both the data-plane
    guard and the new role gate."""
    identity = ResolvedIdentity(role=role, account_id="acme", user_id="operator")
    app = _build_debug_test_app(identity, auth_enabled=True)
    async with _client(app) as client:
        response = await client.get(path)
    assert response.status_code == 200
    body = response.json()
    # The role gate passed; with no vector DB configured the handler reports the
    # structured error instead of leaking records.
    assert body["status"] == "error"
    assert body["error"]["code"] == "NO_VECTOR_DB"


@pytest.mark.parametrize("path", ["/api/v1/debug/vector/scroll", "/api/v1/debug/vector/count"])
@pytest.mark.asyncio
async def test_root_api_key_rejected_from_debug_vector_endpoints(stub_service, path):
    """ROOT API keys remain blocked on these tenant-scoped routes by the
    pre-existing data-plane guard (regression guard for issue #3724 context)."""
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")
    app = _build_debug_test_app(identity, auth_enabled=True)
    async with _client(app) as client:
        response = await client.get(path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("path", ["/api/v1/debug/vector/scroll", "/api/v1/debug/vector/count"])
@pytest.mark.asyncio
async def test_dev_mode_root_reaches_debug_vector_endpoints(stub_service, path):
    """Dev mode retains the implicit ROOT/default access to the debug vectors."""
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")
    app = _build_debug_test_app(identity, auth_enabled=False)
    async with _client(app) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "NO_VECTOR_DB"


@pytest.mark.asyncio
async def test_debug_health_remains_authenticated_but_not_role_gated(stub_service):
    """Health is a liveness probe and must not require ADMIN."""
    identity = ResolvedIdentity(role=Role.USER, account_id="acme", user_id="alice")
    app = _build_debug_test_app(identity)
    async with _client(app) as client:
        response = await client.get("/api/v1/debug/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
