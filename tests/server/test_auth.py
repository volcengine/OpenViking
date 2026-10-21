# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for multi-tenant authentication (openviking/server/auth.py)."""

import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from openviking.server.api_keys import APIKeyManager
from openviking.server.app import create_app
from openviking.server.auth import get_request_context, resolve_identity
from openviking.server.auth.plugins.api_key import _api_key_root_can_access_path
from openviking.server.auth.registry import get_registry
from openviking.server.config import ServerConfig, _is_localhost, validate_server_config
from openviking.server.dependencies import set_service
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.store_assembly import build_api_key_manager
from openviking.service.core import OpenVikingService
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import (
    TaskTracker,
    get_task_tracker,
    set_task_tracker,
)
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def _uid() -> str:
    return f"acct_{uuid.uuid4().hex[:8]}"


class _FakeAgfs:
    def __init__(self):
        self.files = {}
        self.dirs = {"/", "/local"}

    def mkdir(self, path: str, mode: str = "755"):
        self.dirs.add(path.rstrip("/") or "/")
        return {"message": "created", "mode": mode}

    def write(self, path: str, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.files[path] = data
        self.dirs.add(path.rsplit("/", 1)[0] or "/")
        return "OK"

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False):
        del stream
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        return data[offset : offset + size] if size >= 0 else data[offset:]

    def ls(self, path: str = "/"):
        prefix = path.rstrip("/") or "/"
        if prefix not in self.dirs:
            return []
        return [
            {"name": file_path[len(prefix) + 1 :], "path": file_path, "is_dir": False}
            for file_path in self.files
            if file_path.startswith(prefix + "/") and "/" not in file_path[len(prefix) + 1 :]
        ]

    def rm(self, path: str, recursive: bool = False, force: bool = True):
        self.files.pop(path, None)
        return {"message": "deleted"}


def _set_fake_task_tracker():
    set_task_tracker(TaskTracker(store=PersistentTaskStore(_FakeAgfs())))


ROOT_KEY = "root-secret-key-for-testing-only-1234567890abcdef"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/system/backend/sync-status",
        "/api/v1/system/backend/sync-retry",
        "/api/v1/system/sync/viking://resources",
        "/api/v1/system/sync/viking://resources/retry",
    ],
)
def test_root_key_allowlist_rejects_removed_sync_paths(path: str):
    """Removed sync paths must stay outside the API-key root allowlist."""
    assert not _api_key_root_can_access_path(path)


def _make_request(
    path: str,
    headers: dict[str, str] | None = None,
    auth_enabled: bool = True,
    auth_mode: str = "api_key",
    root_api_key: str | None = None,
    api_key_manager=None,
) -> Request:
    """Create a minimal Starlette request for auth dependency tests."""
    # Ensure built-in plugins are registered
    import openviking.server.auth.plugins  # noqa: F401

    if auth_mode == "trusted" and root_api_key is None:
        root_api_key = ROOT_KEY
    request_headers = dict(headers or {})
    if auth_mode == "trusted":
        request_headers.setdefault("X-API-Key", ROOT_KEY)

    raw_headers = []
    for key, value in request_headers.items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    app = FastAPI()
    # When auth is disabled and mode is the default api_key, fall back to dev mode
    effective_auth_mode = auth_mode if auth_enabled or auth_mode != "api_key" else "dev"
    app.state.config = ServerConfig(auth_mode=effective_auth_mode, root_api_key=root_api_key)
    app.state.api_key_manager = (
        api_key_manager if auth_enabled and api_key_manager is not None else None
    )
    # Set auth plugin based on mode
    registry = get_registry()
    plugin_cls = registry.get(effective_auth_mode)
    if plugin_cls is not None:
        app.state.auth_plugin = plugin_cls()
        if effective_auth_mode == "trusted":
            app.state.auth_plugin._api_key_manager = api_key_manager
    scope = {
        "type": "http",
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "app": app,
    }
    return Request(scope)


def _build_auth_http_test_app(
    identity: ResolvedIdentity | None,
    auth_enabled: bool = True,
    auth_mode: str = "api_key",
    root_api_key: str | None = None,
) -> FastAPI:
    """Create a lightweight app that exercises auth dependency wiring.

    The full server fixture depends on AGFS native libraries. This helper keeps
    the test focused on request auth behavior and the structured HTTP error body.
    """
    # Ensure built-in plugins are registered
    import openviking.server.auth.plugins  # noqa: F401

    if auth_mode == "trusted" and root_api_key is None:
        root_api_key = ROOT_KEY

    app = FastAPI()
    # When auth is disabled and mode is the default api_key, fall back to dev mode
    effective_auth_mode = auth_mode if auth_enabled or auth_mode != "api_key" else "dev"
    app.state.config = ServerConfig(auth_mode=effective_auth_mode, root_api_key=root_api_key)
    app.state.api_key_manager = object() if auth_enabled else None
    # Set auth plugin based on mode
    registry = get_registry()
    plugin_cls = registry.get(effective_auth_mode)
    if plugin_cls is not None:
        app.state.auth_plugin = plugin_cls()

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

    if identity is not None:
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
        return {"status": "ok", "result": {"role": str(ctx.role)}}

    @app.post("/api/v1/system/wait")
    async def system_wait(ctx=Depends(get_request_context)):
        """Expose a non-tenant system route for auth regression tests."""
        return {"status": "ok", "result": {"role": str(ctx.role)}}

    @app.get("/api/v1/debug/vector/scroll")
    async def debug_vector_scroll(ctx=Depends(get_request_context)):
        """Expose a tenant-scoped debug route for auth regression tests."""
        return {"status": "ok", "result": {"role": str(ctx.role)}}

    @app.get("/api/v1/test/accounts/{account_id}/users/{user_id}")
    async def trusted_identity_from_url(
        account_id: str, user_id: str, ctx=Depends(get_request_context)
    ):
        """Expose a route whose explicit URL identity can satisfy trusted mode."""
        return {
            "status": "ok",
            "result": {
                "account_id": account_id,
                "user_id": user_id,
                "ctx_account_id": ctx.user.account_id,
                "ctx_user_id": ctx.user.user_id,
            },
        }

    return app


def _build_task_http_test_app(identity: ResolvedIdentity | None) -> FastAPI:
    """Build a lightweight app that mounts the real task router."""
    from openviking.server.routers import tasks as tasks_router

    app = _build_auth_http_test_app(identity=identity, auth_enabled=True, root_api_key=ROOT_KEY)
    app.include_router(tasks_router.router)
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
    """App with root_api_key configured and identity services initialized."""
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry

    config = ServerConfig(root_api_key=ROOT_KEY)
    app = create_app(config=config, service=auth_service)
    set_service(auth_service)

    manager = build_api_key_manager(ROOT_KEY, auth_service.viking_fs)
    await manager.load()
    app.state.api_key_manager = manager

    # Manually initialize auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    plugin_cls = registry.get("api_key")
    plugin = plugin_cls()
    app.state.auth_plugin = plugin

    yield app
    await manager.close()


async def _stored_users(service, account_id):
    manager = build_api_key_manager(ROOT_KEY, service.viking_fs)
    await manager.load()
    try:
        if await manager.get_account(account_id) is None:
            return []
        page = await manager.list_users_page(account_id, expose_key=True)
        return page["users"]
    finally:
        await manager.close()


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
    account_id = _uid()
    await manager.create_account(account_id, "test_admin")
    return await manager.register_user(account_id, "test_user")


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
    assert tenant_resp.status_code == 403
    assert tenant_resp.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_task_endpoints_require_auth():
    """Task endpoints must reject unauthenticated callers before lookup/filtering."""
    set_task_tracker(None)
    app = _build_task_http_test_app(identity=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for url in (
            "/api/v1/tasks",
            "/api/v1/tasks/nonexistent-id",
            "/api/v1/tasks/nonexistent-id?include_events=true",
        ):
            resp = await client.get(url)
            assert resp.status_code == 401
    set_task_tracker(None)


async def test_task_endpoints_are_user_scoped():
    """Authenticated callers must not see another user's background tasks."""
    set_task_tracker(None)
    _set_fake_task_tracker()
    account_id = _uid()
    tracker = get_task_tracker()
    alice_task = await tracker.create(
        "session_commit",
        resource_id="alice-session",
        account_id=account_id,
        user_id="alice",
    )
    bob_task = await tracker.create(
        "session_commit",
        resource_id="bob-session",
        account_id=account_id,
        user_id="bob",
    )

    alice_app = _build_task_http_test_app(
        ResolvedIdentity(role=Role.USER, account_id=account_id, user_id="alice")
    )
    bob_app = _build_task_http_test_app(
        ResolvedIdentity(role=Role.USER, account_id=account_id, user_id="bob")
    )
    alice_transport = httpx.ASGITransport(app=alice_app)
    bob_transport = httpx.ASGITransport(app=bob_app)

    async with httpx.AsyncClient(
        transport=alice_transport, base_url="http://testserver"
    ) as alice_client:
        alice_get = await alice_client.get(f"/api/v1/tasks/{alice_task.task_id}")
        assert alice_get.status_code == 200
        assert alice_get.json()["result"]["resource_id"] == "alice-session"
        assert "execution_events" not in alice_get.json()["result"]
        with_events = await alice_client.get(
            f"/api/v1/tasks/{alice_task.task_id}?include_events=true"
        )
        assert with_events.json()["result"]["execution_events"]["items"][0]["kind"] == "created"

        alice_list = await alice_client.get("/api/v1/tasks")
        assert alice_list.status_code == 200
        assert {task["task_id"] for task in alice_list.json()["result"]} == {alice_task.task_id}
        assert all("execution_events" not in task for task in alice_list.json()["result"])

    async with httpx.AsyncClient(
        transport=bob_transport, base_url="http://testserver"
    ) as bob_client:
        bob_get_other = await bob_client.get(f"/api/v1/tasks/{alice_task.task_id}")
        assert bob_get_other.status_code == 404
        bob_events = await bob_client.get(f"/api/v1/tasks/{alice_task.task_id}?include_events=true")
        assert bob_events.status_code == 404

        bob_list = await bob_client.get("/api/v1/tasks")
        assert bob_list.status_code == 200
        assert {task["task_id"] for task in bob_list.json()["result"]} == {bob_task.task_id}

    set_task_tracker(None)


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


@pytest.mark.parametrize("role", [Role.ADMIN, Role.USER])
async def test_api_key_identity_cannot_be_overridden(auth_app, role):
    manager = auth_app.state.api_key_manager
    account_id = _uid()
    admin_key = await manager.create_account(account_id, "admin_user")
    user_key = await manager.register_user(account_id, "alice")
    key, owner = (admin_key, "admin_user") if role == Role.ADMIN else (user_key, "alice")
    for overrides in (
        {},
        {"X-OpenViking-Account": "other-account"},
        {"X-OpenViking-Account": account_id, "X-OpenViking-User": "bob"},
    ):
        request = _make_request(
            "/api/v1/resources",
            headers={"X-API-Key": key, **overrides},
            auth_enabled=True,
            api_key_manager=manager,
        )
        identity = await resolve_identity(
            request,
            x_api_key=key,
            x_openviking_account=overrides.get("X-OpenViking-Account"),
            x_openviking_user=overrides.get("X-OpenViking-User"),
        )
        assert (identity.account_id, identity.user_id, identity.role) == (account_id, owner, role)
        assert "x-openviking-account" not in request.headers
        assert "x-openviking-user" not in request.headers


async def test_cross_tenant_session_get_returns_not_found(auth_client: httpx.AsyncClient, auth_app):
    """A user must not access another tenant's session by session_id."""
    manager = auth_app.state.api_key_manager
    alice_account_id = _uid()
    bob_account_id = _uid()
    await manager.create_account(alice_account_id, "alice_admin")
    await manager.create_account(bob_account_id, "bob_admin")
    alice_key = await manager.register_user(alice_account_id, "alice")
    bob_key = await manager.register_user(bob_account_id, "bob")

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


async def test_sessions_are_isolated_between_users_within_account(
    auth_client: httpx.AsyncClient, auth_app
):
    """Session access is user-scoped even within the same account."""
    manager = auth_app.state.api_key_manager
    account_id = _uid()
    admin_key = await manager.create_account(account_id, "admin_user")
    alice_key = await manager.register_user(account_id, "alice")
    bob_key = await manager.register_user(account_id, "bob")

    alice_create = await auth_client.post(
        "/api/v1/sessions",
        json={},
        headers={"X-API-Key": alice_key},
    )
    assert alice_create.status_code == 200
    alice_session = alice_create.json()["result"]["session_id"]

    bob_create = await auth_client.post(
        "/api/v1/sessions",
        json={},
        headers={"X-API-Key": bob_key},
    )
    assert bob_create.status_code == 200
    bob_session = bob_create.json()["result"]["session_id"]

    alice_list = await auth_client.get(
        "/api/v1/sessions",
        headers={"X-API-Key": alice_key},
    )
    assert alice_list.status_code == 200
    alice_ids = {item["session_id"] for item in alice_list.json()["result"]}
    assert alice_session in alice_ids
    assert bob_session not in alice_ids

    bob_list = await auth_client.get(
        "/api/v1/sessions",
        headers={"X-API-Key": bob_key},
    )
    assert bob_list.status_code == 200
    bob_ids = {item["session_id"] for item in bob_list.json()["result"]}
    assert bob_session in bob_ids
    assert alice_session not in bob_ids

    admin_list = await auth_client.get(
        "/api/v1/sessions",
        headers={"X-API-Key": admin_key},
    )
    assert admin_list.status_code == 200
    admin_ids = {item["session_id"] for item in admin_list.json()["result"]}
    assert alice_session not in admin_ids
    assert bob_session not in admin_ids

    bob_get_alice = await auth_client.get(
        f"/api/v1/sessions/{alice_session}",
        headers={"X-API-Key": bob_key},
    )
    assert bob_get_alice.status_code == 404

    bob_write_alice = await auth_client.post(
        f"/api/v1/sessions/{alice_session}/messages",
        json={"role": "user", "content": "bob writes same id in his own namespace"},
        headers={"X-API-Key": bob_key},
    )
    assert bob_write_alice.status_code == 200

    alice_get_after_bob_write = await auth_client.get(
        f"/api/v1/sessions/{alice_session}",
        headers={"X-API-Key": alice_key},
    )
    assert alice_get_after_bob_write.status_code == 200
    assert alice_get_after_bob_write.json()["result"]["message_count"] == 0

    bob_get_same_id = await auth_client.get(
        f"/api/v1/sessions/{alice_session}",
        headers={"X-API-Key": bob_key},
    )
    assert bob_get_same_id.status_code == 200
    assert bob_get_same_id.json()["result"]["message_count"] == 1


async def test_root_tenant_scoped_requests_rejected_in_api_key_mode():
    """ROOT API keys cannot access tenant-scoped data APIs in api_key mode."""
    request = _make_request("/api/v1/resources", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    with pytest.raises(PermissionDeniedError, match="ROOT API keys"):
        await get_request_context(request, identity)


async def test_root_system_status_allows_implicit_default_identity():
    """ROOT may call status endpoints without explicit tenant headers."""
    request = _make_request("/api/v1/system/status", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ROOT
    assert ctx.user.account_id == "default"
    assert ctx.user.user_id == "default"


async def test_root_tenant_scoped_requests_reject_explicit_identity_in_api_key_mode():
    """Header identity assertion belongs to trusted mode, not ROOT API key access."""
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": "acme",
            "X-OpenViking-User": "alice",
        },
        auth_enabled=True,
    )
    identity = ResolvedIdentity(role=Role.ROOT, account_id="acme", user_id="alice")

    with pytest.raises(PermissionDeniedError, match="ROOT API keys"):
        await get_request_context(request, identity)


async def test_root_reindex_requests_rejected_in_api_key_mode():
    """ROOT API keys cannot select tenant data through data-plane reindex."""
    request = _make_request("/api/v1/content/reindex", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    with pytest.raises(PermissionDeniedError, match="ROOT API keys"):
        await get_request_context(request, identity)


async def test_admin_reindex_requests_use_key_owner_in_api_key_mode():
    """ADMIN reindex is allowed for the admin key's own account only."""
    request = _make_request("/api/v1/content/reindex", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ADMIN, account_id="acme", user_id="admin")

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ADMIN
    assert ctx.user.account_id == "acme"
    assert ctx.user.user_id == "admin"


async def test_actor_peer_header_sets_request_context_scope():
    request = _make_request("/api/v1/search/find", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.USER, account_id="acme", user_id="alice")

    ctx = await get_request_context(request, identity, "web-visitor-alice")

    assert ctx.actor_peer_id == "web-visitor-alice"


async def test_empty_actor_peer_header_is_unset():
    request = _make_request("/api/v1/search/find", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.USER, account_id="acme", user_id="alice")

    ctx = await get_request_context(request, identity, "  ")

    assert ctx.actor_peer_id is None


async def test_actor_peer_header_rejects_path_separators():
    request = _make_request("/api/v1/search/find", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.USER, account_id="acme", user_id="alice")

    with pytest.raises(InvalidArgumentError, match="path separators"):
        await get_request_context(request, identity, "bad/peer")


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


async def test_root_debug_vector_requests_rejected_in_api_key_mode():
    """Tenant-scoped debug routes cannot use ROOT API keys in api_key mode."""
    request = _make_request("/api/v1/debug/vector/scroll", auth_enabled=True)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    with pytest.raises(PermissionDeniedError, match="ROOT API keys"):
        await get_request_context(request, identity)


async def test_dev_mode_root_tenant_scoped_requests_allow_implicit_identity():
    """Dev mode should keep the existing implicit ROOT/default behavior."""
    request = _make_request("/api/v1/resources", auth_enabled=False)
    identity = ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default")

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ROOT
    assert ctx.user.account_id == "default"
    assert ctx.user.user_id == "default"


async def test_root_tenant_scoped_requests_return_structured_403_via_http():
    """Tenant-scoped HTTP routes should reject ROOT API-key data-plane access."""
    app = _build_auth_http_test_app(
        ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default"),
        auth_enabled=True,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/fs/ls")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


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


async def test_root_debug_vector_requests_return_structured_403_via_http():
    """Tenant-scoped debug routes should reject ROOT API-key data-plane access."""
    app = _build_auth_http_test_app(
        ResolvedIdentity(role=Role.ROOT, account_id="default", user_id="default"),
        auth_enabled=True,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/debug/vector/scroll")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


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


async def test_trusted_mode_allows_header_identity_without_api_key():
    """Trusted mode should accept explicit tenant headers without API key."""
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": "acme",
            "X-OpenViking-User": "alice",
        },
        auth_enabled=False,
        auth_mode="trusted",
    )

    identity = await resolve_identity(
        request,
        x_openviking_account="acme",
        x_openviking_user="alice",
    )

    assert identity.role == Role.USER
    assert identity.account_id == "acme"
    assert identity.user_id == "alice"


async def test_trusted_mode_defaults_role_to_user():
    """Trusted mode should default requests to USER when no explicit role header is sent."""
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": "acme",
            "X-OpenViking-User": "alice",
        },
        auth_enabled=False,
        auth_mode="trusted",
    )

    identity = await resolve_identity(
        request,
        x_openviking_account="acme",
        x_openviking_user="alice",
    )

    assert identity.role == Role.USER


async def test_trusted_mode_looks_up_role_from_api_key_manager(auth_app):
    """Trusted mode should look up role from APIKeyManager using account_id and user_id."""
    manager = auth_app.state.api_key_manager
    account_id = _uid()
    await manager.create_account(account_id, "admin_user")
    await manager.register_user(account_id, "regular_user", "user")

    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": account_id,
            "X-OpenViking-User": "admin_user",
        },
        auth_enabled=True,
        auth_mode="trusted",
        api_key_manager=manager,
    )

    identity = await resolve_identity(
        request,
        x_openviking_account=account_id,
        x_openviking_user="admin_user",
    )

    assert identity.role == Role.ADMIN
    assert identity.account_id == account_id
    assert identity.user_id == "admin_user"

    # Test with regular user
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": account_id,
            "X-OpenViking-User": "regular_user",
        },
        auth_enabled=True,
        auth_mode="trusted",
        api_key_manager=manager,
    )

    identity = await resolve_identity(
        request,
        x_openviking_account=account_id,
        x_openviking_user="regular_user",
    )

    assert identity.role == Role.USER
    assert identity.account_id == account_id
    assert identity.user_id == "regular_user"


async def test_trusted_mode_defaults_to_user_when_user_not_found(auth_app):
    """Trusted mode should default to USER role when user doesn't exist in APIKeyManager."""
    manager = auth_app.state.api_key_manager
    account_id = _uid()
    await manager.create_account(account_id, "admin_user")

    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": account_id,
            "X-OpenViking-User": "nonexistent_user",
        },
        auth_enabled=True,
        auth_mode="trusted",
        api_key_manager=manager,
    )

    identity = await resolve_identity(
        request,
        x_openviking_account=account_id,
        x_openviking_user="nonexistent_user",
    )

    assert identity.role == Role.USER
    assert identity.account_id == account_id
    assert identity.user_id == "nonexistent_user"


async def test_trusted_mode_defaults_to_user_when_account_not_found(auth_app):
    """Trusted mode should default to USER role when account doesn't exist in APIKeyManager."""
    manager = auth_app.state.api_key_manager
    account_id = _uid()

    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-OpenViking-Account": account_id,
            "X-OpenViking-User": "some_user",
        },
        auth_enabled=False,
        auth_mode="trusted",
        api_key_manager=manager,
    )

    identity = await resolve_identity(
        request,
        x_openviking_account=account_id,
        x_openviking_user="some_user",
    )

    assert identity.role == Role.USER
    assert identity.account_id == account_id
    assert identity.user_id == "some_user"


async def test_trusted_identity_registration_batches_data_plane_identity(auth_service):
    """Registration is delayed until flush and remains idempotent after restart."""
    from openviking.server.auth.plugins import TrustedAuthPlugin

    config = ServerConfig(
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
        trusted_identity_flush_interval_seconds=300,
    )
    for restarted in (False, True):
        app = FastAPI()
        app.state.config = config
        app.state.service_name = "openviking-server"
        plugin = TrustedAuthPlugin()
        await plugin.initialize(app, auth_service, config)
        try:
            request = _make_request("/api/v1/resources", auth_enabled=False, auth_mode="trusted")
            request.app.state.config = config
            request.app.state.api_key_manager = app.state.api_key_manager
            identity = await plugin.resolve_identity(
                request, x_openviking_account="acme", x_openviking_user="alice"
            )
            assert (identity.account_id, identity.user_id, identity.role) == (
                "acme",
                "alice",
                Role.USER,
            )
            if not restarted:
                assert await _stored_users(auth_service, "acme") == []
            await plugin.flush_trusted_identities()
            assert await _stored_users(auth_service, "acme") == [
                {"user_id": "alice", "role": "user"}
            ]
        finally:
            await plugin.shutdown()


async def test_invalid_trusted_identity_does_not_block_later_registration(
    auth_service,
):
    """An invalid trusted identity must not poison a later valid registration batch."""
    from openviking.server.auth.plugins import TrustedAuthPlugin

    config = ServerConfig(
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
        trusted_identity_flush_interval_seconds=300,
    )
    app = FastAPI()
    app.state.config = config
    app.state.service_name = "openviking-server"
    plugin = TrustedAuthPlugin()
    await plugin.initialize(app, auth_service, config)
    try:
        request = _make_request(
            "/api/v1/resources",
            headers={
                "X-OpenViking-Account": "acme",
                "X-OpenViking-User": "invalid/user",
            },
            auth_enabled=False,
            auth_mode="trusted",
        )
        request.app.state.config = config

        with pytest.raises(InvalidArgumentError, match="user_id"):
            await plugin.resolve_identity(
                request,
                x_openviking_account="acme",
                x_openviking_user="invalid/user",
            )

        valid_request = _make_request(
            "/api/v1/resources",
            headers={
                "X-OpenViking-Account": "good-account",
                "X-OpenViking-User": "alice",
            },
            auth_enabled=False,
            auth_mode="trusted",
        )
        valid_request.app.state.config = config
        await plugin.resolve_identity(
            valid_request,
            x_openviking_account="good-account",
            x_openviking_user="alice",
        )
        await plugin.flush_trusted_identities()

        assert await _stored_users(auth_service, "good-account") == [
            {"user_id": "alice", "role": "user"}
        ]
    finally:
        await plugin.shutdown()


async def test_trusted_identity_registration_skips_identity_loaded_from_registry(auth_service):
    """A restarted plugin must not queue an identity already loaded from storage."""
    from openviking.server.auth.plugins import TrustedAuthPlugin

    config = ServerConfig(auth_mode="trusted", root_api_key=ROOT_KEY)
    app = FastAPI()
    app.state.config = config
    app.state.service_name = "openviking-server"
    first_plugin = TrustedAuthPlugin()
    await first_plugin.initialize(app, auth_service, config)
    try:
        await first_plugin._api_key_manager.ensure_trusted_identities({"acme": {"alice"}})
    finally:
        await first_plugin.shutdown()

    restarted_app = FastAPI()
    restarted_app.state.config = config
    restarted_app.state.service_name = "openviking-server"
    restarted_plugin = TrustedAuthPlugin()
    await restarted_plugin.initialize(restarted_app, auth_service, config)
    try:
        request = _make_request(
            "/api/v1/resources",
            headers={
                "X-OpenViking-Account": "acme",
                "X-OpenViking-User": "alice",
            },
            auth_enabled=False,
            auth_mode="trusted",
        )
        request.app.state.config = config

        await restarted_plugin.resolve_identity(
            request, x_openviking_account="acme", x_openviking_user="alice"
        )

        assert restarted_plugin._pending == {}
    finally:
        await restarted_plugin.shutdown()


@pytest.mark.parametrize(
    "interval,path",
    [(0, "/api/v1/resources"), (0, "/api/v1/admin/accounts"), (300, "/api/v1/admin/accounts")],
)
async def test_trusted_registration_respects_configuration_and_route(auth_service, interval, path):
    """A zero interval disables registration, and Admin paths never register."""
    from openviking.server.auth.plugins import TrustedAuthPlugin

    config = ServerConfig(
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
        trusted_identity_flush_interval_seconds=interval,
    )
    app = FastAPI()
    app.state.config = config
    app.state.service_name = "openviking-server"
    plugin = TrustedAuthPlugin()
    await plugin.initialize(app, auth_service, config)
    try:
        request = _make_request(
            path,
            headers={
                "X-OpenViking-Account": "acme",
                "X-OpenViking-User": "alice",
            },
            auth_enabled=False,
            auth_mode="trusted",
        )
        request.app.state.config = config
        request.app.state.api_key_manager = app.state.api_key_manager

        await plugin.resolve_identity(
            request,
            x_openviking_account="acme",
            x_openviking_user="alice",
        )

        await plugin.flush_trusted_identities()
        assert await _stored_users(auth_service, "acme") == []
    finally:
        await plugin.shutdown()


async def test_trusted_identity_registration_retries_failed_batch_without_exceeding_backlog(
    auth_service, monkeypatch: pytest.MonkeyPatch
):
    """A failed flush returns its batch to the bounded queue for a later retry."""
    from openviking.server.auth.plugins import TrustedAuthPlugin

    config = ServerConfig(
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
        trusted_identity_pending_max_size=2,
    )
    app = FastAPI()
    app.state.config = config
    app.state.service_name = "openviking-server"
    plugin = TrustedAuthPlugin()
    await plugin.initialize(app, auth_service, config)
    try:
        for user_id in ("alice", "bob", "eve"):
            request = _make_request(
                "/api/v1/resources",
                headers={
                    "X-OpenViking-Account": "acme",
                    "X-OpenViking-User": user_id,
                },
                auth_enabled=False,
                auth_mode="trusted",
            )
            request.app.state.config = config
            await plugin.resolve_identity(
                request,
                x_openviking_account="acme",
                x_openviking_user=user_id,
            )

        original_ensure = APIKeyManager.ensure_trusted_identities
        failed = False

        async def _fail_once(manager, identities):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("temporary storage failure")
            return await original_ensure(manager, identities)

        monkeypatch.setattr(APIKeyManager, "ensure_trusted_identities", _fail_once)
        await plugin.flush_trusted_identities()

        await plugin.flush_trusted_identities()
        assert await _stored_users(auth_service, "acme") == [
            {"user_id": "alice", "role": "user"},
            {"user_id": "bob", "role": "user"},
        ]
    finally:
        await plugin.shutdown()


def test_trusted_identity_registration_config_allows_disabling():
    """A zero interval disables registration; negative values remain invalid."""
    assert (
        ServerConfig(
            trusted_identity_flush_interval_seconds=0
        ).trusted_identity_flush_interval_seconds
        == 0
    )
    with pytest.raises(ValueError):
        ServerConfig(trusted_identity_flush_interval_seconds=-1)
    with pytest.raises(ValueError):
        ServerConfig(trusted_identity_pending_max_size=0)


async def test_trusted_mode_with_root_api_key_requires_matching_api_key():
    """Trusted mode should require the configured server API key when present."""
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-API-Key": "",
            "X-OpenViking-Account": "acme",
            "X-OpenViking-User": "alice",
        },
        auth_enabled=False,
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
    )

    with pytest.raises(OpenVikingError, match="Missing API Key"):
        await resolve_identity(
            request,
            x_openviking_account="acme",
            x_openviking_user="alice",
        )


async def test_trusted_mode_with_root_api_key_accepts_matching_api_key():
    """Trusted mode should accept explicit identity headers plus the configured server API key."""
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-API-Key": ROOT_KEY,
            "X-OpenViking-Account": "acme",
            "X-OpenViking-User": "alice",
        },
        auth_enabled=False,
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
    )

    identity = await resolve_identity(
        request,
        x_api_key=ROOT_KEY,
        x_openviking_account="acme",
        x_openviking_user="alice",
    )

    assert identity.role == Role.USER
    assert identity.account_id == "acme"
    assert identity.user_id == "alice"


async def test_trusted_mode_root_key_allows_admin_role_header(auth_app):
    """Trusted upstreams with the root key may assert ADMIN role for tenant routes."""
    manager = auth_app.state.api_key_manager
    account_id = _uid()
    await manager.create_account(account_id, "owner")
    await manager.register_user(account_id, "alice", "user")

    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-API-Key": ROOT_KEY,
            "X-OpenViking-Account": account_id,
            "X-OpenViking-User": "alice",
            "X-OpenViking-Role": " ADMIN ",
        },
        auth_enabled=True,
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
        api_key_manager=manager,
    )

    identity = await resolve_identity(
        request,
        x_api_key=ROOT_KEY,
        x_openviking_account=account_id,
        x_openviking_user="alice",
    )

    assert identity.role == Role.ADMIN
    assert identity.account_id == account_id
    assert identity.user_id == "alice"


async def test_trusted_mode_rejects_root_role_header():
    """Trusted role header should not allow asserting ROOT."""
    request = _make_request(
        "/api/v1/resources",
        headers={
            "X-API-Key": ROOT_KEY,
            "X-OpenViking-Account": "acme",
            "X-OpenViking-User": "alice",
            "X-OpenViking-Role": "root",
        },
        auth_enabled=False,
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
    )

    with pytest.raises(InvalidArgumentError, match="X-OpenViking-Role"):
        await resolve_identity(
            request,
            x_api_key=ROOT_KEY,
            x_openviking_account="acme",
            x_openviking_user="alice",
        )


async def test_trusted_mode_tenant_http_routes_require_explicit_identity_headers():
    """Trusted mode should reject tenant-scoped routes without account/user headers."""
    app = _build_auth_http_test_app(
        identity=None,
        auth_enabled=False,
        auth_mode="trusted",
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/fs/ls", headers={"X-API-Key": ROOT_KEY})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_trusted_mode_tenant_http_routes_accept_explicit_identity_headers():
    """Trusted mode should allow tenant-scoped routes with account/user headers."""
    app = _build_auth_http_test_app(
        identity=None,
        auth_enabled=False,
        auth_mode="trusted",
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/fs/ls",
            headers={
                "X-API-Key": ROOT_KEY,
                "X-OpenViking-Account": "acme",
                "X-OpenViking-User": "alice",
            },
        )

    assert response.status_code == 200
    assert response.json()["result"] == {"account_id": "acme", "user_id": "alice"}


async def test_trusted_mode_http_routes_accept_explicit_identity_from_url():
    """Trusted mode should accept account_id/user_id supplied directly in the URL."""
    app = _build_auth_http_test_app(
        identity=None,
        auth_enabled=False,
        auth_mode="trusted",
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/test/accounts/acme/users/alice",
            headers={"X-API-Key": ROOT_KEY},
        )

    assert response.status_code == 200
    assert response.json()["result"] == {
        "account_id": "acme",
        "user_id": "alice",
        "ctx_account_id": "acme",
        "ctx_user_id": "alice",
    }


async def test_trusted_mode_rejects_conflicting_header_and_url_identity():
    """Trusted mode should reject requests when explicit URL identity conflicts with headers."""
    app = _build_auth_http_test_app(
        identity=None,
        auth_enabled=False,
        auth_mode="trusted",
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/test/accounts/acme/users/alice",
            headers={
                "X-API-Key": ROOT_KEY,
                "X-OpenViking-Account": "other-acct",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_trusted_mode_http_routes_require_api_key_when_root_key_configured():
    """Trusted mode HTTP routes should require the configured server API key when present."""
    app = _build_auth_http_test_app(
        identity=None,
        auth_enabled=False,
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/fs/ls",
            headers={
                "X-OpenViking-Account": "acme",
                "X-OpenViking-User": "alice",
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_trusted_mode_http_routes_accept_api_key_when_root_key_configured():
    """Trusted mode HTTP routes should accept the configured server API key plus explicit identity headers."""
    app = _build_auth_http_test_app(
        identity=None,
        auth_enabled=False,
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/fs/ls",
            headers={
                "X-API-Key": ROOT_KEY,
                "X-OpenViking-Account": "acme",
                "X-OpenViking-User": "alice",
            },
        )

    assert response.status_code == 200
    assert response.json()["result"] == {"account_id": "acme", "user_id": "alice"}


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


def test_validate_trusted_mode_requires_root_key_on_every_host():
    """Trusted mode without root_api_key should fail on every host."""
    for host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        config = ServerConfig(host=host, root_api_key=None, auth_mode="trusted")
        with pytest.raises(SystemExit):
            validate_server_config(config)


async def test_trusted_mode_admin_api_without_identity_defaults_to_root():
    """Trusted mode admin APIs without identity should default to ROOT role."""
    request = _make_request(
        "/api/v1/admin/accounts",
        headers={},
        auth_enabled=False,
        auth_mode="trusted",
    )

    identity = await resolve_identity(request)

    assert identity.role == Role.ROOT
    assert identity.account_id == "trusted"
    assert identity.user_id == "trusted"


async def test_trusted_mode_admin_api_treats_partial_identity_as_target():
    """Trusted Admin paths treat account/user values as resource targets."""
    request = _make_request(
        "/api/v1/admin/accounts",
        headers={"X-OpenViking-Account": "acme"},
        auth_enabled=False,
        auth_mode="trusted",
    )

    identity = await resolve_identity(request, x_openviking_account="acme")

    assert identity.role == Role.ROOT
    assert identity.account_id == "acme"
    assert identity.user_id == "trusted"


async def test_trusted_mode_root_key_can_target_account_without_caller_identity():
    """A verified Root key should not treat a target account as partial caller identity."""
    request = _make_request(
        "/api/v1/admin/accounts/acme/users",
        headers={"X-API-Key": ROOT_KEY},
        auth_mode="trusted",
        root_api_key=ROOT_KEY,
    )
    request.scope["path_params"] = {"account_id": "acme"}

    identity = await resolve_identity(request, x_api_key=ROOT_KEY)

    assert identity.role == Role.ROOT
    assert identity.account_id == "acme"
    assert identity.user_id == "trusted"


async def test_trusted_mode_get_request_context_exempts_admin_paths():
    """get_request_context should exempt admin paths from identity checks in trusted mode."""
    # Admin path with ROOT identity from default
    request = _make_request(
        "/api/v1/admin/accounts",
        auth_enabled=False,
        auth_mode="trusted",
    )
    identity = ResolvedIdentity(
        role=Role.ROOT,
        account_id="trusted",
        user_id="trusted",
    )

    ctx = await get_request_context(request, identity)

    assert ctx.role == Role.ROOT
    assert ctx.user.account_id == "trusted"
    assert ctx.user.user_id == "trusted"

    # Non-admin path still requires proper identity
    non_admin_request = _make_request(
        "/api/v1/fs/ls",
        auth_enabled=False,
        auth_mode="trusted",
    )
    incomplete_identity = ResolvedIdentity(
        role=Role.ROOT,
        account_id=None,
        user_id="trusted",
    )

    with pytest.raises(InvalidArgumentError, match="X-OpenViking-Account"):
        await get_request_context(non_admin_request, incomplete_identity)


async def test_trusted_mode_admin_api_http_route_without_identity():
    """Trusted mode admin HTTP routes should work without identity headers."""
    app = _build_auth_http_test_app(
        identity=None,
        auth_enabled=False,
        auth_mode="trusted",
    )

    # Add an admin route to the test app
    @app.get("/api/v1/admin/accounts")
    async def admin_accounts(ctx=Depends(get_request_context)):
        return {
            "status": "ok",
            "result": {
                "role": str(ctx.role),
                "account_id": ctx.user.account_id,
                "user_id": ctx.user.user_id,
            },
        }

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/admin/accounts",
            headers={"X-API-Key": ROOT_KEY},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["result"]["role"] == "root"
    assert response.json()["result"]["account_id"] == "trusted"
    assert response.json()["result"]["user_id"] == "trusted"
