# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for VikingBot MCP connection readiness."""

import asyncio
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop


def _make_loop():
    loop = object.__new__(AgentLoop)
    loop._mcp_servers = {"demo": SimpleNamespace()}
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
    attempts = 0

    async def connect_mcp_servers(_servers, registry, _stack):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("controlled connection failure")
        registry.ready = True

    monkeypatch.setattr(
        "vikingbot.agent.tools.mcp.connect_mcp_servers",
        connect_mcp_servers,
    )
    loop = _make_loop()

    await loop._connect_mcp()

    assert not loop._mcp_connected
    assert not loop.tools.ready

    await loop._connect_mcp()

    assert loop._mcp_connected
    assert loop.tools.ready
    assert attempts == 2
