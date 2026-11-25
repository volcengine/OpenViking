# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx
import pytest

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.retrieve.types import ContextType
from openviking_cli.utils.config import OPENVIKING_CLI_CONFIG_ENV


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / "ovcli.conf"
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(path))
    for var in (
        "OPENVIKING_TIMEOUT",
        "OPENVIKING_ACCOUNT",
        "OPENVIKING_USER",
        "OPENVIKING_ACTOR_PEER_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    return path


@pytest.fixture
def requests(monkeypatch):
    captured = []
    http_client = httpx.AsyncClient

    def handle(request):
        captured.append(request)
        return httpx.Response(200, json={"status": "ok", "result": {}})

    def create(**kwargs):
        return http_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", create)
    return captured


async def test_legacy_client_uses_default_timeout_without_sending_unset_identity(requests):
    client = AsyncHTTPClient(url="http://localhost:1933", api_key="api-key")
    await client.initialize()
    try:
        assert await client.health()
    finally:
        await client.close()

    (request,) = requests
    assert request.headers["X-API-Key"] == "api-key"
    assert not any(name.startswith("x-openviking-") for name in request.headers)
    assert request.extensions["timeout"]["read"] == 180.0


@pytest.mark.parametrize(
    ("additional_headers", "header_value"),
    [
        ({}, "from-alias"),
        ({"extra_headers": {"X-Custom": "from-main-field"}}, "from-main-field"),
    ],
    ids=["singular-alias", "plural-wins"],
)
async def test_legacy_config_aliases_are_applied_to_requests(
    isolated_config, requests, additional_headers, header_value
):
    isolated_config.write_text(
        json.dumps(
            {
                "url": "http://localhost:1933",
                "agent_id": "legacy-agent",
                "extra_header": {"X-Custom": "from-alias"},
                "timeout": 12.5,
                **additional_headers,
            }
        )
    )

    client = AsyncHTTPClient(user_id="legacy-user")
    await client.initialize()
    try:
        assert await client.health()
    finally:
        await client.close()

    (request,) = requests
    assert request.headers["X-OpenViking-Actor-Peer"] == "legacy-agent"
    assert request.headers["X-OpenViking-User"] == "legacy-user"
    assert "X-OpenViking-Agent" not in request.headers
    assert request.headers["X-Custom"] == header_value
    assert request.extensions["timeout"]["read"] == 12.5


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
async def test_legacy_gateway_token_stays_bound_to_configured_server(
    isolated_config, requests, encoding
):
    isolated_config.write_text(
        json.dumps(
            {
                "url": "http://config-host:1933",
                "api_key": "config-key",
                "gateway_token": "gateway-token",
                "account": "config-account",
                "user": "config-user",
                "actor_peer_id": "config-actor",
                "timeout": 12.5,
                "profile": True,
            }
        ),
        encoding=encoding,
    )
    client = AsyncHTTPClient(url="http://explicit-host:1933")
    await client.initialize()
    try:
        assert await client.health()
    finally:
        await client.close()

    (request,) = requests
    assert request.url.host == "explicit-host"
    assert "X-Gateway-Token" not in request.headers
    assert request.headers["X-API-Key"] == "config-key"
    assert request.headers["X-OpenViking-Account"] == "config-account"
    assert request.headers["X-OpenViking-User"] == "config-user"
    assert request.headers["X-OpenViking-Actor-Peer"] == "config-actor"
    assert request.extensions["timeout"]["read"] == 12.5
    assert request.url.params["profile"] == "1"


async def test_async_http_client_search_scope_stays_separate_from_actor_identity(requests):
    client = AsyncHTTPClient(url="http://localhost:1933", agent_id="legacy-agent")
    await client.initialize()
    try:
        await client.find(
            "invoice",
            target_uri="viking://~/memories",
            options={"context_type": [ContextType.MEMORY, ContextType.RESOURCE]},
        )
        await client.search(
            "invoice",
            target_uri="viking://~/memories",
            session_id="session-1",
            options={"context_type": "skill"},
        )
    finally:
        await client.close()

    find, search = requests
    assert find.headers["X-OpenViking-Actor-Peer"] == "legacy-agent"
    assert search.headers["X-OpenViking-Actor-Peer"] == "legacy-agent"
    assert find.url.path == "/api/v1/search/find"
    assert json.loads(find.content) == {
        "query": "invoice",
        "target_uri": "viking://~/memories",
        "limit": 10,
        "context_type": ["memory", "resource"],
    }
    assert search.url.path == "/api/v1/search/search"
    assert json.loads(search.content) == {
        "query": "invoice",
        "target_uri": "viking://~/memories",
        "session_id": "session-1",
        "limit": 10,
        "context_type": "skill",
    }


async def test_async_http_client_agent_identity_does_not_replace_message_peer(requests):
    client = AsyncHTTPClient(url="http://localhost:1933", agent_id="legacy-agent")
    await client.initialize()
    try:
        await client.add_message("session-1", "assistant", content="hello")
        await client.add_message("session-1", "user", content="hi", peer_id="speaker")
    finally:
        await client.close()

    for request in requests:
        assert request.url.path == "/api/v1/sessions/session-1/messages"
        assert request.headers["X-OpenViking-Actor-Peer"] == "legacy-agent"
    assistant, user = requests
    assert json.loads(assistant.content) == {"role": "assistant", "content": "hello"}
    assert json.loads(user.content) == {"role": "user", "content": "hi", "peer_id": "speaker"}
