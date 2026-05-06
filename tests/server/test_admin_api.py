# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for Admin API endpoints (openviking/server/routers/admin.py)."""

import uuid

import httpx
import pytest_asyncio
from fastapi import FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse

from openviking.server.api_keys import APIKeyManager
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.service.core import OpenVikingService
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier


def _uid() -> str:
    return f"acme_{uuid.uuid4().hex[:8]}"


ROOT_KEY = "admin-api-test-root-key-abcdef1234567890ab"


class _FakeAGFS:
    def __init__(self):
        self._files = {}
        self._dirs = {"/", "/local"}

    def read(self, path):
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def write(self, path, content):
        self._files[path] = content

    def mkdir(self, path):
        self._dirs.add(path)


class _FakeVikingFS:
    def __init__(self):
        self.agfs = _FakeAGFS()

    async def encrypt_bytes(self, account_id, content):
        return content

    async def decrypt_bytes(self, account_id, content):
        return content


class _FakeService:
    def __init__(self):
        self.viking_fs = _FakeVikingFS()

    async def initialize_account_directories(self, ctx):
        return None

    async def initialize_user_directories(self, ctx):
        return None


def _build_lightweight_admin_test_app() -> FastAPI:
    from openviking.server.routers import admin as admin_router

    app = FastAPI()
    app.state.config = ServerConfig(root_api_key=ROOT_KEY)
    fake_service = _FakeService()
    set_service(fake_service)

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(request: FastAPIRequest, exc: OpenVikingError):
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(code=exc.code, message=exc.message, details=exc.details),
            ).model_dump(),
        )

    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=fake_service.viking_fs)
    app.state.api_key_manager = manager
    app.include_router(admin_router.router)
    return app


@pytest_asyncio.fixture(scope="function")
async def lightweight_admin_app():
    app = _build_lightweight_admin_test_app()
    await app.state.api_key_manager.load()
    return app


@pytest_asyncio.fixture(scope="function")
async def lightweight_admin_client(lightweight_admin_app):
    transport = httpx.ASGITransport(app=lightweight_admin_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture(scope="function")
async def admin_service(temp_dir):
    svc = OpenVikingService(
        path=str(temp_dir / "admin_data"), user=UserIdentifier.the_default_user("admin_user")
    )
    await svc.initialize()
    yield svc
    await svc.close()


@pytest_asyncio.fixture(scope="function")
async def admin_app(admin_service):
    config = ServerConfig(root_api_key=ROOT_KEY)
    app = create_app(config=config, service=admin_service)
    set_service(admin_service)

    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=admin_service.viking_fs)
    await manager.load()
    app.state.api_key_manager = manager

    return app


@pytest_asyncio.fixture(scope="function")
async def admin_client(admin_app):
    transport = httpx.ASGITransport(app=admin_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def root_headers():
    return {"X-API-Key": ROOT_KEY}


def trusted_headers(
    *,
    account: str,
    user: str,
    include_api_key: bool = False,
):
    headers = {
        "X-OpenViking-Account": account,
        "X-OpenViking-User": user,
    }
    if include_api_key:
        headers["X-API-Key"] = ROOT_KEY
    return headers


async def create_agent_namespace(service: OpenVikingService, account_id: str, agent_id: str):
    ctx = RequestContext(
        user=UserIdentifier(account_id, "system", agent_id),
        role=Role.ROOT,
    )
    await service.viking_fs.mkdir(f"viking://agent/{agent_id}", ctx=ctx, exist_ok=True)


# ---- Account CRUD ----


async def test_create_account(admin_client: httpx.AsyncClient):
    """ROOT can create an account with first admin."""
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["account_id"] == acct
    assert body["result"]["admin_user_id"] == "alice"
    assert "user_key" in body["result"]


async def test_list_accounts(admin_client: httpx.AsyncClient):
    """ROOT can list all accounts."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.get("/api/v1/admin/accounts", headers=root_headers())
    assert resp.status_code == 200
    accounts = resp.json()["result"]
    account_ids = {a["account_id"] for a in accounts}
    assert "default" in account_ids
    assert acct in account_ids


async def test_delete_account(admin_client: httpx.AsyncClient):
    """ROOT can delete an account."""
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    user_key = resp.json()["result"]["user_key"]

    resp = await admin_client.delete(f"/api/v1/admin/accounts/{acct}", headers=root_headers())
    assert resp.status_code == 200
    assert resp.json()["result"]["deleted"] is True

    # User key should now be invalid
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": user_key},
    )
    assert resp.status_code == 401


async def test_create_duplicate_account_fails(admin_client: httpx.AsyncClient):
    """Creating duplicate account should fail."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "bob"},
        headers=root_headers(),
    )
    assert resp.status_code == 409  # ALREADY_EXISTS


# ---- User CRUD ----


async def test_register_user(admin_client: httpx.AsyncClient):
    """ROOT can register a user in an account."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["user_id"] == "bob"
    assert "user_key" in body["result"]

    # Bob's key should work
    bob_key = body["result"]["user_key"]
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 200


async def test_root_can_register_admin_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ROOT can create an ADMIN user via register_user."""
    acct = _uid()
    await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob-admin", "role": "admin"},
        headers=root_headers(),
    )
    assert resp.status_code == 200

    admin_key = resp.json()["result"]["user_key"]
    list_users = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={"X-API-Key": admin_key},
    )
    assert list_users.status_code == 200


async def test_root_cannot_register_root_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ROOT must use set_role instead of minting ROOT directly in register_user."""
    acct = _uid()
    await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-root", "role": "root"},
        headers=root_headers(),
    )
    assert resp.status_code == 403


async def test_admin_can_register_user_in_own_account(admin_client: httpx.AsyncClient):
    """ADMIN can register users in their own account."""
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 200


async def test_admin_can_register_admin_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ADMIN can create another ADMIN in the same account via register_user."""
    acct = _uid()
    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-admin", "role": "admin"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 200

    admin_key = resp.json()["result"]["user_key"]
    list_users = await lightweight_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={"X-API-Key": admin_key},
    )
    assert list_users.status_code == 200


async def test_admin_cannot_register_root_role_user(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ADMIN should not be able to mint a ROOT key via register_user."""
    acct = _uid()
    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-root", "role": "root"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 403


async def test_admin_cannot_mint_root_key_that_reaches_root_only_endpoint(
    lightweight_admin_client: httpx.AsyncClient,
):
    """ADMIN registration must never yield a key that works on ROOT-only endpoints."""
    acct = _uid()
    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "mallory-root", "role": "root"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 403
    result = resp.json().get("result") or {}
    assert "user_key" not in result

    mallory_key = result.get("user_key")
    if mallory_key:
        root_only = await lightweight_admin_client.get(
            "/api/v1/admin/accounts",
            headers={"X-API-Key": mallory_key},
        )
        assert root_only.status_code == 403


async def test_admin_cannot_register_user_in_other_account(admin_client: httpx.AsyncClient):
    """ADMIN cannot register users in another account."""
    acct = _uid()
    other = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]

    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other, "admin_user_id": "eve"},
        headers=root_headers(),
    )

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{other}/users",
        json={"user_id": "bob", "role": "user"},
        headers={"X-API-Key": alice_key},
    )
    assert resp.status_code == 403


async def test_list_users(admin_client: httpx.AsyncClient):
    """ROOT can list users in an account."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    resp = await admin_client.get(f"/api/v1/admin/accounts/{acct}/users", headers=root_headers())
    assert resp.status_code == 200
    users = resp.json()["result"]
    user_ids = {u["user_id"] for u in users}
    assert user_ids == {"alice", "bob"}


async def test_list_agents(admin_client: httpx.AsyncClient, admin_service: OpenVikingService):
    """ROOT can list agent namespaces in an account."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    await create_agent_namespace(admin_service, acct, "research")
    await create_agent_namespace(admin_service, acct, "writer")

    resp = await admin_client.get(f"/api/v1/admin/accounts/{acct}/agents", headers=root_headers())

    assert resp.status_code == 200
    assert resp.json()["result"] == [
        {"agent_id": "default", "uri": "viking://agent/default"},
        {"agent_id": "research", "uri": "viking://agent/research"},
        {"agent_id": "writer", "uri": "viking://agent/writer"},
    ]


async def test_list_agents_returns_default_for_new_account(
    admin_client: httpx.AsyncClient,
):
    """New accounts should expose the initialized default agent namespace."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )

    resp = await admin_client.get(f"/api/v1/admin/accounts/{acct}/agents", headers=root_headers())

    assert resp.status_code == 200
    assert resp.json()["result"] == [
        {"agent_id": "default", "uri": "viking://agent/default"},
    ]


async def test_admin_can_list_agents_in_own_account(
    admin_client: httpx.AsyncClient,
    admin_service: OpenVikingService,
):
    """ADMIN can list agent namespaces in their own account."""
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]
    await create_agent_namespace(admin_service, acct, "assistant")

    resp = await admin_client.get(
        f"/api/v1/admin/accounts/{acct}/agents",
        headers={"X-API-Key": alice_key},
    )

    assert resp.status_code == 200
    assert resp.json()["result"] == [
        {"agent_id": "assistant", "uri": "viking://agent/assistant"},
        {"agent_id": "default", "uri": "viking://agent/default"},
    ]


async def test_admin_cannot_list_agents_in_other_account(
    admin_client: httpx.AsyncClient,
    admin_service: OpenVikingService,
):
    """ADMIN cannot list agent namespaces in another account."""
    acct = _uid()
    other = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    alice_key = resp.json()["result"]["user_key"]
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other, "admin_user_id": "eve"},
        headers=root_headers(),
    )
    await create_agent_namespace(admin_service, other, "foreign")

    resp = await admin_client.get(
        f"/api/v1/admin/accounts/{other}/agents",
        headers={"X-API-Key": alice_key},
    )

    assert resp.status_code == 403


async def test_list_agents_unknown_account_returns_404(admin_client: httpx.AsyncClient):
    """Unknown accounts should use the same 404 behavior as other admin account APIs."""
    resp = await admin_client.get(
        f"/api/v1/admin/accounts/{_uid()}/agents",
        headers=root_headers(),
    )

    assert resp.status_code == 404


async def test_remove_user(admin_client: httpx.AsyncClient):
    """ROOT can remove a user."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = resp.json()["result"]["user_key"]

    resp = await admin_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=root_headers()
    )
    assert resp.status_code == 200

    # Bob's key should be invalid now
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 401


# ---- Role management ----


async def test_set_role(admin_client: httpx.AsyncClient):
    """ROOT can change a user's role."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    resp = await admin_client.put(
        f"/api/v1/admin/accounts/{acct}/users/bob/role",
        json={"role": "admin"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["role"] == "admin"


async def test_regenerate_key(admin_client: httpx.AsyncClient):
    """ROOT can regenerate a user's key."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    old_key = resp.json()["result"]["user_key"]

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users/bob/key",
        headers=root_headers(),
    )
    assert resp.status_code == 200
    new_key = resp.json()["result"]["user_key"]
    assert new_key != old_key

    # Old key invalid
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": old_key},
    )
    assert resp.status_code == 401

    # New key valid
    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": new_key},
    )
    assert resp.status_code == 200


# ---- Permission guard ----


async def test_user_role_cannot_access_admin_api(admin_client: httpx.AsyncClient):
    """USER role should not access admin endpoints."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = resp.json()["result"]["user_key"]

    # USER cannot register users
    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "charlie", "role": "user"},
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 403


async def test_no_auth_admin_api_returns_401(admin_client: httpx.AsyncClient):
    """Admin API without key should return 401."""
    resp = await admin_client.get("/api/v1/admin/accounts")
    assert resp.status_code == 401


@pytest_asyncio.fixture(scope="function")
async def trusted_admin_app(admin_service):
    config = ServerConfig(auth_mode="trusted", root_api_key=ROOT_KEY)
    app = create_app(config=config, service=admin_service)
    set_service(admin_service)
    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=admin_service.viking_fs)
    await manager.load()
    # Create test users for trusted mode tests if they don't exist
    if "platform" not in manager._accounts:
        await manager.create_account("platform", "gateway-admin")
    app.state.api_key_manager = manager
    return app


@pytest_asyncio.fixture(scope="function")
async def trusted_admin_client(trusted_admin_app):
    transport = httpx.ASGITransport(app=trusted_admin_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_trusted_mode_root_can_create_account(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ROOT requests should be able to create accounts."""
    # Set gateway-admin to ROOT role
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
            "isolate_user_scope_by_agent": True,
            "isolate_agent_scope_by_user": True,
        },
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["account_id"] == acct
    assert body["result"]["admin_user_id"] == "alice"
    assert body["result"]["isolate_user_scope_by_agent"] is True
    assert body["result"]["isolate_agent_scope_by_user"] is True
    assert "user_key" not in body["result"]


async def test_trusted_mode_admin_can_register_user_in_own_account(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ADMIN requests should be able to manage users in their own account."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    resp = await trusted_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=trusted_headers(
            account=acct,
            user="alice",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["account_id"] == acct
    assert resp.json()["result"]["user_id"] == "bob"
    assert "user_key" not in resp.json()["result"]


async def test_trusted_mode_admin_can_list_users_with_account_only_in_url(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ADMIN requests may omit X-OpenViking-Account when the URL already provides it."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    resp = await trusted_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={
            "X-API-Key": ROOT_KEY,
            "X-OpenViking-User": "alice",
            "X-OpenViking-Account": acct,
        },
    )
    assert resp.status_code == 200
    assert any(user["user_id"] == "alice" for user in resp.json()["result"])


async def test_trusted_mode_admin_can_list_users_without_account_or_user_headers(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted admin routes may omit caller account/user when the route itself identifies the target."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    resp = await trusted_admin_client.get(
        f"/api/v1/admin/accounts/{acct}/users",
        headers={
            "X-API-Key": ROOT_KEY,
            "X-OpenViking-Account": acct,
            "X-OpenViking-User": "alice",
        },
    )
    assert resp.status_code == 200
    assert any(user["user_id"] == "alice" for user in resp.json()["result"])


async def test_trusted_mode_admin_cannot_register_user_in_other_account(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted ADMIN requests should reject conflicting account identity."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    other = _uid()
    for account_id, admin_user_id in ((acct, "alice"), (other, "eve")):
        create_resp = await trusted_admin_client.post(
            "/api/v1/admin/accounts",
            json={"account_id": account_id, "admin_user_id": admin_user_id},
            headers=trusted_headers(
                account="platform",
                user="gateway-admin",
                include_api_key=True,
            ),
        )
        assert create_resp.status_code == 200

    resp = await trusted_admin_client.post(
        f"/api/v1/admin/accounts/{other}/users",
        json={"user_id": "bob", "role": "user"},
        headers=trusted_headers(
            account=acct,
            user="alice",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_trusted_mode_user_cannot_call_admin_api(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted USER requests should still be denied by Admin API role checks."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    create_resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert create_resp.status_code == 200

    # Change alice to USER role
    await manager.set_role(acct, "alice", "user")

    resp = await trusted_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=trusted_headers(
            account=acct,
            user="alice",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 403


async def test_trusted_mode_requires_matching_api_key_for_admin_api(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted admin requests should require the configured server API key when present."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": _uid(), "admin_user_id": "alice"},
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=False,
        ),
    )
    assert resp.status_code == 401


async def test_trusted_mode_create_account_persists_namespace_policy(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted account creation should persist namespace policy for later requests."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
            "isolate_user_scope_by_agent": True,
            "isolate_agent_scope_by_user": False,
        },
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200

    manager = trusted_admin_app.state.api_key_manager
    assert manager.get_account_policy(acct).isolate_user_scope_by_agent is True
    assert manager.get_account_policy(acct).isolate_agent_scope_by_user is False
