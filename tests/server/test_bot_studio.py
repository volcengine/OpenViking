"""The browser cannot elevate or cross accounts through Studio management."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.routers import bot_studio


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(bot_studio.router, prefix="/studio")
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
            ("GET", "/studio/connections"),
            ("POST", "/studio/connections"),
            ("POST", "/studio/onboarding"),
            ("GET", "/studio/onboarding"),
            ("GET", "/studio/onboarding/job"),
            ("PATCH", "/studio/onboarding/job"),
            ("GET", "/studio/connections/x/messages?conversation=y"),
        ]:
            result = await client.request(method, url, json={})
            assert result.status_code == 403
    dispatch.assert_not_called()


async def test_update_scope_comes_from_authenticated_context(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: SimpleNamespace(
        role="root", account_id="a"
    )
    dispatch = AsyncMock(return_value={"status": "ok", "result": {}})
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.patch(
            "/studio/connections/id", json={"account": "victim", "action": "pause"}
        )
        assert result.status_code == 200
    assert dispatch.call_args.args[0].account_id == "a"


async def test_users_never_expose_credentials(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: SimpleNamespace(
        role="root", account_id="a"
    )
    monkeypatch.setattr(
        bot_studio,
        "account_users",
        AsyncMock(return_value=[{"user_id": "bot", "api_key": "private"}, {"user_id": "hashed"}]),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/studio/users")
    assert response.status_code == 200
    assert "private" not in response.text
    assert response.json()["result"] == [
        {"user_id": "bot", "available": True},
        {"user_id": "hashed", "available": False},
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
    app.dependency_overrides[get_request_context] = lambda: SimpleNamespace(
        role="root", account_id="a"
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
            "/studio/onboarding",
            json={
                "user_id": "bot",
                "account": "victim",
                "identity": {"api_key": "forged"},
            },
        )
    assert result.status_code == 200
    assert dispatch.call_args.args[0].account_id == "a"
    assert dispatch.call_args.kwargs["identity"] == identity
    assert "server-key" not in result.text


@pytest.mark.parametrize(
    "path,allowed",
    [
        ("/bot/v1/studio/capabilities", True),
        ("/bot/v1/studio/users", True),
        ("/bot/v1/studio/onboarding", True),
        ("/bot/v1/chat", False),
        ("/bot/v1/studio-other", False),
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


async def test_root_studio_account_selector_does_not_use_data_identity_headers(app, monkeypatch):
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
            "/studio/connections", headers={"X-OpenViking-Studio-Account": "team"}
        )
    assert result.status_code == 200
    assert dispatch.call_args.args[0].account_id == "team"
    assert ctx.account_id == "default"


async def test_removed_scheduler_endpoint_is_not_exposed(app, monkeypatch):
    app.dependency_overrides[get_request_context] = lambda: SimpleNamespace(
        role="root", account_id="a"
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(bot_studio, "dispatch", dispatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/studio/schedules")
    assert response.status_code == 404
    dispatch.assert_not_called()
