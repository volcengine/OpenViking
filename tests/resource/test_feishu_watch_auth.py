# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Feishu watch auth helpers."""

from datetime import datetime, timedelta, timezone

from openviking.resource.feishu_watch_auth import (
    FeishuAppCredentials,
    FeishuOAuthClient,
    FeishuRefreshedToken,
    apply_feishu_refreshed_token,
    create_feishu_auth_state,
    feishu_auth_state_needs_refresh,
)


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
