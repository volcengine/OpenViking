# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""External OAuth contract, lifetime and identity regression checks."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from openviking.connector import auth
from openviking.connector.client import ConnectorClient
from openviking.parse.accessors.feishu_accessor import FeishuAccessor
from openviking.parse.understanding_api import UnderstandingAPI
from openviking.resource.feishu_watch_auth import (
    FeishuAppCredentials,
    FeishuOAuthClient,
    FeishuTokenRefreshError,
)
from openviking_cli.exceptions import InternalError, InvalidArgumentError

REFERENCE = {
    "account_id": "cloud-account",
    "user_id": "cloud-user",
    "ov_user_id": "alice",
    "platform": "feishu_doc",
    "type": "oauth",
}


@pytest.fixture(autouse=True)
def external_endpoint(monkeypatch):
    monkeypatch.setattr(
        auth, "external_auth_url", lambda: "https://connector.example/oauth/access_token"
    )


def test_http_contract_and_secret_safe_errors(monkeypatch):
    responses = iter(
        [
            {
                "code": 0,
                "data": {"access_token": "user-token", "expires_at": 4102444800, "version": 2},
            },
            {"code": 123, "message": "must-not-expose-secret"},
        ]
    )

    def handle(request):
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer key"
        assert request.headers["v-account-id"] == "cloud-account"
        assert json.loads(request.content) == REFERENCE
        return httpx.Response(200, json=next(responses))

    client_type = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    provider = auth.ExternalFeishuToken(REFERENCE, "key")
    assert provider.get_token() == "user-token"
    provider._valid_until = 0
    with pytest.raises(InternalError) as error:
        provider.get_token()
    assert "must-not-expose-secret" not in str(error.value)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"access_token": ""},
        {"access_token": "x", "expires_at": 1},
        {"access_token": "x", "expires_at": "4102444800"},
        {"access_token": "x", "expires_at": float("nan")},
        {"access_token": "x", "expires_at": True},
    ],
)
def test_invalid_or_expired_tokens_never_fall_back(monkeypatch, data):
    monkeypatch.setattr(ConnectorClient, "get_oauth_access_token", lambda *_args: data)
    with pytest.raises(InternalError, match="invalid or expired"):
        auth.ExternalFeishuToken(REFERENCE, "key").get_token()


@pytest.mark.asyncio
async def test_long_import_reloads_token_and_scopes_are_isolated(monkeypatch):
    now = [1000]
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: now[0]))
    fetch = Mock(
        side_effect=[
            {"access_token": "old", "expires_at": 1060},
            {"access_token": "new", "expires_at": 2000},
            {"access_token": "other-user", "expires_at": 2000},
        ]
    )
    monkeypatch.setattr(ConnectorClient, "get_oauth_access_token", fetch)
    provider = auth.ExternalFeishuToken(REFERENCE, "key")
    with auth.feishu_token_scope(provider):
        option = await asyncio.to_thread(FeishuAccessor._user_request_option, "snapshot")
        assert option.user_access_token == "old"
        assert provider.get_token() == "old"
        assert fetch.call_count == 1
        now[0] = 1031
        option = await asyncio.to_thread(FeishuAccessor._user_request_option, "snapshot")
        assert option.user_access_token == "new"
        api = object.__new__(UnderstandingAPI)
        assert await api._resolve_lark_file({"feishu_access_token": "snapshot"}) == {
            "user_access_token": "new"
        }

        async def other_task():
            with auth.feishu_token_scope(auth.ExternalFeishuToken(REFERENCE, "other-key")):
                return await asyncio.to_thread(FeishuAccessor._user_request_option, "snapshot")

        assert (await asyncio.create_task(other_task())).user_access_token == "other-user"
        assert auth.current_feishu_token.get() is provider
    assert auth.current_feishu_token.get() is None
    assert FeishuAccessor._user_request_option("explicit").user_access_token == "explicit"


@pytest.mark.parametrize(
    "reference", [None, {}, {**REFERENCE, "ov_user_id": "bob"}, {**REFERENCE, "platform": "git"}]
)
def test_reference_identity_is_checked(reference):
    with pytest.raises(InvalidArgumentError):
        auth.validate_oauth_ref(reference, "alice")


@pytest.mark.asyncio
async def test_external_mode_never_refreshes_locally(monkeypatch):
    credentials = FeishuAppCredentials("app", "secret", "https://open.feishu.cn", 30)
    client = FeishuOAuthClient(credentials)
    refresh = Mock(side_effect=AssertionError("local refresh must not run"))
    monkeypatch.setattr(client, "_refresh_user_access_token_sync", refresh)
    with pytest.raises(FeishuTokenRefreshError, match="disabled"):
        await client.refresh_user_access_token("shared-refresh-token")
    refresh.assert_not_called()
