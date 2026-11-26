# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP wire-result tests for tool execution failures."""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, CallToolResult

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.identity import RequestContext, Role
from openviking.service.mcp_service import MCPService
from openviking_cli.exceptions import PermissionDeniedError
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


@pytest.mark.usefixtures("public_resource_dns")
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

    upstream_error = {
        "content": [{"type": "text", "text": "upstream rejected the request"}],
        "structuredContent": {"code": "invalid_city"},
        "isError": True,
    }
    service.mcps = SimpleNamespace(invoke=AsyncMock(return_value=upstream_error))
    result = await _call_tool(
        "invoke_mcp_tool",
        {
            "tool_uri": "viking://agent/tools/weather/current.json",
            "arguments": {"city": "Atlantis"},
        },
    )
    assert result == upstream_error


@pytest.mark.parametrize("timeout_seconds", [None, 65.5])
async def test_invoke_timeout_is_exposed_and_forwarded(monkeypatch, timeout_seconds):
    uri = "viking://agent/tools/weather/current.json"
    arguments = {"city": "Beijing"}
    native_result = {
        "content": [{"type": "text", "text": "weather result"}],
        "structuredContent": {"temperature": 23},
        "isError": False,
    }
    invoke = AsyncMock(return_value=native_result)
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(mcps=SimpleNamespace(invoke=invoke)),
    )
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    schema = tools["invoke_mcp_tool"].inputSchema
    assert "timeout_seconds" in schema["properties"]
    assert "timeout_seconds" not in schema.get("required", [])
    request = {"tool_uri": uri, "arguments": arguments}
    if timeout_seconds is not None:
        request["timeout_seconds"] = timeout_seconds

    result = await _call_tool("invoke_mcp_tool", request)

    assert result == native_result
    invoke.assert_awaited_once_with(
        uri, arguments, mcp_endpoint._get_ctx(), timeout_seconds=timeout_seconds
    )


@pytest.mark.parametrize(
    "options,expected_timeout",
    [({}, 60), ({"timeout_seconds": None}, 60), ({"timeout_seconds": 65.5}, 65.5)],
)
async def test_service_invoke_default_timeout(monkeypatch, options, expected_timeout):
    uri = "viking://agent/tools/weather/current.json"
    arguments = {"city": "Beijing"}
    service = MCPService(None)
    server = {"url": "https://mcp.example.com/mcp"}
    document = {
        "tool": {
            "name": "current",
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }
    }
    monkeypatch.setattr(service, "_registered", AsyncMock(return_value=(server, {})))
    monkeypatch.setattr(service, "_read_tool_document", AsyncMock(return_value=document))
    monkeypatch.setattr(service, "_connection_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(service, "_connection_server", AsyncMock(return_value=server))
    upstream = SimpleNamespace(
        initialize=AsyncMock(),
        call_tool=AsyncMock(return_value=CallToolResult(content=[])),
    )

    @asynccontextmanager
    async def session(*_args):
        yield upstream

    session_factory = Mock(side_effect=session)
    monkeypatch.setattr(service, "_session", session_factory)

    await service.invoke(uri, arguments, mcp_endpoint._get_ctx(), **options)

    session_factory.assert_called_once_with(server, {}, expected_timeout)
    upstream.call_tool.assert_awaited_once_with(
        "current",
        arguments=arguments,
        read_timeout_seconds=timedelta(seconds=expected_timeout),
    )


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


async def test_health_unavailable_remains_diagnostic_data(monkeypatch):
    def unavailable():
        raise RuntimeError("not initialized")

    monkeypatch.setattr(mcp_endpoint, "get_service", unavailable)

    result = await _call_tool("health", {})

    assert result["isError"] is False
    assert result["content"] == [
        {"type": "text", "text": "OpenViking is unhealthy: not initialized"}
    ]
