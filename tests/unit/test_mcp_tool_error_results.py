# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP wire-result tests for tool execution failures."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
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


async def test_whole_call_failure_sets_error_result(monkeypatch):
    uri = "viking://resources/a.md"
    service = SimpleNamespace(
        fs=SimpleNamespace(
            write=AsyncMock(
                side_effect=PermissionDeniedError("write permission required", resource=uri)
            )
        )
    )
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    result = await _call_tool("write", {"uri": uri, "content": "denied"})
    assert result["isError"] is True
    assert result["content"] == [
        {"type": "text", "text": "PERMISSION_DENIED: write permission required"}
    ]
    assert result["structuredContent"]["error"] == {
        "code": "PERMISSION_DENIED",
        "message": "write permission required",
        "details": {"resource": uri},
    }


async def test_remote_resource_business_failure_sets_error_result(monkeypatch):
    service = SimpleNamespace(
        resources=SimpleNamespace(
            add_resource=AsyncMock(
                return_value={"status": "error", "message": "processor rejected"}
            )
        )
    )
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    arguments = {"path": "https://example.com/article"}

    direct_result = await mcp_endpoint.add_resource(**arguments)
    result = await _call_tool("add_resource", arguments)

    assert direct_result == "Error adding resource: processor rejected"
    assert type(direct_result) is str
    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": direct_result}]


async def test_invoke_mcp_tool_preserves_upstream_error_on_wire(monkeypatch):
    invocation = {
        "content": [{"type": "text", "text": "upstream failure"}],
        "isError": True,
    }
    invoke = AsyncMock(return_value=invocation)
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(mcps=SimpleNamespace(invoke=invoke)),
    )
    arguments = {
        "tool_uri": "viking://agent/tools/weather/current.json",
        "arguments": {"city": "Atlantis"},
    }

    result = await _call_tool("invoke_mcp_tool", arguments)

    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": "upstream failure"}]


async def test_gateway_find_to_invoke_without_read(monkeypatch):
    uri = "viking://agent/tools/demo/get-sum.json"
    definition = {
        "name": "get-sum",
        "title": "Get Sum Tool",
        "description": "Returns the sum of two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First number"},
                "b": {"type": "number", "description": "Second number"},
            },
            "required": ["a", "b"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "execution": {"taskSupport": "forbidden"},
    }
    hit = {
        "uri": uri,
        "server": "demo",
        "revision": "r1",
        "score": 0.9,
        "tool": definition,
        "address": "http://127.0.0.1:3001/mcp",
    }
    find = AsyncMock(return_value={"tools": [hit], "total": 1})
    invoke = AsyncMock(return_value={"content": [{"type": "text", "text": "5"}]})
    read = AsyncMock(side_effect=AssertionError("Gateway must not require another read"))
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            mcps=SimpleNamespace(find=find, invoke=invoke),
            fs=SimpleNamespace(read=read, read_visible=read),
        ),
    )

    discovery = await _call_tool("find_mcp_tools", {"query": "Add two numbers"})
    payload = discovery["structuredContent"]
    assert discovery["isError"] is False
    assert json.loads(discovery["content"][0]["text"]) == payload
    candidate = payload["tools"][0]
    assert candidate["tool"] == definition
    assert "address" not in candidate
    arguments = dict(zip(candidate["tool"]["inputSchema"]["required"], [2, 3], strict=True))
    result = await _call_tool(
        "invoke_mcp_tool",
        {
            "tool_uri": candidate["uri"],
            "arguments": arguments,
        },
    )

    assert result["isError"] is False
    assert result["content"][-1] == {"type": "text", "text": "5"}
    invoke.assert_awaited_once_with(uri, {"a": 2, "b": 3}, mcp_endpoint._get_ctx())
    read.assert_not_awaited()


@pytest.mark.parametrize("fails", [False, True])
async def test_gateway_discovery_empty_and_error_results(monkeypatch, fails):
    find = AsyncMock(return_value={"tools": [], "total": 0})
    if fails:
        find.side_effect = InvalidArgumentError("Tool search query must not be empty")
    monkeypatch.setattr(
        mcp_endpoint, "get_service", lambda: SimpleNamespace(mcps=SimpleNamespace(find=find))
    )
    result = await _call_tool("find_mcp_tools", {"query": "" if fails else "unknown tool"})
    assert result["isError"] is fails
    if fails:
        assert "must not be empty" in result["content"][0]["text"]
    else:
        assert result["structuredContent"] == {"tools": [], "total": 0}
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


async def test_live_context_read_is_transparent_on_wire(monkeypatch):
    descriptor = "---\ntype: OpenViking Live Context\n---\n"
    invoke_live_context = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": "5"}],
            "structuredContent": {"value": 5},
            "isError": False,
        }
    )
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(read_visible=AsyncMock(return_value=descriptor)),
            mcps=SimpleNamespace(invoke_live_context=invoke_live_context),
        ),
    )

    result = await _call_tool("read", {"uris": "viking://resources/sum.md"})

    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": "5"}]
    assert result["structuredContent"] == {"value": 5}
    assert descriptor not in result["content"][0]["text"]
    assert "Live result from" not in result["content"][0]["text"]


async def test_skill_install_business_failure_sets_error_result(monkeypatch):
    monkeypatch.setattr(
        mcp_endpoint,
        "install_skills",
        AsyncMock(return_value={"status": "error", "message": "invalid skill"}),
    )
    arguments = {"data": "---\nname: example\ndescription: Example\n---\nBody"}

    direct_result = await mcp_endpoint.add_skill(**arguments)
    result = await _call_tool("add_skill", arguments)

    assert direct_result == "Error adding skill: invalid skill"
    assert type(direct_result) is str
    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": direct_result}]


async def test_grep_partial_failure_without_matches_stays_partial(monkeypatch):
    async def grep(_uri, pattern, **_kwargs):
        if pattern == "bad":
            raise RuntimeError("storage offline")
        return {"matches": []}

    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(grep=grep)),
    )
    arguments = {"uri": "viking://resources", "pattern": ["good", "bad"]}

    direct_result = await mcp_endpoint.grep(**arguments)
    result = await _call_tool("grep", arguments)

    assert direct_result == (
        "No matches found for successful pattern(s): good\n"
        "Patterns that could not be searched:\n  bad: RuntimeError: storage offline"
    )
    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": direct_result}]


async def test_read_all_failures_set_error_result(monkeypatch):
    read_visible = AsyncMock(side_effect=PermissionDeniedError("read permission required"))
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
        "=== viking://resources/a.md ===\nPERMISSION_DENIED: read permission required\n\n"
        "=== viking://resources/b.md ===\nPERMISSION_DENIED: read permission required"
    )
    assert result["isError"] is True
    assert "PERMISSION_DENIED: read permission required" in result["content"][0]["text"]
    assert "structuredContent" not in result


async def test_read_partial_failure_remains_a_success_result(monkeypatch):
    async def read_visible(uri, **kwargs):
        if uri.endswith("bad.md"):
            raise InvalidArgumentError("not readable")
        return "visible content"

    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(read_visible=read_visible),
            mcps=SimpleNamespace(invoke_live_context=AsyncMock(return_value=None)),
        ),
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
