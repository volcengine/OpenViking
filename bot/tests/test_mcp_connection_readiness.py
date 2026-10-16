# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for VikingBot MCP connection readiness."""

import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.mcp import connect_mcp_servers


def _make_loop():
    loop = object.__new__(AgentLoop)
    loop._mcp_servers = {"demo": SimpleNamespace()}
    loop._mcp_pending_servers = dict(loop._mcp_servers)
    loop._mcp_stack = None
    loop._mcp_connected = False
    loop._mcp_connect_lock = asyncio.Lock()
    loop.tools = SimpleNamespace(ready=False)
    return loop


@pytest.mark.asyncio
async def test_concurrent_mcp_callers_wait_for_connection(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def connect_mcp_servers(_servers, registry, _stack):
        nonlocal attempts
        attempts += 1
        started.set()
        await release.wait()
        registry.ready = True
        return set()

    monkeypatch.setattr(
        "vikingbot.agent.tools.mcp.connect_mcp_servers",
        connect_mcp_servers,
    )

    loop = _make_loop()

    async def connect_and_observe():
        await loop._connect_mcp()
        return loop.tools.ready

    first = asyncio.create_task(connect_and_observe())
    await started.wait()
    second = asyncio.create_task(connect_and_observe())
    await asyncio.sleep(0)

    assert not second.done()

    release.set()
    assert await asyncio.gather(first, second) == [True, True]
    assert attempts == 1


@pytest.mark.asyncio
async def test_failed_mcp_connection_remains_retryable(monkeypatch):
    attempted_servers = []

    async def connect_mcp_servers(servers, registry, _stack):
        attempted_servers.append(tuple(servers))
        if len(attempted_servers) == 1:
            return {"late"}
        registry.ready = True
        return set()

    monkeypatch.setattr(
        "vikingbot.agent.tools.mcp.connect_mcp_servers",
        connect_mcp_servers,
    )
    loop = _make_loop()
    loop._mcp_servers["late"] = SimpleNamespace()
    loop._mcp_pending_servers = dict(loop._mcp_servers)

    await loop._connect_mcp()

    assert not loop._mcp_connected
    assert not loop.tools.ready

    await loop._connect_mcp()

    assert loop._mcp_connected
    assert loop.tools.ready
    assert attempted_servers == [("demo", "late"), ("late",)]


@pytest.mark.asyncio
async def test_failed_mcp_server_reports_retry_and_closes_partial_transport(monkeypatch):
    http_client = SimpleNamespace(closed=False)

    class HttpClientContext:
        async def __aenter__(self):
            return http_client

        async def __aexit__(self, *_args):
            http_client.closed = True

    class RefusedTransport:
        async def __aenter__(self):
            raise ConnectionRefusedError("server is not ready")

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(
        "vikingbot.agent.tools.mcp.httpx.AsyncClient", lambda **_kwargs: HttpClientContext()
    )
    monkeypatch.setattr(
        "mcp.client.streamable_http.streamable_http_client",
        lambda *_args, **_kwargs: RefusedTransport(),
    )
    config = SimpleNamespace(
        type="streamableHttp",
        url="http://127.0.0.1:27182/mcp",
        headers={},
    )

    async with AsyncExitStack() as stack:
        failed_servers = await connect_mcp_servers({"demo": config}, SimpleNamespace(), stack)

    assert failed_servers == {"demo"}
    assert http_client.closed
