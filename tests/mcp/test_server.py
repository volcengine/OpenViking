# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenViking MCP server adapters."""

import json

from openviking.mcp.server import OpenVikingMCPAdapter


class _StubClient:
    def find(self, query, target_uri="", limit=10, score_threshold=None):
        return {"memories": [], "resources": [], "skills": [], "total": 0}

    def search(self, query, target_uri="", session_id=None, limit=10, score_threshold=None):
        return {"memories": [], "resources": [], "skills": [], "total": 0}

    def read(self, uri, offset=0, limit=-1):
        return "hello"

    def ls(self, uri="viking://", simple=False, recursive=False, output="agent"):
        return [{"uri": "viking://resources"}]

    def abstract(self, uri):
        return "abs"

    def overview(self, uri):
        return "ov"

    def add_resource(
        self,
        path,
        target=None,
        reason="",
        instruction="",
        wait=False,
        timeout=None,
    ):
        return {"root_uri": "viking://resources/demo"}


def test_list_tools_returns_v1_read_definitions():
    adapter = OpenVikingMCPAdapter(_StubClient())
    tools = adapter.list_tools()
    names = {tool["name"] for tool in tools}

    assert "openviking_find" in names
    assert "openviking_search" in names
    assert "openviking_read" in names
    assert "openviking_ls" in names
    assert "openviking_abstract" in names
    assert "openviking_overview" in names
    assert "openviking_wait_processed" in names
    assert "openviking_stat" in names
    assert "openviking_tree" in names
    assert "openviking_grep" in names
    assert "openviking_glob" in names
    assert "openviking_status" in names
    assert "openviking_health" in names
    assert "openviking_add_resource" not in names


def test_list_tools_includes_write_tool_when_enabled():
    adapter = OpenVikingMCPAdapter(_StubClient(), allow_write=True)
    tools = adapter.list_tools()
    names = {tool["name"] for tool in tools}

    assert "openviking_add_resource" in names


def test_call_tool_returns_json_text_payload():
    adapter = OpenVikingMCPAdapter(_StubClient())
    payload = adapter.call_tool("openviking_read", {"uri": "viking://resources/readme.md"})
    body = json.loads(payload)

    assert body["ok"] is True
    assert body["result"] == "hello"


def test_call_tool_write_is_denied_in_readonly_mode():
    adapter = OpenVikingMCPAdapter(_StubClient(), allow_write=False)
    payload = adapter.call_tool("openviking_add_resource", {"path": "./data"})
    body = json.loads(payload)

    assert body["ok"] is False
    assert body["error"]["code"] == "PERMISSION_DENIED"
