# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenViking MCP tool definitions and dispatch."""

import json

import pytest

from openviking.mcp.tools import TOOL_DEFINITIONS, dispatch_tool


class _FakeClient:
    def __init__(self):
        self.calls = []

    def find(self, query, target_uri="", limit=10, score_threshold=None):
        self.calls.append(("find", query, target_uri, limit, score_threshold))
        return {"memories": [], "resources": [], "skills": [], "total": 0}

    def read(self, uri, offset=0, limit=-1):
        self.calls.append(("read", uri, offset, limit))
        return "hello"

    def ls(self, uri="viking://", simple=False, recursive=False, output="agent"):
        self.calls.append(("ls", uri, simple, recursive, output))
        return [{"uri": "viking://resources"}]

    def abstract(self, uri):
        self.calls.append(("abstract", uri))
        return "abs"

    def overview(self, uri):
        self.calls.append(("overview", uri))
        return "ov"


def _tool_names():
    return {tool["name"] for tool in TOOL_DEFINITIONS}


def test_tool_definitions_have_minimum_mvp_set():
    names = _tool_names()
    assert "openviking_find" in names
    assert "openviking_read" in names
    assert "openviking_ls" in names
    assert "openviking_abstract" in names
    assert "openviking_overview" in names


def test_dispatch_find_success():
    client = _FakeClient()
    payload = dispatch_tool("openviking_find", {"query": "what is openviking"}, client)
    body = json.loads(payload)

    assert body["ok"] is True
    assert body["result"]["total"] == 0
    assert client.calls[0][0] == "find"


def test_dispatch_read_applies_default_limit_cap():
    client = _FakeClient()
    dispatch_tool("openviking_read", {"uri": "viking://resources/a.md"}, client)
    assert client.calls[0] == ("read", "viking://resources/a.md", 0, 200)


def test_dispatch_read_rejects_oversized_limit():
    client = _FakeClient()
    payload = dispatch_tool("openviking_read", {"uri": "viking://resources/a.md", "limit": 5000}, client)
    body = json.loads(payload)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "args,key",
    [
        ({"uri": "viking://resources/a.md", "limit": True}, "limit"),
        ({"uri": "viking://resources/a.md", "offset": False}, "offset"),
    ],
)
def test_dispatch_read_rejects_boolean_integer_fields(args, key):
    client = _FakeClient()
    payload = dispatch_tool("openviking_read", args, client)
    body = json.loads(payload)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert body["error"]["message"] == f"'{key}' must be an integer"


def test_dispatch_find_requires_query():
    client = _FakeClient()
    payload = dispatch_tool("openviking_find", {}, client)
    body = json.loads(payload)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_dispatch_unknown_tool_returns_error():
    client = _FakeClient()
    payload = dispatch_tool("no_such_tool", {}, client)
    body = json.loads(payload)

    assert body["ok"] is False
    assert body["error"]["code"] == "TOOL_NOT_FOUND"


def test_dispatch_client_exception_is_wrapped():
    class _BrokenClient:
        def read(self, *args, **kwargs):
            raise RuntimeError("boom")

    payload = dispatch_tool("openviking_read", {"uri": "viking://resources/a.md"}, _BrokenClient())
    body = json.loads(payload)

    assert body["ok"] is False
    assert body["error"]["code"] == "INTERNAL"


@pytest.mark.parametrize(
    "tool_name,args,expected_call",
    [
        (
            "openviking_ls",
            {"uri": "viking://resources", "simple": True, "recursive": True},
            ("ls", "viking://resources", True, True, "agent"),
        ),
        ("openviking_abstract", {"uri": "viking://resources"}, ("abstract", "viking://resources")),
        ("openviking_overview", {"uri": "viking://resources"}, ("overview", "viking://resources")),
    ],
)
def test_dispatch_other_mvp_tools(tool_name, args, expected_call):
    client = _FakeClient()
    payload = dispatch_tool(tool_name, args, client)
    body = json.loads(payload)

    assert body["ok"] is True
    assert client.calls[0] == expected_call
