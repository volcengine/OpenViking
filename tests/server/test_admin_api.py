# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for Admin API endpoints (openviking/server/routers/admin.py)."""

import uuid

import httpx
import pytest_asyncio

from openviking.server.api_keys import APIKeyManager
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier


def _uid() -> str:
    return f"acme_{uuid.uuid4().hex[:8]}"


ROOT_KEY = "admin-api-test-root-key-abcdef1234567890ab"


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
