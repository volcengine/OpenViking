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

    def ls(self, uri="viking://", simple=False, recursive=False, output="agent", **kwargs):
        return [{"uri": "viking://resources"}]

    def abstract(self, uri):
        return "abs"

    def overview(self, uri):
        return "ov"

    def wait_processed(self, timeout=None):
        return {"pending": 0}

    def stat(self, uri):
        return {"uri": uri}

    def tree(self, uri, output="agent", abs_limit=128, show_all_hidden=False, node_limit=1000):
        return {"uri": uri, "children": []}

    def grep(self, uri, pattern, case_insensitive=False):
        return {"matches": []}

    def glob(self, pattern, uri="viking://"):
        return {"matches": []}

    def get_status(self):
        return {"healthy": True}

    def is_healthy(self):
        return True

    def list_sessions(self):
        return [{"session_id": "s1"}]

    def get_session(self, session_id):
        return {"session_id": session_id}

    def relations(self, uri):
        return [{"uri": uri, "reason": "ref"}]

    def create_session(self):
        return {"session_id": "s1"}

    def add_message(self, session_id, role, content=None, parts=None):
        return {"session_id": session_id}

    def commit_session(self, session_id):
        return {"session_id": session_id}

    def add_resource(self, path, target=None, reason="", instruction="", wait=False, timeout=None):
        return {"root_uri": "viking://resources/demo"}

    def add_skill(self, data, wait=False, timeout=None):
        return {"root_uri": "viking://agent/skills/demo"}

    def link(self, from_uri, uris, reason=""):
        return None

    def unlink(self, from_uri, uri):
        return None

    def mkdir(self, uri):
        return None

    def mv(self, from_uri, to_uri):
        return None

    def delete_session(self, session_id):
        return None

    def rm(self, uri, recursive=False):
        return None

    def export_ovpack(self, uri, to):
        return to

    def import_ovpack(self, file_path, target, force=False, vectorize=True):
        return "viking://resources/imported"


def test_list_tools_filters_by_access_level():
    readonly = OpenVikingMCPAdapter(_StubClient(), access_level="readonly")
    mutate = OpenVikingMCPAdapter(_StubClient(), access_level="mutate")
    admin = OpenVikingMCPAdapter(_StubClient(), access_level="admin")

    readonly_names = {tool["name"] for tool in readonly.list_tools()}
    mutate_names = {tool["name"] for tool in mutate.list_tools()}
    admin_names = {tool["name"] for tool in admin.list_tools()}

    assert "openviking_find" in readonly_names
    assert "openviking_session_create" not in readonly_names
    assert "openviking_fs_mkdir" in mutate_names
    assert "openviking_fs_rm" not in mutate_names
    assert "openviking_fs_rm" in admin_names
    assert "openviking_pack_import" in admin_names


def test_call_tool_returns_json_text_payload():
    adapter = OpenVikingMCPAdapter(_StubClient())
    payload = adapter.call_tool("openviking_read", {"uri": "viking://resources/readme.md"})
    body = json.loads(payload)

    assert body["ok"] is True
    assert body["result"] == "hello"


def test_call_tool_write_is_denied_when_permission_insufficient():
    adapter = OpenVikingMCPAdapter(_StubClient(), access_level="ingest")
    payload = adapter.call_tool("openviking_fs_rm", {"uri": "viking://resources/x"})
    body = json.loads(payload)

    assert body["ok"] is False
    assert body["error"]["code"] == "PERMISSION_DENIED"
