# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Real MCP v2 transport contract tests for the mounted OpenViking endpoint."""

from types import SimpleNamespace

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

import openviking
import openviking.server.dependencies as dependencies
from openviking.server.app import create_app
from openviking.server.auth.plugins.trusted import TrustedAuthPlugin
from openviking.server.config import ServerConfig
from openviking.server.mcp_endpoint import mcp_lifespan

_DEFAULT_MCP_BODY_LIMIT = 4 * 1024 * 1024
_CUSTOM_MCP_BODY_LIMIT = 5 * 1024 * 1024
_ROOT_API_KEY = "mcp-transport-test-root-key"
_AUTH_HEADERS = {
    "X-API-Key": _ROOT_API_KEY,
    "X-OpenViking-Account": "transport-test-account",
    "X-OpenViking-User": "transport-test-user",
    "X-OpenViking-Role": "user",
}


def _make_app(config: ServerConfig, service):
    app = create_app(config=config, service=service)
    app.state.auth_plugin = TrustedAuthPlugin()
    return app


def _transport(app, headers):
    http_client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        headers=headers,
    )
    return http_client, streamable_http_client(
        "http://testserver/mcp",
        http_client=http_client,
    )


@pytest.mark.asyncio
async def test_mounted_mcp_transport_contract(monkeypatch):
    writes = []

    async def write(**kwargs):
        writes.append(kwargs)
        return {
            "uri": kwargs["uri"],
            "mode": kwargs["mode"],
            "written_bytes": len(kwargs["content"].encode("utf-8")),
            "semantic_status": "skipped",
            "vector_status": "skipped",
        }

    service = SimpleNamespace(
        fs=SimpleNamespace(write=write),
        sessions=None,
    )
    monkeypatch.setattr(dependencies, "_service", service)

    default_app = _make_app(
        ServerConfig(auth_mode="trusted", root_api_key=_ROOT_API_KEY),
        service,
    )
    custom_app = _make_app(
        ServerConfig(
            auth_mode="trusted",
            root_api_key=_ROOT_API_KEY,
            mcp_max_request_body_size_bytes=_CUSTOM_MCP_BODY_LIMIT,
        ),
        service,
    )

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=default_app),
        base_url="http://testserver",
    ) as raw_client:
        unauthenticated = await raw_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}},
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == -32001

        oversized = await raw_client.post(
            "/mcp",
            content=b"x" * (_DEFAULT_MCP_BODY_LIMIT + 1),
            headers=_AUTH_HEADERS,
        )
        assert oversized.status_code == 413

    large_content = "x" * (_DEFAULT_MCP_BODY_LIMIT + 1024)
    async with mcp_lifespan():
        modern_http, modern_transport = _transport(custom_app, _AUTH_HEADERS)
        async with modern_http:
            async with Client(modern_transport, mode="auto", cache=None) as modern_client:
                assert modern_client.protocol_version == "2026-07-28"
                tools = await modern_client.list_tools()
                assert len(tools.tools) == 16

                result = await modern_client.call_tool(
                    "write",
                    {
                        "uri": "viking://~/notes.md",
                        "content": large_content,
                    },
                )
                assert result.is_error is False

        legacy_http, legacy_transport = _transport(custom_app, _AUTH_HEADERS)
        async with legacy_http:
            async with Client(legacy_transport, mode="legacy", cache=None) as legacy_client:
                assert legacy_client.server_info.version == openviking.__version__

    assert len(writes) == 1
    assert writes[0]["uri"] == "viking://user/transport-test-user/notes.md"
    assert writes[0]["ctx"].account_id == "transport-test-account"
    assert writes[0]["ctx"].user.user_id == "transport-test-user"
    assert writes[0]["ctx"].role == "user"
    assert writes[0]["content"] == large_content
