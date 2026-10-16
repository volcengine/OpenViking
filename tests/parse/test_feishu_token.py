# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for account-isolated Feishu tenant-token caching."""

import json

from openviking.parse.accessors.feishu_token import resolve_feishu_tenant_token_cache


class RecordingCache:
    def __init__(self):
        self.values = {}
        self.keys = []

    def get(self, key):
        self.keys.append(key)
        return self.values.get(key)

    def set(self, key, value, expire):
        self.keys.append(key)
        self.values[key] = value


def test_sdk_tenant_token_cache_isolated_by_full_credentials(monkeypatch):
    from lark_oapi.core.http import Transport
    from lark_oapi.core.model import Config, RawResponse
    from lark_oapi.core.token import TokenManager

    calls = []

    def fake_execute(config, request):
        calls.append((config.app_id, config.app_secret, request.uri))
        response = RawResponse()
        response.status_code = 200
        response.content = json.dumps(
            {
                "code": 0,
                "tenant_access_token": f"token-for-{config.app_secret}",
                "expire": 7200,
            }
        ).encode()
        return response

    monkeypatch.setattr(Transport, "execute", fake_execute)
    cache = RecordingCache()
    scoped_cache = resolve_feishu_tenant_token_cache(cache)
    monkeypatch.setattr(TokenManager, "cache", TokenManager.cache)

    account_a = Config()
    account_a.app_id = "shared-app"
    account_a.app_secret = "secret-a"
    account_a.domain = "https://open.feishu.cn"
    account_b = Config()
    account_b.app_id = "shared-app"
    account_b.app_secret = "secret-b"
    account_b.domain = "https://open.feishu.cn"

    with scoped_cache.sdk_scope(
        app_id=account_a.app_id,
        app_secret=account_a.app_secret,
        domain=account_a.domain,
    ):
        assert TokenManager.cache is scoped_cache
        assert TokenManager.get_self_tenant_token(account_a) == "token-for-secret-a"
    with scoped_cache.sdk_scope(
        app_id=account_b.app_id,
        app_secret=account_b.app_secret,
        domain=account_b.domain,
    ):
        assert TokenManager.cache is scoped_cache
        assert TokenManager.get_self_tenant_token(account_b) == "token-for-secret-b"
    with scoped_cache.sdk_scope(
        app_id=account_a.app_id,
        app_secret=account_a.app_secret,
        domain=account_a.domain,
    ):
        assert TokenManager.get_self_tenant_token(account_a) == "token-for-secret-a"
    assert TokenManager.cache is scoped_cache
    assert calls == [
        ("shared-app", "secret-a", "/open-apis/auth/v3/tenant_access_token/internal"),
        ("shared-app", "secret-b", "/open-apis/auth/v3/tenant_access_token/internal"),
    ]
    assert len(cache.values) == 2
    assert all("secret-" not in key for key in cache.keys)
