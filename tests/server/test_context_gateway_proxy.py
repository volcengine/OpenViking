"""Studio binds a gateway key to a chosen user; the user's key stays on the server."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import context_gateway
from openviking_cli.session.user_id import UserIdentifier

TOKEN_ENV = "TEST_CONTEXT_GATEWAY_ADMIN_TOKEN"
USERS = {
    "a": [
        {"user_id": "alice", "role": "user", "api_key": "alice-key"},
        {"user_id": "boss", "role": "admin", "api_key": "boss-key"},
        {"user_id": "hashed", "role": "user", "key_prefix": "prefix"},
        {"user_id": "root", "role": "root", "api_key": "root-key"},
    ],
    "b": [{"user_id": "carol", "role": "user", "api_key": "carol-key"}],
}
SETTINGS = {"name": "Laptop", "policy_id": "p", "upstream_ids": ["u"], "models": []}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(context_gateway.router)
    app.state.api_key_manager = SimpleNamespace(
        refresh_account_users_from_store=AsyncMock(),
        get_users=lambda account_id, **kwargs: USERS[account_id],
    )
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "boss"), role=Role.ADMIN
    )
    return app


@pytest.fixture
def forwarded(monkeypatch):
    """Requests that reached the gateway's management API."""
    calls = []

    def gateway(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "k", "key": "ovcg_secret"})

    monkeypatch.setenv(TOKEN_ENV, "t" * 32)
    config = SimpleNamespace(enabled=True, admin_token_env=TOKEN_ENV, url="http://gateway")
    monkeypatch.setattr(
        context_gateway,
        "get_openviking_config",
        lambda: SimpleNamespace(context_gateway=config),
    )
    monkeypatch.setattr(
        context_gateway,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(gateway), **kwargs
            ),
            HTTPError=httpx.HTTPError,
        ),
    )
    return calls


async def send(app, method, path, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, "/api/v1/admin/context-gateway/" + path, **kwargs)


@pytest.mark.parametrize("user_id,key", [("alice", "alice-key"), ("boss", "boss-key")])
async def test_selected_user_is_replaced_with_their_stored_key(app, forwarded, user_id, key):
    response = await send(app, "POST", "keys", json={**SETTINGS, "user_id": user_id})
    assert response.status_code == 200
    (request,) = forwarded
    assert str(request.url) == "http://gateway/admin/keys"
    assert request.headers["X-OpenViking-Account"] == "a"
    assert json.loads(request.content) == {**SETTINGS, "openviking_key": key}
    assert key not in response.text
    app.state.api_key_manager.refresh_account_users_from_store.assert_awaited_with("a")


@pytest.mark.parametrize("user_id", ["missing", "carol", "root"])
async def test_only_users_and_admins_of_the_current_account_can_be_selected(
    app, forwarded, user_id
):
    response = await send(app, "POST", "keys", json={**SETTINGS, "user_id": user_id})
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown OpenViking user in this account"
    assert forwarded == []


async def test_unreadable_key_asks_for_a_pasted_key(app, forwarded):
    response = await send(app, "POST", "keys", json={**SETTINGS, "user_id": "hashed"})
    assert response.status_code == 409
    assert "paste the user's OpenViking key" in response.json()["detail"]
    assert "prefix" not in response.text
    assert forwarded == []


async def test_root_cannot_choose_a_user(app, forwarded):
    # Root's request account need not be the account Studio shows.
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "default"), role=Role.ROOT
    )
    response = await send(app, "POST", "keys", json={**SETTINGS, "user_id": "alice"})
    assert response.status_code == 400
    assert "paste the user's OpenViking key" in response.json()["detail"]
    assert forwarded == []
    app.state.api_key_manager.refresh_account_users_from_store.assert_not_called()


async def test_user_and_pasted_key_together_are_rejected_without_echo(app, forwarded):
    response = await send(
        app, "POST", "keys", json={**SETTINGS, "user_id": "alice", "openviking_key": "pasted"}
    )
    assert response.status_code == 422
    assert "pasted" not in response.text
    assert forwarded == []


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "keys", json.dumps({**SETTINGS, "openviking_key": "pasted"}).encode()),
        ("POST", "keys", b'{"user_id": '),
        ("POST", "keys/k/capture/reset", b'{"user_id": "alice"}'),
        ("PUT", "upstreams/u", b'{"user_id": "alice"}'),
        ("GET", "keys", b""),
        ("GET", "tools", b""),
    ],
)
async def test_other_requests_pass_through_unchanged(app, forwarded, method, path, body):
    response = await send(app, method, path, content=body)
    assert response.status_code == 200
    (request,) = forwarded
    assert request.method == method
    assert request.url.path == "/admin/" + path
    assert request.content == body
    app.state.api_key_manager.refresh_account_users_from_store.assert_not_called()


@pytest.mark.parametrize("path", ["uploads", "v1/models"])
async def test_only_management_resources_are_forwarded(app, forwarded, path):
    response = await send(app, "GET", path)
    assert response.status_code == 404
    assert forwarded == []
