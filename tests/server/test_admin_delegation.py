# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from openviking.observability.http_observability_middleware import HTTPObservabilityMiddleware
from openviking.observability.usage_audit import (
    init_usage_audit_from_server_config,
    shutdown_usage_audit,
)
from openviking.server.auth import get_request_context
from openviking.server.auth.delegation import require_unbound_credential_export
from openviking.server.auth.plugins.api_key import ApiKeyAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.oauth.router import router as oauth_router
from openviking.server.request_id import RequestIdMiddleware
from openviking.server.routers.admin import router as admin_router
from openviking.server.routers.bot import router as bot_router
from openviking.server.routers.compile import router as compile_router
from openviking.server.routers.console import router as console_router
from openviking.server.routers.system import router as system_router
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import OpenVikingError, PermissionDeniedError
from tests.server.account_store_fakes import InMemoryAGFS


@pytest.fixture(params=[False, True], ids=["plaintext", "hashed"])
async def delegation_runtime(request, tmp_path):
    fs = SimpleNamespace(agfs=InMemoryAGFS())
    manager = build_api_key_manager("root", fs, api_key_hashing_enabled=request.param)
    await manager.load()
    admin_key = await manager.create_account("acme", "admin")
    user_key = await manager.register_user("acme", "alice")
    await manager.register_user("acme", "second-admin", role="admin")
    await manager.create_account("other", "alice")
    await manager.create_group("acme", "editors")
    await manager.add_group_members("acme", "editors", ["alice"])
    peer = build_api_key_manager("root", fs, api_key_hashing_enabled=request.param)
    await peer.load()
    config = ServerConfig(
        auth_mode="api_key", root_api_key="root", api_key_admin_delegation_enabled=True
    )
    config.observability.usage_audit.sqlite_path = str(tmp_path / "usage.sqlite3")
    app = FastAPI()
    app.state.config = config
    app.state.auth_plugin = ApiKeyAuthPlugin()
    app.state.api_key_manager = peer
    app.state.oauth_store = SimpleNamespace()
    app.state.oauth_provider = SimpleNamespace()
    app.include_router(admin_router)
    app.include_router(console_router)
    app.include_router(system_router)
    app.include_router(compile_router)
    app.include_router(bot_router, prefix="/bot/v1")
    app.include_router(oauth_router)
    app.add_middleware(HTTPObservabilityMiddleware)
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(OpenVikingError)
    async def error_handler(_, exc):
        return JSONResponse(status_code=403, content={"error": str(exc)})

    @app.get("/inspect")
    async def inspect(request: Request, ctx: RequestContext = Depends(get_request_context)):
        return {
            "account": ctx.account_id,
            "user": ctx.user.user_id,
            "role": str(ctx.role),
            "groups": ctx.group_ids,
            "delegated_by": ctx.delegated_by_user_id,
            "credential_present": bool(ctx.api_key),
            "header_present": bool(
                request.headers.get("X-API-Key") or request.headers.get("Authorization")
            ),
        }

    runtime = await init_usage_audit_from_server_config(config, app=app, service=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        try:
            yield SimpleNamespace(
                client=client,
                manager=manager,
                peer=peer,
                admin_key=admin_key,
                user_key=user_key,
                app=app,
                runtime=runtime,
            )
        finally:
            await shutdown_usage_audit(app=app)
            await peer.close()
            await manager.close()


def _headers(runtime, **extra):
    return {
        "Authorization": f"OpenViking-Delegate {runtime.admin_key}",
        "X-OpenViking-Delegate-User": "alice",
        **extra,
    }


async def test_explicit_protocol_fixed_user_groups_no_export_and_persisted_audit(
    delegation_runtime,
):
    r = delegation_runtime
    response = await r.client.get(
        "/inspect", headers=_headers(r, **{"X-OpenViking-Groups": "admin-group"})
    )
    assert response.status_code == 200
    assert response.json() == {
        "account": "acme",
        "user": "alice",
        "role": "user",
        "groups": ["editors"],
        "delegated_by": "admin",
        "credential_present": False,
        "header_present": False,
    }
    denied = await r.client.post(
        "/api/v1/admin/accounts/acme/users", headers=_headers(r), json={"user_id": "forged"}
    )
    assert denied.status_code == 403
    await r.runtime.worker.flush()
    audit = await r.runtime.store.query_audit_logs(account_id="acme", user_id="alice")
    successful = next(item for item in audit["items"] if item["route"] == "/inspect")
    assert successful["delegated_by_user_id"] == "admin"
    assert successful["user_id"] == "alice"
    assert r.admin_key not in str(audit)
    assert r.user_key not in str(audit)


@pytest.mark.parametrize(
    "extra",
    [
        {"X-OpenViking-Account": "other"},
        {"X-OpenViking-User": "alice"},
        {"X-OpenViking-Role": "admin"},
        {"X-OpenViking-Delegate-User": ""},
        {"X-OpenViking-Delegate-User": "missing"},
        {"X-OpenViking-Delegate-User": "../alice"},
        {"Authorization": "OpenViking-Delegate root"},
        {"X-API-Key": "ambiguous-normal-key"},
    ],
)
async def test_delegation_rejects_assertions_missing_targets_and_root(delegation_runtime, extra):
    r = delegation_runtime
    assert (await r.client.get("/inspect", headers=_headers(r, **extra))).status_code == 403


async def test_user_credential_cannot_delegate_and_feature_is_opt_in(delegation_runtime):
    r = delegation_runtime
    assert (
        await r.client.get(
            "/inspect",
            headers=_headers(r, **{"Authorization": f"OpenViking-Delegate {r.user_key}"}),
        )
    ).status_code == 403
    r.app.state.config.api_key_admin_delegation_enabled = False
    assert (await r.client.get("/inspect", headers=_headers(r))).status_code == 403
    health = (await r.client.get("/health")).json()
    assert health["capabilities"]["admin_user_delegation"] is False
    # No explicit header retains the old native API-key identity-header behavior.
    plain = await r.client.get(
        "/inspect", headers={"X-API-Key": r.admin_key, "X-OpenViking-User": "alice"}
    )
    assert plain.json()["user"] == "admin"
    assert plain.json()["role"] == "admin"


async def test_health_reports_effective_identity_and_capability(delegation_runtime):
    r = delegation_runtime
    result = (await r.client.get("/health", headers=_headers(r))).json()
    assert result["capabilities"]["admin_user_delegation"] is True
    assert (
        result["account_id"],
        result["user_id"],
        result["role"],
        result["delegated_by_user_id"],
    ) == ("acme", "alice", "user", "admin")


async def test_two_readers_observe_group_revocation_and_target_deletion(delegation_runtime):
    r = delegation_runtime
    assert (await r.client.get("/inspect", headers=_headers(r))).json()["groups"] == ["editors"]
    await r.manager.remove_group_members("acme", "editors", ["alice"])
    assert (await r.client.get("/inspect", headers=_headers(r))).json()["groups"] == []
    await r.manager.begin_deletion(
        "acme", "alice", task_id="delete-alice", owner_account_id="acme", owner_user_id="admin"
    )
    assert (await r.client.get("/inspect", headers=_headers(r))).status_code == 403


@pytest.mark.parametrize("change", ["rotate", "demote", "delete"])
async def test_stale_admin_snapshot_cannot_authorize_new_delegation(delegation_runtime, change):
    r = delegation_runtime
    stale = await r.peer.resolve_identity(r.admin_key)
    if change == "rotate":
        await r.manager.regenerate_key("acme", "admin")
    elif change == "demote":
        await r.manager.set_role("acme", "admin", "user")
    else:
        await r.manager.begin_deletion(
            "acme",
            "admin",
            task_id="delete-admin",
            owner_account_id="acme",
            owner_user_id="second-admin",
        )
    with pytest.raises(PermissionDeniedError):
        await r.peer.resolve_admin_delegation(r.admin_key, stale, "alice")


async def test_delegation_never_uses_oauth_or_unsupported_provider(delegation_runtime, monkeypatch):
    r = delegation_runtime
    identity = await r.peer.resolve_identity(r.admin_key)
    with pytest.raises(PermissionDeniedError):
        await r.peer.resolve_admin_delegation(
            r.admin_key, replace(identity, from_oauth=True), "alice"
        )
    monkeypatch.setattr(r.peer._store, "supports_admin_delegation", False)
    assert (await r.client.get("/inspect", headers=_headers(r))).status_code == 403
    assert (await r.client.get("/health")).json()["capabilities"]["admin_user_delegation"] is False


async def test_credential_export_boundary(delegation_runtime):
    r = delegation_runtime
    target = await r.peer.resolve_admin_delegation(
        r.admin_key, await r.peer.resolve_identity(r.admin_key), "alice"
    )
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(
        user=UserIdentifier("acme", "alice"),
        role=Role.USER,
        delegated_by_user_id=target.delegated_by_user_id,
    )
    with pytest.raises(PermissionDeniedError, match="does not preserve"):
        require_unbound_credential_export(ctx, "Compile")


async def test_target_does_not_need_a_key_and_cannot_mint_one(delegation_runtime):
    r = delegation_runtime
    await r.manager._store.revoke_active_api_keys("acme", "alice")
    assert (await r.client.get("/inspect", headers=_headers(r))).status_code == 200
    assert (
        await r.client.post("/api/v1/admin/accounts/acme/users/alice/key", headers=_headers(r))
    ).status_code == 403


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/compile", {"compile_name": "synthetic-compile"}),
        ("/bot/v1/sessions/synthetic/interrupt", None),
        ("/api/v1/auth/oauth-verify", {"code": "ABC123"}),
    ],
)
async def test_unbound_exports_are_denied_at_real_http_entrypoints(delegation_runtime, path, body):
    r = delegation_runtime
    response = await r.client.post(path, headers=_headers(r), json=body)
    assert response.status_code == 403, response.text
    assert "does not preserve Admin delegation" in response.text


async def test_standard_bearer_cannot_enable_delegation(delegation_runtime):
    r = delegation_runtime
    response = await r.client.get(
        "/inspect",
        headers={"Authorization": f"Bearer {r.admin_key}", "X-OpenViking-Delegate-User": "alice"},
    )
    assert response.status_code == 403
