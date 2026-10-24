# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP wire-result tests for tool execution failures."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


async def test_unexpected_failure_is_logged_and_recorded_on_trace(monkeypatch, caplog):
    uri = "viking://resources/a.md"
    content = "request content must not enter the log message"
    failure = RuntimeError("storage unavailable")
    service = SimpleNamespace(fs=SimpleNamespace(write=AsyncMock(side_effect=failure)))
    record_trace_exception = Mock()
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    monkeypatch.setattr(mcp_endpoint, "record_trace_exception", record_trace_exception)

    mcp_endpoint.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.ERROR, logger=mcp_endpoint.logger.name):
            result = await _call_tool("write", {"uri": uri, "content": content})
    finally:
        mcp_endpoint.logger.removeHandler(caplog.handler)

    assert result["isError"] is True
    assert result["content"] == [
        {"type": "text", "text": "Error executing tool write: storage unavailable"}
    ]
    record_trace_exception.assert_called_once_with(failure)
    failure_records = [
        record for record in caplog.records if record.getMessage() == "Unexpected MCP tool failure"
    ]
    assert len(failure_records) == 1
    assert failure_records[0].mcp_tool == "write"
    assert failure_records[0].exc_info is not None
    assert content not in failure_records[0].getMessage()


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
