# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_headers", "url", "expected_tokens"),
    [
        ({}, None, [None, "configured-token"]),
        ({"x-gateway-token": "manual-token"}, None, ["manual-token"]),
        ({}, "http://other.example", [None]),
    ],
    ids=["configured-gateway", "explicit-header", "different-server"],
)
async def test_async_http_client_retries_gateway_token_only_after_marked_challenge(
    tmp_path, monkeypatch, sdk_transport, extra_headers, url, expected_tokens
):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://gateway.example/",
                "gateway_token": "configured-token",
                "extra_headers": extra_headers,
            }
        )
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    tokens = []

    def handle(request):
        tokens.append(request.headers.get("X-Gateway-Token"))
        if tokens[-1] == "configured-token":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            401,
            headers={"X-VikingBot-Gateway": "true"},
            json={"detail": "X-Gateway-Token header required"},
        )

    sdk_transport(handle)
    client = AsyncHTTPClient(url=url)
    await client.initialize()
    try:
        assert await client.health() is (url is None and not extra_headers)
    finally:
        await client.close()

    assert tokens == expected_tokens


@pytest.mark.asyncio
async def test_async_http_client_does_not_retry_unmarked_unauthorized_response(
    tmp_path, monkeypatch, sdk_transport
):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(
        json.dumps({"url": "http://gateway.example", "gateway_token": "configured-token"})
    )
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            401,
            json={
                "status": "error",
                "error": {"code": "UNAUTHENTICATED", "message": "Invalid key"},
            },
        )

    sdk_transport(handle)
    client = AsyncHTTPClient()
    await client.initialize()
    try:
        assert not await client.health()
    finally:
        await client.close()

    (request,) = requests
    assert "X-Gateway-Token" not in request.headers


@pytest.mark.parametrize(
    ("config", "error"),
    [
        ({"timeout": "fast"}, r"Invalid value for 'ovcli\.timeout'"),
        ({"ur": "http://localhost:1933"}, r"ovcli\.ur'.*ovcli\.url"),
        ({"upload": {"unknown": "value"}}, r"ovcli\.upload\.unknown"),
        (
            {"actor_peer_id": "actor-a", "agent_id": "legacy-agent"},
            "actor_peer_id cannot be used with agent_id",
        ),
    ],
    ids=["timeout-type", "top-level-typo", "nested-typo", "conflicting-peers"],
)
def test_async_http_client_reports_invalid_ovcli_config(tmp_path, monkeypatch, config, error):
    config_path = tmp_path / "ovcli.conf"
    config_path.write_text(json.dumps({"url": "http://localhost:1933", **config}))
    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(config_path))

    with pytest.raises(ValueError, match=error):
        AsyncHTTPClient()


def test_async_http_client_rejects_explicit_actor_peer_id_and_agent_id_together():
    with pytest.raises(ValueError, match="actor_peer_id cannot be used with agent_id"):
        AsyncHTTPClient(
            url="http://explicit-host:1933",
            actor_peer_id="actor-a",
            agent_id="legacy-agent",
        )
