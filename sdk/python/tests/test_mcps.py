# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient


@pytest.mark.asyncio
async def test_async_http_client_exposes_all_mcp_operations():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(request=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {"ok": True}

    assert await client.add_mcp(
        "demo",
        "https://mcp.example.com/api",
        headers={"Authorization": "Bearer secret"},
        wait=True,
    ) == {"ok": True}
    await client.remove_mcp("demo/server")
    await client.list_mcps()
    await client.find_mcp_tools("weather forecast", limit=5)
    await client.validate_mcp("demo", "https://mcp.example.com/api")
    await client.get_mcp("demo/server")
    await client.sync_mcp("demo/server", wait=True)
    await client.get_mcp_status("demo")
    await client.invoke_mcp_tool(
        "agent/tools/demo/current.json",
        arguments={"city": "Beijing"},
    )
    await client.mount_mcp_tool(
        "agent/tools/demo/current.json",
        "resources/demo/weather.md",
        "Beijing Weather",
        description="Current weather in Beijing",
        timeout_seconds=120,
        arguments={"city": "Beijing"},
        tags=["project=demo", "kind=live-context"],
        wait=True,
    )
    await client.batch_mount_mcp_tools(
        [
            {
                "tool_uri": "agent/tools/demo/current.json",
                "node_uri": "resources/demo/batch.md",
                "title": "Batch Weather",
                "tags": ["project=demo"],
            }
        ],
        wait=True,
    )
    await client.unmount_mcp_tool("resources/demo/weather.md", wait=True)
    await client.batch_unmount_mcp_tools(
        ["resources/demo/batch.md"],
        wait=True,
    )
    await client.list_mcp_mounts("demo", tool="current")

    assert fake_http.request.await_args_list == [
        call(
            "POST",
            "/api/v1/mcps",
            json={
                "name": "demo",
                "url": "https://mcp.example.com/api",
                "headers": {"Authorization": "Bearer secret"},
                "wait": True,
            },
        ),
        call("DELETE", "/api/v1/mcps/demo%2Fserver"),
        call("GET", "/api/v1/mcps"),
        call(
            "POST",
            "/api/v1/mcps/find",
            json={"query": "weather forecast", "limit": 5},
        ),
        call(
            "POST",
            "/api/v1/mcps/validate",
            json={
                "name": "demo",
                "url": "https://mcp.example.com/api",
                "headers": {},
            },
        ),
        call("GET", "/api/v1/mcps/demo%2Fserver"),
        call(
            "POST",
            "/api/v1/mcps/demo%2Fserver/sync",
            json={"wait": True},
        ),
        call("GET", "/api/v1/mcps/status", params={"name": "demo"}),
        call(
            "POST",
            "/api/v1/mcps/invoke",
            json={
                "uri": "viking://agent/tools/demo/current.json",
                "arguments": {"city": "Beijing"},
            },
        ),
        call(
            "POST",
            "/api/v1/mcps/mount",
            json={
                "tool_uri": "viking://agent/tools/demo/current.json",
                "node_uri": "viking://resources/demo/weather.md",
                "title": "Beijing Weather",
                "description": "Current weather in Beijing",
                "timeout_seconds": 120,
                "arguments": {"city": "Beijing"},
                "tags": ["project=demo", "kind=live-context"],
                "wait": True,
            },
        ),
        call(
            "POST",
            "/api/v1/mcps/batch-mount",
            json={
                "mounts": [
                    {
                        "tool_uri": "viking://agent/tools/demo/current.json",
                        "node_uri": "viking://resources/demo/batch.md",
                        "title": "Batch Weather",
                        "description": None,
                        "timeout_seconds": 60,
                        "arguments": {},
                        "tags": ["project=demo"],
                    }
                ],
                "wait": True,
            },
        ),
        call(
            "DELETE",
            "/api/v1/mcps/mount",
            params={
                "node_uri": "viking://resources/demo/weather.md",
                "wait": True,
            },
        ),
        call(
            "POST",
            "/api/v1/mcps/batch-unmount",
            json={
                "node_uris": ["viking://resources/demo/batch.md"],
                "wait": True,
            },
        ),
        call(
            "GET",
            "/api/v1/mcps/mounts",
            params={"mcp": "demo", "tool": "current"},
        ),
    ]


@pytest.mark.asyncio
async def test_async_http_client_mcp_defaults_match_server_contract():
    client = AsyncHTTPClient(url="http://localhost:1933")
    fake_http = SimpleNamespace(request=AsyncMock(return_value=object()))
    client._http = fake_http
    client._handle_response = lambda _response: {}

    await client.invoke_mcp_tool("viking://agent/tools/demo/current.json")
    await client.mount_mcp_tool(
        "viking://agent/tools/demo/current.json",
        "viking://resources/demo/weather.md",
        "Weather",
    )
    await client.batch_mount_mcp_tools(
        [
            {
                "tool_uri": "viking://agent/tools/demo/current.json",
                "node_uri": "viking://resources/demo/batch.md",
                "title": "Batch Weather",
            }
        ]
    )
    await client.unmount_mcp_tool("viking://resources/demo/weather.md")
    await client.batch_unmount_mcp_tools(["viking://resources/demo/batch.md"])
    await client.list_mcp_mounts("demo")

    assert fake_http.request.await_args_list == [
        call(
            "POST",
            "/api/v1/mcps/invoke",
            json={
                "uri": "viking://agent/tools/demo/current.json",
                "arguments": {},
            },
        ),
        call(
            "POST",
            "/api/v1/mcps/mount",
            json={
                "tool_uri": "viking://agent/tools/demo/current.json",
                "node_uri": "viking://resources/demo/weather.md",
                "title": "Weather",
                "description": None,
                "timeout_seconds": 60,
                "arguments": {},
                "wait": False,
            },
        ),
        call(
            "POST",
            "/api/v1/mcps/batch-mount",
            json={
                "mounts": [
                    {
                        "tool_uri": "viking://agent/tools/demo/current.json",
                        "node_uri": "viking://resources/demo/batch.md",
                        "title": "Batch Weather",
                        "description": None,
                        "timeout_seconds": 60,
                        "arguments": {},
                    }
                ],
                "wait": False,
            },
        ),
        call(
            "DELETE",
            "/api/v1/mcps/mount",
            params={
                "node_uri": "viking://resources/demo/weather.md",
                "wait": False,
            },
        ),
        call(
            "POST",
            "/api/v1/mcps/batch-unmount",
            json={
                "node_uris": ["viking://resources/demo/batch.md"],
                "wait": False,
            },
        ),
        call("GET", "/api/v1/mcps/mounts", params={"mcp": "demo"}),
    ]


def test_sync_http_client_exposes_and_forwards_mcp_operations():
    method_names = [
        "add_mcp",
        "remove_mcp",
        "list_mcps",
        "find_mcp_tools",
        "validate_mcp",
        "get_mcp",
        "sync_mcp",
        "get_mcp_status",
        "invoke_mcp_tool",
        "mount_mcp_tool",
        "batch_mount_mcp_tools",
        "unmount_mcp_tool",
        "batch_unmount_mcp_tools",
        "list_mcp_mounts",
    ]
    assert all(name in SyncHTTPClient.__dict__ for name in method_names)

    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client.mount_mcp_tool = AsyncMock(return_value={"mounted": True})

    result = client.mount_mcp_tool(
        "viking://agent/tools/demo/current.json",
        "viking://resources/demo/weather.md",
        "Weather",
        description="Current weather",
        timeout_seconds=90,
        arguments={"city": "Beijing"},
        tags=["project=demo"],
        wait=True,
    )

    assert result == {"mounted": True}
    client._async_client.mount_mcp_tool.assert_awaited_once_with(
        "viking://agent/tools/demo/current.json",
        "viking://resources/demo/weather.md",
        "Weather",
        description="Current weather",
        timeout_seconds=90,
        arguments={"city": "Beijing"},
        tags=["project=demo"],
        wait=True,
    )
