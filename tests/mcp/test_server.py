# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenViking MCP server adapters."""

import json

from openviking.mcp.server import OpenVikingMCPAdapter


class _StubClient:
    def find(self, query, target_uri="", limit=10, score_threshold=None):
        return {"memories": [], "resources": [], "skills": [], "total": 0}

    def read(self, uri, offset=0, limit=-1):
        return "hello"

    def ls(self, uri="viking://", simple=False, recursive=False, output="agent"):
        return [{"uri": "viking://resources"}]

    def abstract(self, uri):
        return "abs"

    def overview(self, uri):
        return "ov"


def test_list_tools_returns_mvp_definitions():
    adapter = OpenVikingMCPAdapter(_StubClient())
    tools = adapter.list_tools()
    names = {tool["name"] for tool in tools}

    assert "openviking_find" in names
    assert "openviking_read" in names
    assert "openviking_ls" in names
    assert "openviking_abstract" in names
    assert "openviking_overview" in names


def test_call_tool_returns_json_text_payload():
    adapter = OpenVikingMCPAdapter(_StubClient())
    payload = adapter.call_tool("openviking_read", {"uri": "viking://resources/readme.md"})
    body = json.loads(payload)

    assert body["ok"] is True
    assert body["result"] == "hello"
