# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient


@pytest.mark.asyncio
async def test_explicit_arguments_win_over_env(tmp_path, monkeypatch, sdk_transport):
    conf = tmp_path / "ovcli.conf"
    conf.write_text(
        json.dumps(
            {
                "url": "http://file-host:1933",
                "api_key": "file-key",
                "account": "file-account",
                "user": "file-user",
                "actor_peer_id": "file-actor",
                "timeout": 8,
                "profile": True,
                "extra_headers": {"X-Custom": "file", "X-File-Only": "file"},
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(conf))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    sdk_transport(handle)

    async def health(**kwargs):
        client = AsyncHTTPClient(**kwargs)
        await client.initialize()
        try:
            assert await client.health()
        finally:
            await client.close()
        return requests[-1]

    def assert_connection(request, source, timeout):
        assert request.url.host == f"{source}-host"
        assert request.headers["X-API-Key"] == f"{source}-key"
        assert request.headers["X-OpenViking-Account"] == f"{source}-account"
        assert request.headers["X-OpenViking-User"] == f"{source}-user"
        assert request.headers["X-OpenViking-Actor-Peer"] == f"{source}-actor"
        assert request.extensions["timeout"]["read"] == timeout

    file_request = await health()
    assert_connection(file_request, "file", 8)
    assert file_request.url.params["profile"] == "1"
    assert file_request.headers["X-Custom"] == "file"

    for field, value in {
        "URL": "http://env-host:1933",
        "API_KEY": "env-key",
        "ACCOUNT": "env-account",
        "USER": "env-user",
        "ACTOR_PEER_ID": "env-actor",
        "TIMEOUT": "12.5",
    }.items():
        monkeypatch.setenv(f"OPENVIKING_{field}", value)
    assert_connection(await health(), "env", 12.5)

    explicit_request = await health(
        url="http://explicit-host:1933",
        api_key="explicit-key",
        account="explicit-account",
        user="explicit-user",
        actor_peer_id="explicit-actor",
        timeout=60.0,
        profile_enabled=False,
        extra_headers={"X-Custom": "explicit"},
    )
    assert_connection(explicit_request, "explicit", 60.0)
    assert "profile" not in explicit_request.url.params
    assert explicit_request.headers["X-Custom"] == "explicit"
    assert "X-File-Only" not in explicit_request.headers


def test_url_is_required_when_missing_everywhere(monkeypatch):
    monkeypatch.delenv("OPENVIKING_URL", raising=False)

    with pytest.raises(ValueError, match="url is required"):
        AsyncHTTPClient()
