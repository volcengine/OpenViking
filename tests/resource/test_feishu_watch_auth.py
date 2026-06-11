# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Feishu watch auth helpers."""

import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace

from openviking.resource.feishu_watch_auth import (
    FEISHU_REFRESH_GRANT_TYPE,
    FeishuAppCredentials,
    FeishuOAuthClient,
    FeishuRefreshedToken,
    apply_feishu_refreshed_token,
    create_feishu_auth_state,
    feishu_auth_state_needs_refresh,
)


class _FakeRefreshAccessTokenRequest:
    @staticmethod
    def builder():
        return _FakeRefreshAccessTokenRequestBuilder()


class _FakeRefreshAccessTokenRequestBuilder:
    def __init__(self):
        self._request = SimpleNamespace(body=None)

    def request_body(self, body):
        self._request.body = body
        return self

    def build(self):
        return self._request


class _FakeRefreshAccessTokenRequestBody:
    @staticmethod
    def builder():
        return _FakeRefreshAccessTokenRequestBodyBuilder()


class _FakeRefreshAccessTokenRequestBodyBuilder:
    def __init__(self):
        self._body = SimpleNamespace(grant_type=None, refresh_token=None)

    def grant_type(self, grant_type):
        self._body.grant_type = grant_type
        return self

    def refresh_token(self, refresh_token):
        self._body.refresh_token = refresh_token
        return self

    def build(self):
        return self._body


class _SuccessRefreshResponse:
    code = 0
    msg = ""

    def __init__(self):
        self.data = SimpleNamespace(
            access_token="u-new",
            refresh_token="r-new",
            expires_in=7200,
        )

    @staticmethod
    def success():
        return True


class _FakeRefreshAccessToken:
    def __init__(self):
        self.request = None

    def create(self, request):
        self.request = request
        return _SuccessRefreshResponse()


def test_feishu_oauth_client_refreshes_user_access_token_with_sdk_request(monkeypatch):
    authen_v1 = ModuleType("lark_oapi.api.authen.v1")
    authen_v1.CreateRefreshAccessTokenRequest = _FakeRefreshAccessTokenRequest
    authen_v1.CreateRefreshAccessTokenRequestBody = _FakeRefreshAccessTokenRequestBody
    monkeypatch.setitem(sys.modules, "lark_oapi.api.authen.v1", authen_v1)

    refresh_access_token = _FakeRefreshAccessToken()
    client = FeishuOAuthClient(
        FeishuAppCredentials(
            app_id="app-id",
            app_secret="app-secret",
            domain="https://open.feishu.cn",
            request_timeout=30.0,
        )
    )
    client._client = SimpleNamespace(
        authen=SimpleNamespace(
            v1=SimpleNamespace(refresh_access_token=refresh_access_token),
        )
    )

    refreshed = client._refresh_user_access_token_sync("r-old")

    assert refreshed == FeishuRefreshedToken(
        access_token="u-new",
        refresh_token="r-new",
        expires_in=7200,
    )
    assert refresh_access_token.request.body.grant_type == FEISHU_REFRESH_GRANT_TYPE
    assert refresh_access_token.request.body.refresh_token == "r-old"


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
