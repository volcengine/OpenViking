# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP wire-result tests for tool execution failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _set_mcp_identity():
    context = RequestContext(
        user=UserIdentifier.the_default_user("mcp-error-test"),
        role=Role.ROOT,
    )
    token = mcp_endpoint._mcp_ctx.set(context)
    yield
    mcp_endpoint._mcp_ctx.reset(token)


async def _call_tool(name: str, arguments: dict) -> dict:
    handler = mcp_endpoint.mcp._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(
        params=CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root.model_dump(by_alias=True, exclude_none=True)


@pytest.mark.parametrize(
    ("name", "arguments", "service", "message"),
    [
        pytest.param(
            "add_resource",
            {"path": "space:home", "add_type": "feishu"},
            SimpleNamespace(),
            "Error: add_type requires an exact 'to' target.",
            id="validation",
        ),
        pytest.param(
            "list_watches",
            {},
            SimpleNamespace(watch_scheduler=None),
            "Error: Watch scheduler not running",
            id="watch-list-unavailable",
        ),
        pytest.param(
            "cancel_watch",
            {"to_uri": "viking://resources/project"},
            SimpleNamespace(watch_scheduler=None),
            "Error: Watch scheduler not running",
            id="watch-cancel-unavailable",
        ),
        pytest.param(
            "glob",
            {"pattern": "**/*.md"},
            SimpleNamespace(
                fs=SimpleNamespace(glob=AsyncMock(side_effect=RuntimeError("storage offline")))
            ),
            "Error: storage offline",
            id="glob-backend",
        ),
    ],
)
async def test_whole_call_failure_sets_error_result(monkeypatch, name, arguments, service, message):
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)

    direct_result = await getattr(mcp_endpoint, name)(**arguments)
    result = await _call_tool(name, arguments)

    assert type(direct_result) is str
    assert direct_result == message
    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": message}]
    assert result["structuredContent"] == {"result": message}


async def test_read_all_failures_set_error_result(monkeypatch):
    read_visible = AsyncMock(side_effect=InvalidArgumentError("not readable"))
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(read_visible=read_visible)),
    )

    arguments = {
        "uris": ["viking://resources/a.md", "viking://resources/b.md"],
    }
    direct_result = await mcp_endpoint.read(**arguments)
    result = await _call_tool(
        "read",
        arguments,
    )

    assert type(direct_result) is str
    assert direct_result == (
        "=== viking://resources/a.md ===\nnot readable\n\n"
        "=== viking://resources/b.md ===\nnot readable"
    )
    assert result["isError"] is True
    assert "not readable" in result["content"][0]["text"]
    assert "structuredContent" not in result


async def test_read_partial_failure_remains_a_success_result(monkeypatch):
    async def read_visible(uri, **kwargs):
        if uri.endswith("bad.md"):
            raise InvalidArgumentError("not readable")
        return "visible content"

    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(read_visible=read_visible)),
    )

    arguments = {
        "uris": [
            "viking://resources/good.md",
            "viking://resources/bad.md",
        ]
    }
    direct_result = await mcp_endpoint.read(**arguments)
    result = await _call_tool(
        "read",
        arguments,
    )

    assert type(direct_result) is str
    assert "visible content" in direct_result
    assert "not readable" in direct_result
    assert result["isError"] is False
    assert "visible content" in result["content"][0]["text"]
    assert "not readable" in result["content"][0]["text"]


async def test_health_unavailable_remains_diagnostic_data(monkeypatch):
    def unavailable():
        raise RuntimeError("not initialized")

    monkeypatch.setattr(mcp_endpoint, "get_service", unavailable)

    result = await _call_tool("health", {})

    assert result["isError"] is False
    assert result["content"] == [
        {"type": "text", "text": "OpenViking is unhealthy: not initialized"}
    ]
