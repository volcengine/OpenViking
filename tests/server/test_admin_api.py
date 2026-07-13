# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for Admin API endpoints (openviking/server/routers/admin.py)."""

import asyncio
import hashlib
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import JSONResponse

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.api_keys import APIKeyManager
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.user_config import read_user_add_targets
from openviking.service.core import OpenVikingService
from openviking.service.task_store import (
    SYSTEM_TASK_ACCOUNT_ID,
    SYSTEM_TASK_USER_ID,
)
from openviking_cli.exceptions import OpenVikingError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def _uid() -> str:
    return f"acme_{uuid.uuid4().hex[:8]}"


def _seed_secret(user_id: str, seed: str) -> str:
    return hashlib.sha256(f"{user_id}\0{seed}".encode("utf-8")).hexdigest()


ROOT_KEY = "admin-api-test-root-key-abcdef1234567890ab"


class _FakeAGFS:
    def __init__(self):
        self._files = {}
        self._dirs = {"/", "/local"}

    def read(self, path, **_kwargs):
        if path not in self._files:
            raise AGFSNotFoundError(path)
        return self._files[path]

    def write(self, path, content, **_kwargs):
        self.ensure_parent_dirs(path)
        self._files[path] = content

    def mkdir(self, path, **_kwargs):
        self._dirs.add(path)

    def ensure_parent_dirs(self, path, **_kwargs):
        parent = path.rsplit("/", 1)[0]
        if not parent:
            return
        current = ""
        for part in [part for part in parent.strip("/").split("/") if part]:
            current = f"{current}/{part}" if current else f"/{part}"
            self._dirs.add(current)


class _FakeVikingFS:
    def __init__(self):
        self.agfs = _FakeAGFS()
        self.files = {}

    async def read_file(self, uri, **_kwargs):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri, content, **_kwargs):
        self.files[uri] = content

    async def rm(self, uri, **_kwargs):
        self.files.pop(uri, None)


class _FakeService:
    def __init__(self):
        self.viking_fs = _FakeVikingFS()

    async def initialize_account_directories(self, ctx):
        return None

    async def initialize_user_directories(self, ctx):
        return None


def _build_lightweight_admin_test_app() -> FastAPI:
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry
    from openviking.server.routers import admin as admin_router

    app = FastAPI()
    app.state.config = ServerConfig(root_api_key=ROOT_KEY)
    fake_service = _FakeService()
    app.state.fake_service = fake_service
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

    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    app.state.auth_plugin = registry.get("api_key")()

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
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry

    config = ServerConfig(root_api_key=ROOT_KEY)
    app = create_app(config=config, service=admin_service)
    set_service(admin_service)

    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=admin_service.viking_fs)
    await manager.load()
    app.state.api_key_manager = manager

    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    app.state.auth_plugin = registry.get("api_key")()

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


async def _agfs_exists(service: OpenVikingService, path: str) -> bool:
    try:
        await service.viking_fs._async_agfs.stat(path)
    except Exception:
        return False
    return True


async def _agfs_mkdirp(service: OpenVikingService, path: str) -> None:
    parts = [part for part in path.strip("/").split("/") if part]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else f"/{part}"
        if await _agfs_exists(service, current):
            continue
        await service.viking_fs._async_agfs.mkdir(current)


async def _agfs_write(service: OpenVikingService, path: str, content: str) -> None:
    await _agfs_mkdirp(service, path.rsplit("/", 1)[0])
    await service.viking_fs._async_agfs.write(path, content.encode("utf-8"))


async def _agfs_read_text(service: OpenVikingService, path: str) -> str:
    raw = await service.viking_fs._async_agfs.read(path)
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    if hasattr(raw, "content"):
        return raw.content.decode("utf-8")
    return str(raw)


async def _wait_for_task(client: httpx.AsyncClient, task_id: str) -> dict:
    for _ in range(100):
        resp = await client.get(f"/api/v1/tasks/{task_id}", headers=root_headers())
        assert resp.status_code == 200
        task = resp.json()["result"]
        if task["status"] in {"completed", "failed"}:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not finish")


# ---- Account CRUD ----


async def test_create_account(admin_client: httpx.AsyncClient, admin_service: OpenVikingService):
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

    ctx = RequestContext(user=UserIdentifier(acct, "alice"), role=Role.ADMIN)
    assert await admin_service.viking_fs.abstract("viking://resources", ctx=ctx)
    assert await admin_service.viking_fs.abstract("viking://user", ctx=ctx)


async def test_create_user_paths_accept_initial_user_config(
    lightweight_admin_client: httpx.AsyncClient,
    lightweight_admin_app: FastAPI,
):
    acct = _uid()
    viking_fs = lightweight_admin_app.state.fake_service.viking_fs

    resp = await lightweight_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
            "user_config": {"add_targets": {"resource_uri": "viking://user/resources/admin"}},
        },
        headers=root_headers(),
    )
    assert resp.status_code == 200, resp.text

    alice_settings = await read_user_add_targets(
        viking_fs,
        RequestContext(user=UserIdentifier(acct, "alice"), role=Role.ADMIN),
    )
    assert alice_settings.resource_uri == "viking://user/resources/admin"

    resp = await lightweight_admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={
            "user_id": "bob",
            "role": "user",
            "user_config": {"add_targets": {"resource_uri": "viking://user/resources/bob"}},
        },
        headers=root_headers(),
    )
    assert resp.status_code == 200, resp.text

    bob_settings = await read_user_add_targets(
        viking_fs,
        RequestContext(user=UserIdentifier(acct, "bob"), role=Role.USER),
    )
    assert bob_settings.resource_uri == "viking://user/resources/bob"


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


async def test_seeded_admin_key_endpoints(admin_client: httpx.AsyncClient):
    from openviking.server.api_keys import parse_api_key

    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice", "seed": "admin-seed"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    admin_key = resp.json()["result"]["user_key"]
    account_id, user_id, secret = parse_api_key(admin_key)
    assert account_id == acct
    assert user_id == "alice"
    assert secret == _seed_secret("alice", "admin-seed")

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user", "seed": "bob-seed"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    old_key = resp.json()["result"]["user_key"]
    _, _, old_secret = parse_api_key(old_key)
    assert old_secret == _seed_secret("bob", "bob-seed")

    resp = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users/bob/key",
        json={"seed": "bob-new-seed"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    new_key = resp.json()["result"]["user_key"]
    _, _, new_secret = parse_api_key(new_key)
    assert new_secret == _seed_secret("bob", "bob-new-seed")
    assert new_key != old_key

    resp = await admin_client.get(
        "/api/v1/fs/ls?uri=viking://",
        headers={"X-API-Key": old_key},
    )
    assert resp.status_code == 401


async def test_empty_seed_rejected(admin_client: httpx.AsyncClient):
    acct = _uid()
    resp = await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice", "seed": ""},
        headers=root_headers(),
    )
    assert resp.status_code == 400


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


# ---- Legacy migration ----


async def test_user_role_cannot_run_legacy_migration(admin_client: httpx.AsyncClient):
    """Legacy migration is ROOT-only."""
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

    resp = await admin_client.post(
        "/api/v1/admin/migrate",
        headers={"X-API-Key": bob_key},
    )
    assert resp.status_code == 403


async def test_legacy_migration_preflight_failure_does_not_create_task(
    admin_client: httpx.AsyncClient,
    admin_service: OpenVikingService,
):
    """Ambiguous session ownership fails preflight before task creation."""
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
    await _agfs_write(admin_service, f"/local/{acct}/session/orphan/messages.jsonl", "{}\n")

    resp = await admin_client.post("/api/v1/admin/migrate", headers=root_headers())
    assert resp.status_code == 412
    error = resp.json()["error"]
    assert error["code"] == "FAILED_PRECONDITION"
    assert error["details"]["operation_count"] == 0
    assert error["details"]["errors"][0]["session_id"] == "orphan"

    tasks_resp = await admin_client.get(
        "/api/v1/tasks?task_type=legacy_migration",
        headers=root_headers(),
    )
    assert tasks_resp.status_code == 200
    assert tasks_resp.json()["result"] == []


async def test_legacy_migration_task_migrates_legacy_data(
    admin_client: httpx.AsyncClient,
    admin_app,
    admin_service: OpenVikingService,
):
    """ROOT migrate fans out shared agent data and moves sessions under users."""
    acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    register_bob = await admin_client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": "bob", "role": "user"},
        headers=root_headers(),
    )
    bob_key = register_bob.json()["result"]["user_key"]

    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/memories/facts/project.md",
        "shared fact",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/skills/code-review/SKILL.md",
        "legacy skill",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/instructions/system.md",
        "do not migrate",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/bob/skills/code-review/SKILL.md",
        "existing skill",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-001/.meta.json",
        json.dumps({"created_by_user_id": "alice"}),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-001/messages.jsonl",
        '{"role":"user"}\n',
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-002/.meta.json",
        json.dumps({"user_id": "charlie"}),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-002/messages.jsonl",
        '{"role":"assistant"}\n',
    )

    resp = await admin_client.post("/api/v1/admin/migrate", headers=root_headers())
    assert resp.status_code == 200
    task_id = resp.json()["result"]["task_id"]
    assert await _agfs_exists(
        admin_service,
        f"/local/{SYSTEM_TASK_ACCOUNT_ID}/tasks/{SYSTEM_TASK_USER_ID}/{task_id}.json",
    )
    hidden_resp = await admin_client.get(f"/api/v1/tasks/{task_id}", headers={"X-API-Key": bob_key})
    assert hidden_resp.status_code == 404

    task = await _wait_for_task(admin_client, task_id)
    assert task["status"] == "completed"
    result = task["result"]
    created_user = next(item for item in result["created_users"] if item["user_id"] == "charlie")
    assert created_user["account_id"] == acct
    assert "user_key" not in created_user
    assert result["migrated"]["operations"]["agent_memories"] == 3
    assert result["migrated"]["operations"]["agent_skills"] == 2
    assert result["migrated"]["operations"]["sessions"] == 2
    assert any(item["reason"] == "target skill already exists" for item in result["skipped"])
    assert any("Skipped legacy instructions" in item for item in result["warnings"])

    manager = admin_app.state.api_key_manager
    assert manager.has_user(acct, "charlie")
    for user_id in ("alice", "bob", "charlie"):
        assert (
            await _agfs_read_text(
                admin_service,
                f"/local/{acct}/user/{user_id}/peers/code-agent/memories/facts/project.md",
            )
            == "shared fact"
        )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/skills/code-review/SKILL.md",
        )
        == "legacy skill"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/bob/skills/code-review/SKILL.md",
        )
        == "existing skill"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/sessions/sess-001/messages.jsonl",
        )
        == '{"role":"user"}\n'
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/sessions/sess-002/messages.jsonl",
        )
        == '{"role":"assistant"}\n'
    )
    assert not await _agfs_exists(
        admin_service,
        f"/local/{acct}/user/alice/peers/code-agent/instructions/system.md",
    )


async def test_legacy_migration_covers_all_accounts_and_agent_user_layout(
    admin_client: httpx.AsyncClient,
    admin_app,
    admin_service: OpenVikingService,
):
    """One ROOT migration scans all accounts and handles agent/user scoped legacy data."""
    acct = _uid()
    other_acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "admin"},
        headers=root_headers(),
    )
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other_acct, "admin_user_id": "dana"},
        headers=root_headers(),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/charlie/memories/.overview.md",
        "legacy physical user",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/memories/facts/shared.md",
        "shared fact",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/review-agent/user/charlie/memories/facts/private.md",
        "private fact",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/review-agent/user/charlie/skills/review/SKILL.md",
        "review skill",
    )
    await _agfs_write(
        admin_service,
        f"/local/{other_acct}/agent/code-agent/memories/facts/other.md",
        "other account fact",
    )

    resp = await admin_client.post("/api/v1/admin/migrate", headers=root_headers())
    assert resp.status_code == 200
    task = await _wait_for_task(admin_client, resp.json()["result"]["task_id"])
    assert task["status"] == "completed"

    result = task["result"]
    assert any(
        item["account_id"] == acct and item["user_id"] == "charlie"
        for item in result["created_users"]
    )
    manager = admin_app.state.api_key_manager
    assert manager.has_user(acct, "charlie")
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/admin/peers/code-agent/memories/facts/shared.md",
        )
        == "shared fact"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/peers/code-agent/memories/facts/shared.md",
        )
        == "shared fact"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/peers/review-agent/memories/facts/private.md",
        )
        == "private fact"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/charlie/skills/review/SKILL.md",
        )
        == "review skill"
    )
    assert not await _agfs_exists(
        admin_service,
        f"/local/{acct}/user/admin/peers/review-agent/memories/facts/private.md",
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{other_acct}/user/dana/peers/code-agent/memories/facts/other.md",
        )
        == "other account fact"
    )


async def test_legacy_cleanup_removes_only_legacy_namespaces(
    admin_client: httpx.AsyncClient,
    admin_service: OpenVikingService,
):
    """Cleanup removes legacy agent/session roots without deleting migrated user data."""
    acct = _uid()
    other_acct = _uid()
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=root_headers(),
    )
    await admin_client.post(
        "/api/v1/admin/accounts",
        json={"account_id": other_acct, "admin_user_id": "dana"},
        headers=root_headers(),
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/agent/code-agent/memories/facts/old.md",
        "legacy agent",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/session/sess-001/messages.jsonl",
        '{"role":"user"}\n',
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/alice/agent/review-agent/memories/facts/old.md",
        "legacy user agent",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/alice/peers/code-agent/memories/facts/new.md",
        "new peer data",
    )
    await _agfs_write(
        admin_service,
        f"/local/{acct}/user/alice/sessions/sess-001/messages.jsonl",
        '{"role":"assistant"}\n',
    )
    await _agfs_write(
        admin_service,
        f"/local/{other_acct}/agent/code-agent/memories/facts/old.md",
        "other legacy agent",
    )

    resp = await admin_client.post(
        "/api/v1/admin/migrate",
        json={"action": "cleanup"},
        headers=root_headers(),
    )
    assert resp.status_code == 200
    task = await _wait_for_task(admin_client, resp.json()["result"]["task_id"])
    assert task["status"] == "completed"
    assert task["task_type"] == "legacy_cleanup"
    assert task["result"]["cleanup"]["directories"] == 4
    removed = {
        (item["account_id"], item["source"]) for item in task["result"]["cleanup"]["targets"]
    }
    assert (acct, "viking://agent") in removed
    assert (acct, "viking://session") in removed
    assert (acct, "viking://user/alice/agent") in removed
    assert (other_acct, "viking://agent") in removed

    assert not await _agfs_exists(admin_service, f"/local/{acct}/agent")
    assert not await _agfs_exists(admin_service, f"/local/{acct}/session")
    assert not await _agfs_exists(admin_service, f"/local/{acct}/user/alice/agent")
    assert not await _agfs_exists(admin_service, f"/local/{other_acct}/agent")
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/peers/code-agent/memories/facts/new.md",
        )
        == "new peer data"
    )
    assert (
        await _agfs_read_text(
            admin_service,
            f"/local/{acct}/user/alice/sessions/sess-001/messages.jsonl",
        )
        == '{"role":"assistant"}\n'
    )


async def test_legacy_agent_and_session_uri_reads_are_read_only(
    admin_service: OpenVikingService,
):
    """Old agent/session URIs remain readable but not mutable."""
    ctx = RequestContext(user=UserIdentifier("default", "admin_user"), role=Role.USER)
    await _agfs_write(
        admin_service,
        "/local/default/agent/code-agent/memories/facts/project.md",
        "legacy agent fact",
    )
    await _agfs_write(
        admin_service,
        "/local/default/session/old-session/messages.jsonl",
        '{"role":"user"}\n',
    )

    assert (
        await admin_service.viking_fs.read_file(
            "viking://agent/code-agent/memories/facts/project.md",
            ctx=ctx,
        )
        == "legacy agent fact"
    )
    assert (
        await admin_service.viking_fs.read_file(
            "viking://session/old-session/messages.jsonl",
            ctx=ctx,
        )
        == '{"role":"user"}\n'
    )
    with pytest.raises(PermissionDeniedError):
        await admin_service.viking_fs.write_file(
            "viking://agent/code-agent/memories/facts/new.md",
            "blocked",
            ctx=ctx,
        )
    with pytest.raises(PermissionDeniedError):
        await admin_service.viking_fs.mkdir("viking://session/new-session", ctx=ctx)


@pytest_asyncio.fixture(scope="function")
async def trusted_admin_app(admin_service):
    from openviking.server.auth.plugins import TrustedAuthPlugin
    from openviking.server.auth.registry import get_registry

    config = ServerConfig(auth_mode="trusted", root_api_key=ROOT_KEY)
    app = create_app(config=config, service=admin_service)
    set_service(admin_service)
    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=admin_service.viking_fs)
    await manager.load()
    # Create test users for trusted mode tests if they don't exist
    if "platform" not in manager._accounts:
        await manager.create_account("platform", "gateway-admin")
    app.state.api_key_manager = manager

    # Set auth plugin (lifespan not triggered in ASGI tests)
    registry = get_registry()
    if registry.get("trusted") is None:
        registry.register(TrustedAuthPlugin)
    app.state.auth_plugin = registry.get("trusted")()

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


async def test_trusted_mode_admin_api_uses_trusted_gateway_identity(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted admin routes use the trusted gateway identity instead of tenant user role."""
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
    assert resp.status_code == 200
    assert resp.json()["result"]["account_id"] == acct
    assert resp.json()["result"]["user_id"] == "bob"
    assert "user_key" not in resp.json()["result"]


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


async def test_trusted_mode_create_account_lists_current_account_metadata(
    trusted_admin_client: httpx.AsyncClient,
    trusted_admin_app,
):
    """Trusted account creation should list the current account metadata shape."""
    # Set gateway-admin to ROOT role first
    manager = trusted_admin_app.state.api_key_manager
    await manager.set_role("platform", "gateway-admin", "root")

    acct = _uid()
    resp = await trusted_admin_client.post(
        "/api/v1/admin/accounts",
        json={
            "account_id": acct,
            "admin_user_id": "alice",
        },
        headers=trusted_headers(
            account="platform",
            user="gateway-admin",
            include_api_key=True,
        ),
    )
    assert resp.status_code == 200

    manager = trusted_admin_app.state.api_key_manager
    account = next(item for item in manager.get_accounts() if item["account_id"] == acct)
    assert set(account) == {"account_id", "created_at", "user_count"}
