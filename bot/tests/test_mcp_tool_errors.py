# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Model-facing results for outbound MCP tool failures."""

from types import SimpleNamespace

import pytest
from vikingbot.agent.tools.mcp import MCPToolWrapper


class _CallFailure(Exception):
    def __init__(self, code, message: str = "failure"):
        super().__init__(message)
        self.error = SimpleNamespace(code=code, message=message)


class _FailingSession:
    def __init__(self, failure: Exception):
        self.failure = failure

    async def call_tool(self, _name, arguments):
        del arguments
        raise self.failure


def _wrapper(failure: Exception) -> MCPToolWrapper:
    tool = SimpleNamespace(
        name="search_messages",
        description="Search messages",
        inputSchema={"type": "object", "properties": {}},
    )
    return MCPToolWrapper(_FailingSession(failure), "beeper", tool)


async def test_jsonrpc_error_answer_is_reported_as_server_rejection():
    result = await _wrapper(_CallFailure(-32603, "400 Invalid input: limit must be <= 20")).execute(
        None,
        limit=50,
    )

    assert result == (
        "(MCP server rejected the tool call (JSON-RPC -32603): "
        "400 Invalid input: limit must be <= 20)"
    )


@pytest.mark.parametrize("code", [-32000, -32001])
async def test_jsonrpc_no_answer_codes_remain_transport_failures(code):
    result = await _wrapper(_CallFailure(code, "No answer")).execute(None)

    assert result == "(MCP tool call failed: _CallFailure)"


async def test_jsonrpc_error_message_is_redacted_before_reaching_the_model():
    result = await _wrapper(
        _CallFailure(-32602, "Invalid api_key=sk-secret-value-that-must-not-leak")
    ).execute(None)

    assert "sk-secret-value-that-must-not-leak" not in result
    assert "[REDACTED]" in result


async def test_non_jsonrpc_exception_keeps_generic_failure_result():
    result = await _wrapper(RuntimeError("socket failed")).execute(None)

    assert result == "(MCP tool call failed: RuntimeError)"
