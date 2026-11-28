# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for issue #5830: VikingBot MCP sessions must report an
OpenViking-owned client identity instead of the embedded mcp SDK default.

The original behavior was that ``connect_mcp_servers`` constructed
``ClientSession(read, write)`` without a ``client_info`` argument, so the locked
MCP SDK 1.27.0 sent ``{"name": "mcp", "version": "0.1.0"}`` to every server.
After the fix, VikingBot should send ``{"name": "openviking-vikingbot",
"version": <installed openviking version>}`` to every server across all three
supported transports (stdio / sse / streamableHttp).
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import vikingbot
from vikingbot import __version__ as _VKB_VERSION
from vikingbot.agent.tools.mcp import (
    _MCP_CLIENT_INFO_NAME,
    connect_mcp_servers,
)
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.config.schema import MCPServerConfig


def _install_fake_transport(monkeypatch, transport_type: str) -> None:
    """Patch the three MCP transport factories at their real import sites.

    The real factories are async context managers that yield ``(read, write)``
    (stdio / sse) or ``(read, write, get_session_id)`` (streamable_http).
    Replace each with an async context manager that yields matching tuples so
    ``stack.enter_async_context(...)`` can drive them.
    """

    if transport_type == "stdio":

        @asynccontextmanager
        async def _fake_stdio_client(params):
            yield MagicMock(name="read"), MagicMock(name="write")

        monkeypatch.setattr("mcp.client.stdio.stdio_client", _fake_stdio_client)
        return

    if transport_type == "sse":

        @asynccontextmanager
        async def _fake_sse_client(url, httpx_client_factory=None):
            yield MagicMock(name="read"), MagicMock(name="write")

        monkeypatch.setattr("mcp.client.sse.sse_client", _fake_sse_client)
        return

    @asynccontextmanager
    async def _fake_streamable_http_client(url, http_client=None):
        yield (
            MagicMock(name="read"),
            MagicMock(name="write"),
            MagicMock(name="get_session_id"),
        )

    monkeypatch.setattr(
        "mcp.client.streamable_http.streamable_http_client",
        _fake_streamable_http_client,
    )


def _install_fake_client_session(monkeypatch) -> dict[str, object]:
    """Replace ``mcp.ClientSession`` with a recorder that captures init kwargs."""

    captured: dict[str, object] = {}

    class _FakeClientSession:
        def __init__(self, read, write, *args, **kwargs):
            captured["read"] = read
            captured["write"] = write
            captured["args"] = args
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            captured["initialized"] = True
            return None

        async def list_tools(self):
            captured.setdefault("list_tools", True)
            return SimpleNamespace(tools=[])

    monkeypatch.setattr("mcp.ClientSession", _FakeClientSession)
    return captured


@pytest.mark.parametrize(
    "transport_type",
    ["stdio", "sse", "streamableHttp"],
)
def test_connect_mcp_servers_sends_openviking_client_identity(monkeypatch, transport_type):
    """Each transport must pass an OpenViking-owned ``client_info`` to ClientSession."""

    captured = _install_fake_client_session(monkeypatch)
    _install_fake_transport(monkeypatch, transport_type)

    cfg = MCPServerConfig(
        type=transport_type,
        command="ignored-stdio-command" if transport_type == "stdio" else "",
        args=[],
        url="http://localhost:0/mcp" if transport_type != "stdio" else "",
        enabled_tools=["*"],
    )

    async def _run():
        async with AsyncExitStack() as stack:
            registry = ToolRegistry()
            await connect_mcp_servers({"fixture": cfg}, registry, stack)

    asyncio.run(_run())

    assert captured.get("initialized") is True, (
        f"ClientSession.initialize was never called for {transport_type!r}"
    )
    kwargs = captured["kwargs"]
    assert "client_info" in kwargs, (
        f"connect_mcp_servers did not pass client_info for {transport_type!r}: "
        f"got kwargs={kwargs!r}"
    )
    client_info = kwargs["client_info"]
    # Implementation is a Pydantic model; compare by attribute access so the
    # test does not couple to the SDK's internal repr or constructor identity.
    assert getattr(client_info, "name", None) == "openviking-vikingbot"
    assert getattr(client_info, "version", None) == _VKB_VERSION
    # vikingbot.__version__ resolves to the installed openviking package version.
    assert _VKB_VERSION == vikingbot.__version__


def test_module_level_client_info_name_is_stable():
    """The MCP client name is part of the contract — guard against accidental edits."""

    assert _MCP_CLIENT_INFO_NAME == "openviking-vikingbot"
