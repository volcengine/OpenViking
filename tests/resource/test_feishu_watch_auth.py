# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Feishu watch auth helpers."""

import builtins
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from openviking.resource import feishu_watch_auth
from openviking.resource.feishu_watch_auth import (
    FeishuAppCredentials,
    FeishuOAuthClient,
    FeishuRefreshedToken,
    apply_feishu_refreshed_token,
    create_feishu_auth_state,
    feishu_auth_state_needs_refresh,
)


def test_oauth_client_uses_watch_app_credentials(monkeypatch):
    seen = {}

    def fake_load_credentials(*, app_id=None, app_secret=None):
        seen.update(app_id=app_id, app_secret=app_secret)
        return FeishuAppCredentials(
            app_id=app_id,
            app_secret=app_secret,
            domain="https://open.feishu.cn",
            request_timeout=30,
        )

    monkeypatch.setattr(feishu_watch_auth, "load_feishu_app_credentials", fake_load_credentials)

    client = FeishuOAuthClient.from_auth_state({"app_id": "cli-test", "app_secret": "secret-test"})

    assert seen == {"app_id": "cli-test", "app_secret": "secret-test"}
    assert client._credentials.app_id == "cli-test"


def test_feishu_auth_state_refresh_window():
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    state = create_feishu_auth_state("u-old", "r-old")

    assert feishu_auth_state_needs_refresh(state, now=now) is True

    refreshed = apply_feishu_refreshed_token(
        state,
        FeishuRefreshedToken(access_token="u-new", refresh_token="r-new", expires_in=7200),
        now=now,
    )

    assert refreshed["access_token"] == "u-new"
    assert refreshed["refresh_token"] == "r-new"
    assert feishu_auth_state_needs_refresh(refreshed, now=now) is False

    near_expiry = {
        **refreshed,
        "expires_at": (now + timedelta(minutes=4)).isoformat(),
    }
    assert feishu_auth_state_needs_refresh(near_expiry, now=now) is True


@pytest.mark.parametrize(
    ("domain", "token_url"),
    [
        ("https://open.feishu.cn", "https://accounts.feishu.cn/oauth/v3/token"),
        ("https://open.larksuite.com", "https://accounts.larksuite.com/oauth/v3/token"),
    ],
)
def test_refresh_user_token_uses_oauth_v3_and_rotates_refresh_token(monkeypatch, domain, token_url):
    def fake_post(url, *, data, timeout):
        assert url == token_url
        assert data == {
            "grant_type": "refresh_token",
            "client_id": "cli-test",
            "client_secret": "secret-test",
            "refresh_token": "header.payload.signature",
        }
        assert timeout == 12
        return httpx.Response(
            200,
            json={
                "access_token": "u-new",
                "refresh_token": "r-new",
                "expires_in": 7200,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = FeishuOAuthClient(
        FeishuAppCredentials(
            app_id="cli-test",
            app_secret="secret-test",
            domain=domain,
            request_timeout=12,
        )
    )
    legacy_client = MagicMock()
    legacy_client.authen.v1.refresh_access_token.create.side_effect = AssertionError(
        "legacy authen/v1 refresh endpoint must not be used"
    )
    monkeypatch.setattr(client, "_get_client", lambda: legacy_client, raising=False)

    refreshed = client._refresh_user_access_token_sync("header.payload.signature")

    assert refreshed == FeishuRefreshedToken(
        access_token="u-new",
        refresh_token="r-new",
        expires_in=7200,
    )


def test_refresh_user_token_keeps_legacy_endpoint_for_ur_tokens(monkeypatch):
    response = MagicMock()
    response.success.return_value = True
    response.data.access_token = "u-new"
    response.data.refresh_token = "ur-new"
    response.data.expires_in = 7200
    legacy_client = MagicMock()
    legacy_client.authen.v1.refresh_access_token.create.return_value = response
    legacy_authen = SimpleNamespace(
        CreateRefreshAccessTokenRequest=MagicMock(),
        CreateRefreshAccessTokenRequestBody=MagicMock(),
    )
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "lark_oapi.api.authen.v1":
            return legacy_authen
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(
        httpx,
        "post",
        MagicMock(side_effect=AssertionError("legacy tokens must not use OAuth v3")),
    )
    client = FeishuOAuthClient(
        FeishuAppCredentials(
            app_id="cli-test",
            app_secret="secret-test",
            domain="https://open.feishu.cn",
            request_timeout=12,
        )
    )
    monkeypatch.setattr(client, "_get_client", lambda: legacy_client, raising=False)

    refreshed = client._refresh_user_access_token_sync("ur-old")

    legacy_client.authen.v1.refresh_access_token.create.assert_called_once()
    assert refreshed == FeishuRefreshedToken(
        access_token="u-new",
        refresh_token="ur-new",
        expires_in=7200,
    )


def test_get_tenant_access_token_uses_configured_app(monkeypatch):
    seen = {}

    def fake_get_token(config):
        seen["config"] = config
        return " t-test "

    monkeypatch.setattr(
        "lark_oapi.core.token.TokenManager.get_self_tenant_token",
        fake_get_token,
    )
    client = FeishuOAuthClient(
        FeishuAppCredentials(
            app_id="cli-test",
            app_secret="secret-test",
            domain="https://open.feishu.cn",
            request_timeout=12,
        )
    )

    assert client._get_tenant_access_token_sync() == "t-test"
    assert seen["config"].app_id == "cli-test"
    assert seen["config"].app_secret == "secret-test"
    assert seen["config"].domain == "https://open.feishu.cn"
    assert seen["config"].timeout == 12
