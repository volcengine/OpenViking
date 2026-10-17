# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP client identity tests for VikingBot outbound connections."""

from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import mcp
import mcp.client.sse
import mcp.client.stdio
import mcp.client.streamable_http
import vikingbot.agent.tools.mcp as mcp_tools
from vikingbot import __version__


@asynccontextmanager
async def _pair_transport(*_args, **_kwargs):
    yield object(), object()


@asynccontextmanager
async def _triple_transport(*_args, **_kwargs):
    yield object(), object(), None


class _HTTPClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


async def test_all_mcp_transports_report_vikingbot_version(monkeypatch):
    captured_client_info = []

    class CapturingSession:
        def __init__(self, _read, _write, *, client_info):
            captured_client_info.append(client_info)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    monkeypatch.setattr(mcp, "ClientSession", CapturingSession)
    monkeypatch.setattr(mcp.client.stdio, "stdio_client", _pair_transport)
    monkeypatch.setattr(mcp.client.sse, "sse_client", _pair_transport)
    monkeypatch.setattr(mcp.client.streamable_http, "streamable_http_client", _triple_transport)
    monkeypatch.setattr(mcp_tools.httpx, "AsyncClient", _HTTPClient)

    base = {
        "command": "fixture",
        "args": [],
        "env": {},
        "url": None,
        "headers": {},
        "enabled_tools": ["*"],
        "tool_timeout": 30,
    }
    servers = {
        "stdio": SimpleNamespace(**base, type="stdio"),
        "sse": SimpleNamespace(**base, type="sse"),
        "http": SimpleNamespace(**base, type="streamableHttp"),
    }
    registry = SimpleNamespace(register=lambda _tool: None)

    async with AsyncExitStack() as stack:
        await mcp_tools.connect_mcp_servers(servers, registry, stack)

    assert [(info.name, info.version) for info in captured_client_info] == [
        ("openviking-vikingbot", __version__),
        ("openviking-vikingbot", __version__),
        ("openviking-vikingbot", __version__),
    ]
