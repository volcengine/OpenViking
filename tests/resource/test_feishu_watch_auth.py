# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Feishu watch auth helpers."""

from datetime import datetime, timedelta, timezone

from openviking.resource.feishu_watch_auth import (
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
