"""The browser cannot elevate or cross accounts through Studio management."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import admin, bot_studio
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(bot_studio.router)
    app.include_router(admin.router)
    return app


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_management_rejects_non_root(app, role, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: SimpleNamespace(
        role=role, account_id="a"
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, url in [
            ("GET", "/api/v1/admin/accounts/a/bot/connections"),
            ("POST", "/api/v1/admin/accounts/a/bot/connections"),
            ("POST", "/api/v1/admin/accounts/a/bot/onboarding-runs"),
            ("GET", "/api/v1/admin/accounts/a/bot/onboarding-runs/current?type=feishu"),
            ("GET", "/api/v1/admin/accounts/a/bot/onboarding-runs/job"),
            ("POST", "/api/v1/admin/accounts/a/bot/onboarding-runs/job/actions"),
            ("DELETE", "/api/v1/admin/accounts/a/bot/connections/x?revision=1"),
            ("POST", "/api/v1/admin/accounts/a/bot/connections/x/credentials"),
            ("POST", "/api/v1/admin/accounts/a/bot/connections/x/verifications"),
            ("GET", "/api/v1/admin/accounts/a/bot/connections/x/messages?conversation=y"),
        ]:
            result = await client.request(method, url, json={})
            assert result.status_code == 403
    dispatch.assert_not_called()


async def test_update_scope_comes_from_authenticated_context(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "root"), role=Role.ROOT
    )
    dispatch = AsyncMock(return_value={"status": "ok", "result": {}})
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.patch(
            "/api/v1/admin/accounts/a/bot/connections/id", json={"revision": 1, "enabled": False}
        )
        assert result.status_code == 200
    assert dispatch.call_args.args[0].account_id == "a"


@pytest.mark.parametrize("include_summary", [True, False])
@pytest.mark.parametrize("include_credentials", [True, False])
async def test_reused_admin_users_support_safe_credential_status(
    app, monkeypatch, include_credentials, include_summary
):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "root"),
        role=Role.ROOT,
    )
    registry = SimpleNamespace(
        refresh_account_users_from_store=AsyncMock(),
        get_users_page=lambda *args, **kwargs: {
            "users": [
                {"user_id": "bot", "role": "user", "api_key": "private"},
                {"user_id": "hashed", "role": "user", "key_prefix": "prefix"},
            ],
            "total": 2,
            "account_total": 2,
            "manager_count": 0,
            "key_count": 2,
        },
    )
    app.state.api_key_manager = registry
    monkeypatch.setattr(admin, "_get_api_key_manager", lambda request: registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/admin/accounts/a/users",
            params={
                "role": "user",
                "include_credentials": str(include_credentials).lower(),
                "include_summary": str(include_summary).lower(),
            },
        )
    assert response.status_code == 200
    result = response.json()["result"]
    if include_summary:
        assert result["total"] == result["account_total"] == 2
        assert result["manager_count"] == 0
        result = result["users"]
    if include_credentials:
        assert result[0]["api_key"] == "private"
    else:
        assert "private" not in response.text and "prefix" not in response.text
        assert result == [
            {"user_id": "bot", "role": "user", "api_key_available": True},
            {"user_id": "hashed", "role": "user", "api_key_available": False},
        ]


@pytest.mark.parametrize("user_id,accepted", [("missing", False), ("root", False), ("bot", True)])
async def test_selection_uses_current_account_registry(monkeypatch, user_id, accepted):
    from fastapi import HTTPException

    registry = SimpleNamespace(
        refresh_account_users_from_store=AsyncMock(),
        get_users=lambda *a, **kw: [{"user_id": "bot", "api_key": "internal-key"}],
    )
    monkeypatch.setattr(bot_studio, "get_api_key_manager_or_raise", lambda request: registry)
    monkeypatch.setattr(
        bot_studio, "get_server_url_from_server_data", lambda config: "http://localhost"
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=None)))
    ctx = SimpleNamespace(account_id="a")
    if accepted:
        result = await bot_studio.selected_identity(request, ctx, user_id)
        assert result["api_key"] == "internal-key"
        assert result["role"] == "user"
        assert result["account_id"] == "a"
    else:
        with pytest.raises(HTTPException) as exc:
            await bot_studio.selected_identity(request, ctx, user_id)
        assert exc.value.status_code == 400
    registry.refresh_account_users_from_store.assert_awaited_with("a")


async def test_hashed_key_is_not_treated_as_a_usable_credential(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        bot_studio,
        "account_users",
        AsyncMock(return_value=[{"user_id": "bot", "key_prefix": "prefix"}]),
    )
    with pytest.raises(HTTPException) as exc:
        await bot_studio.selected_identity(None, SimpleNamespace(account_id="a"), "bot")
    assert exc.value.status_code == 409


async def test_onboarding_identity_is_selected_server_side(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "root"), role=Role.ROOT
    )
    identity = {"user_id": "bot", "account_id": "a", "api_key": "server-key"}
    select = AsyncMock(return_value=identity)
    dispatch = AsyncMock(return_value={"status": "ok", "result": {"id": "job"}})
    monkeypatch.setattr(bot_studio, "selected_identity", select)
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/api/v1/admin/accounts/a/bot/onboarding-runs",
            json={
                "user_id": "bot",
                "type": "feishu",
                "request_id": "e232e744-f13a-44f3-8fda-4e82ad1b58b4",
            },
        )
    assert result.status_code == 200
    assert dispatch.call_args.args[0].account_id == "a"
    assert dispatch.call_args.kwargs["identity"] == identity
    assert "server-key" not in result.text


@pytest.mark.parametrize(
    "path,allowed",
    [
        ("/api/v1/admin/bot/capabilities", True),
        ("/api/v1/admin/accounts/a/users", True),
        ("/api/v1/admin/accounts/a/bot/onboarding-runs", True),
        ("/bot/v1/chat", False),
        ("/bot/v1/studio-other", False),
        ("/bot/v1/studio/connections", False),
    ],
)
def test_real_root_policy_allows_only_studio_control_plane(path, allowed):
    from openviking.server.auth.plugins.api_key import ApiKeyAuthPlugin
    from openviking.server.identity import ResolvedIdentity
    from openviking_cli.exceptions import PermissionDeniedError

    identity = ResolvedIdentity(role="root", account_id="default", user_id="default")
    if allowed:
        ApiKeyAuthPlugin().get_request_context_checks(path, identity)
    else:
        with pytest.raises(PermissionDeniedError):
            ApiKeyAuthPlugin().get_request_context_checks(path, identity)


async def test_root_management_scope_comes_from_account_path(app, monkeypatch):
    from openviking.server.identity import RequestContext
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier("default", "default"), role="root")
    app.dependency_overrides[get_request_context] = lambda: ctx
    dispatch = AsyncMock(return_value={"status": "ok", "result": []})
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get(
            "/api/v1/admin/accounts/team/bot/connections",
            headers={"X-OpenViking-Studio-Account": "ignored"},
        )
    assert result.status_code == 200
    assert dispatch.call_args.args[0].account_id == "team"
    assert ctx.account_id == "default"


async def test_removed_scheduler_endpoint_is_not_exposed(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "root"), role=Role.ROOT
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/studio/schedules")
    assert response.status_code == 404
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "method,suffix,body,action",
    [
        ("PATCH", "", {"revision": 3, "enabled": True}, "resume"),
        ("DELETE", "?revision=3", None, "delete"),
        ("POST", "/verifications", {"revision": 3}, "verify"),
    ],
)
async def test_connection_http_methods_map_to_lifecycle(
    app, monkeypatch, method, suffix, body, action
):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("default", "root"),
        role=Role.ROOT,
    )
    dispatch = AsyncMock(return_value={"status": "ok", "result": {}})
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(
            method, "/api/v1/admin/accounts/team/bot/connections/id" + suffix, json=body
        )
    assert response.status_code == 200
    assert dispatch.call_args.args[0].account_id == "team"
    assert dispatch.call_args.args[2] == {"action": action, "revision": 3}


async def test_platform_credentials_are_opaque_to_http_router(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "root"),
        role=Role.ROOT,
    )
    identity = {"user_id": "bot", "api_key": "server-only"}
    monkeypatch.setattr(bot_studio, "selected_identity", AsyncMock(return_value=identity))
    dispatch = AsyncMock(return_value={"status": "ok", "result": {"id": "new"}})
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/admin/accounts/a/bot/connections",
            json={
                "type": "future-platform",
                "user_id": "bot",
                "credentials": {"client_id": "client", "client_secret": "secret"},
            },
        )
        assert response.status_code == 200
        assert dispatch.call_args.args[2]["type"] == "future-platform"
        assert dispatch.call_args.args[2]["credentials"] == {
            "client_id": "client",
            "client_secret": "secret",
        }
        assert dispatch.call_args.kwargs["identity"] == identity
        response = await client.patch(
            "/api/v1/admin/accounts/a/bot/connections/new",
            json={
                "revision": 1,
                "enabled": True,
                "identity": {"api_key": "forged"},
            },
        )
        assert response.status_code == 422


async def test_settings_patch_reuses_connection_endpoint(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "root"),
        role=Role.ROOT,
    )
    dispatch = AsyncMock(return_value={"status": "ok", "result": {}})
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/api/v1/admin/accounts/a/bot/connections/id",
            json={
                "revision": 2,
                "settings": {"thread_require_mention": False},
            },
        )
        assert response.status_code == 200
        assert dispatch.call_args.args[2] == {
            "action": "settings",
            "revision": 2,
            "settings": {"thread_require_mention": False},
        }
        response = await client.patch(
            "/api/v1/admin/accounts/a/bot/connections/id",
            json={
                "revision": 2,
                "enabled": True,
                "settings": {},
            },
        )
        assert response.status_code == 422


@pytest.mark.parametrize("action", ["retry", "cancel", "manual"])
async def test_onboarding_actions_preserve_account_and_validate_body(app, monkeypatch, action):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("default", "root"), role=Role.ROOT
    )
    dispatch = AsyncMock(return_value={"status": "ok", "result": {}})
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    base = "/api/v1/admin/accounts/team/bot/onboarding-runs/job"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(base + "/actions", json={"action": action})
        assert response.status_code == 200
        assert dispatch.call_args.args[0].account_id == "team"
        assert dispatch.call_args.args[1:] == ("onboarding_update", {"id": "job", "action": action})
        dispatch.reset_mock()
        for body in ({}, {"action": "unknown"}, {"action": action, "account": "other"}):
            response = await client.post(base + "/actions", json=body)
            assert response.status_code == 422
        response = await client.post(base + "/" + action)
        assert response.status_code == 404
        dispatch.assert_not_called()


def test_studio_routes_are_registered_but_excluded_from_public_schema(app):
    paths = app.openapi()["paths"]
    assert all(route.path not in paths for route in bot_studio.router.routes)
    assert all(
        route in app.routes or any(r.path == route.path for r in app.routes)
        for route in bot_studio.router.routes
    )
    assert "/api/v1/admin/accounts/{account_id}/users" in paths
