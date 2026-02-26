# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenViking MCP tool definitions and dispatch."""

import json

import pytest

from openviking.mcp.permissions import MCPAccessLevel
from openviking.mcp.tools import dispatch_tool, get_tool_definitions


class _FakeClient:
    def __init__(self):
        self.calls = []

    def find(self, query, target_uri="", limit=10, score_threshold=None):
        self.calls.append(("find", query, target_uri, limit, score_threshold))
        return {"memories": [], "resources": [], "skills": [], "total": 0}

    def search(self, query, target_uri="", session_id=None, limit=10, score_threshold=None):
        self.calls.append(("search", query, target_uri, session_id, limit, score_threshold))
        return {"memories": [], "resources": [], "skills": [], "total": 0}

    def read(self, uri, offset=0, limit=-1):
        self.calls.append(("read", uri, offset, limit))
        return "hello"

    def ls(
        self,
        uri="viking://",
        simple=False,
        recursive=False,
        output="agent",
        abs_limit=256,
        show_all_hidden=False,
        node_limit=1000,
    ):
        self.calls.append(
            ("ls", uri, simple, recursive, output, abs_limit, show_all_hidden, node_limit)
        )
        return [{"uri": "viking://resources"}]

    def abstract(self, uri):
        self.calls.append(("abstract", uri))
        return "abs"

    def overview(self, uri):
        self.calls.append(("overview", uri))
        return "ov"

    def wait_processed(self, timeout=None):
        self.calls.append(("wait_processed", timeout))
        return {"pending": 0}

    def stat(self, uri):
        self.calls.append(("stat", uri))
        return {"uri": uri}

    def tree(self, uri, output="agent", abs_limit=128, show_all_hidden=False, node_limit=1000):
        self.calls.append(("tree", uri, output, abs_limit, show_all_hidden, node_limit))
        return {"uri": uri, "children": []}

    def grep(self, uri, pattern, case_insensitive=False):
        self.calls.append(("grep", uri, pattern, case_insensitive))
        return {"matches": []}

    def glob(self, pattern, uri="viking://"):
        self.calls.append(("glob", pattern, uri))
        return {"matches": []}

    def get_status(self):
        self.calls.append(("status",))
        return {"healthy": True}

    def is_healthy(self):
        self.calls.append(("health",))
        return True

    def create_session(self):
        self.calls.append(("create_session",))
        return {"session_id": "s1"}

    def list_sessions(self):
        self.calls.append(("list_sessions",))
        return [{"session_id": "s1"}]

    def get_session(self, session_id):
        self.calls.append(("get_session", session_id))
        return {"session_id": session_id}

    def delete_session(self, session_id):
        self.calls.append(("delete_session", session_id))
        return None

    def add_message(self, session_id, role, content=None, parts=None):
        self.calls.append(("add_message", session_id, role, content, parts))
        return {"session_id": session_id}

    def commit_session(self, session_id):
        self.calls.append(("commit_session", session_id))
        return {"session_id": session_id}

    def add_resource(
        self,
        path,
        target=None,
        reason="",
        instruction="",
        wait=False,
        timeout=None,
    ):
        self.calls.append(("add_resource", path, target, reason, instruction, wait, timeout))
        return {"root_uri": "viking://resources/demo"}

    def add_skill(self, data, wait=False, timeout=None):
        self.calls.append(("add_skill", data, wait, timeout))
        return {"root_uri": "viking://agent/skills/demo"}

    def relations(self, uri):
        self.calls.append(("relations", uri))
        return [{"uri": "viking://resources/x", "reason": "ref"}]

    def link(self, from_uri, uris, reason=""):
        self.calls.append(("link", from_uri, uris, reason))
        return None

    def unlink(self, from_uri, uri):
        self.calls.append(("unlink", from_uri, uri))
        return None

    def mkdir(self, uri):
        self.calls.append(("mkdir", uri))
        return None

    def mv(self, from_uri, to_uri):
        self.calls.append(("mv", from_uri, to_uri))
        return None

    def rm(self, uri, recursive=False):
        self.calls.append(("rm", uri, recursive))
        return None

    def export_ovpack(self, uri, to):
        self.calls.append(("export_ovpack", uri, to))
        return to

    def import_ovpack(self, file_path, target, force=False, vectorize=True):
        self.calls.append(("import_ovpack", file_path, target, force, vectorize))
        return "viking://resources/imported"


def _tool_names(access_level=MCPAccessLevel.READONLY):
    return {tool["name"] for tool in get_tool_definitions(access_level=access_level)}


def _payload(tool_name, arguments, client, access_level=MCPAccessLevel.READONLY):
    return json.loads(dispatch_tool(tool_name, arguments, client, access_level=access_level))


def test_get_tool_definitions_filters_by_access_level():
    readonly = _tool_names("readonly")
    ingest = _tool_names("ingest")
    mutate = _tool_names("mutate")
    admin = _tool_names("admin")

    assert "openviking_find" in readonly
    assert "openviking_session_list" in readonly
    assert "openviking_resource_add" not in readonly

    assert "openviking_session_create" in ingest
    assert "openviking_resource_add" in ingest
    assert "openviking_fs_mkdir" not in ingest

    assert "openviking_fs_mkdir" in mutate
    assert "openviking_relation_link" in mutate
    assert "openviking_fs_rm" not in mutate

    assert "openviking_fs_rm" in admin
    assert "openviking_pack_import" in admin
    assert "openviking_session_delete" in admin


def test_dispatch_alias_add_resource_works_with_ingest_access():
    client = _FakeClient()
    body = _payload("openviking_add_resource", {"path": "./demo"}, client, access_level="ingest")

    assert body["ok"] is True
    assert client.calls[0] == ("add_resource", "./demo", None, "", "", False, None)


def test_dispatch_denies_when_access_level_is_insufficient():
    client = _FakeClient()
    body = _payload("openviking_session_delete", {"session_id": "s1"}, client, access_level="mutate")

    assert body["ok"] is False
    assert body["error"]["code"] == "PERMISSION_DENIED"
    assert body["error"]["details"]["required"] == "admin"
    assert body["error"]["details"]["current"] == "mutate"


@pytest.mark.parametrize(
    "tool_name,args,expected_call,level",
    [
        ("openviking_find", {"query": "what is openviking"}, ("find", "what is openviking", "", 10, None), "readonly"),
        ("openviking_search", {"query": "design", "session_id": "s1"}, ("search", "design", "", "s1", 10, None), "readonly"),
        ("openviking_read", {"uri": "viking://resources/a.md"}, ("read", "viking://resources/a.md", 0, 200), "readonly"),
        (
            "openviking_ls",
            {"uri": "viking://resources", "output": "original", "abs_limit": 10, "node_limit": 20},
            ("ls", "viking://resources", False, False, "original", 10, False, 20),
            "readonly",
        ),
        ("openviking_abstract", {"uri": "viking://resources"}, ("abstract", "viking://resources"), "readonly"),
        ("openviking_overview", {"uri": "viking://resources"}, ("overview", "viking://resources"), "readonly"),
        ("openviking_wait_processed", {"timeout": 5.0}, ("wait_processed", 5.0), "readonly"),
        ("openviking_stat", {"uri": "viking://resources/a.md"}, ("stat", "viking://resources/a.md"), "readonly"),
        (
            "openviking_tree",
            {"uri": "viking://resources", "abs_limit": 256, "show_all_hidden": True, "node_limit": 50},
            ("tree", "viking://resources", "agent", 256, True, 50),
            "readonly",
        ),
        ("openviking_grep", {"uri": "viking://resources", "pattern": "OpenViking", "ignore_case": True}, ("grep", "viking://resources", "OpenViking", True), "readonly"),
        ("openviking_glob", {"pattern": "*.md", "uri": "viking://resources"}, ("glob", "*.md", "viking://resources"), "readonly"),
        ("openviking_status", {}, ("status",), "readonly"),
        ("openviking_health", {}, ("health",), "readonly"),
        ("openviking_session_create", {}, ("create_session",), "ingest"),
        ("openviking_session_list", {}, ("list_sessions",), "readonly"),
        ("openviking_session_get", {"session_id": "s1"}, ("get_session", "s1"), "readonly"),
        ("openviking_session_add_message", {"session_id": "s1", "role": "user", "content": "hello"}, ("add_message", "s1", "user", "hello", None), "ingest"),
        ("openviking_session_commit", {"session_id": "s1"}, ("commit_session", "s1"), "ingest"),
        ("openviking_resource_add", {"path": "./demo", "to": "viking://resources/target"}, ("add_resource", "./demo", "viking://resources/target", "", "", False, None), "ingest"),
        ("openviking_resource_add_skill", {"data": "./skills/demo"}, ("add_skill", "./skills/demo", False, None), "ingest"),
        ("openviking_relation_list", {"uri": "viking://resources/a.md"}, ("relations", "viking://resources/a.md"), "readonly"),
        ("openviking_relation_link", {"from_uri": "viking://a", "uris": ["viking://b"], "reason": "ref"}, ("link", "viking://a", ["viking://b"], "ref"), "mutate"),
        ("openviking_relation_unlink", {"from_uri": "viking://a", "uri": "viking://b"}, ("unlink", "viking://a", "viking://b"), "mutate"),
        ("openviking_fs_mkdir", {"uri": "viking://resources/newdir"}, ("mkdir", "viking://resources/newdir"), "mutate"),
        ("openviking_fs_mv", {"from_uri": "viking://a", "to_uri": "viking://b"}, ("mv", "viking://a", "viking://b"), "mutate"),
        ("openviking_fs_rm", {"uri": "viking://resources/newdir", "recursive": True}, ("rm", "viking://resources/newdir", True), "admin"),
        ("openviking_pack_export", {"uri": "viking://resources/a", "to": "./a.ovpack"}, ("export_ovpack", "viking://resources/a", "./a.ovpack"), "admin"),
        ("openviking_pack_import", {"file_path": "./a.ovpack", "target_uri": "viking://resources/import", "force": True, "vectorize": False}, ("import_ovpack", "./a.ovpack", "viking://resources/import", True, False), "admin"),
    ],
)
def test_dispatch_tools_success(tool_name, args, expected_call, level):
    client = _FakeClient()
    body = _payload(tool_name, args, client, access_level=level)

    assert body["ok"] is True
    assert client.calls[0] == expected_call


def test_dispatch_add_message_requires_content_or_parts():
    client = _FakeClient()
    body = _payload(
        "openviking_session_add_message",
        {"session_id": "s1", "role": "user"},
        client,
        access_level="ingest",
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "args,message",
    [
        ({"session_id": "s1", "role": "bot", "content": "x"}, "'role' must be one of"),
        ({"session_id": "s1", "role": "user", "parts": []}, "'parts' must not be an empty array"),
        ({"session_id": "s1", "role": "user", "parts": ["x"]}, "'parts' must be an array of objects"),
    ],
)
def test_dispatch_add_message_rejects_invalid_payload(args, message):
    client = _FakeClient()
    body = _payload("openviking_session_add_message", args, client, access_level="ingest")
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert message in body["error"]["message"]


@pytest.mark.parametrize(
    "args,message",
    [
        ({"from_uri": "viking://a", "uris": []}, "non-empty string array"),
        ({"from_uri": "viking://a", "uris": [""]}, "contain non-empty strings"),
    ],
)
def test_dispatch_link_rejects_invalid_uris(args, message):
    client = _FakeClient()
    body = _payload("openviking_relation_link", args, client, access_level="mutate")
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert message in body["error"]["message"]


@pytest.mark.parametrize(
    "tool_name,args,error_message",
    [
        ("openviking_read", {"uri": "viking://resources/a.md", "limit": True}, "'limit' must be an integer"),
        ("openviking_search", {"query": "x", "threshold": True}, "'threshold' must be a number"),
        ("openviking_ls", {"output": "invalid"}, "'output' must be either 'agent' or 'original'"),
    ],
)
def test_dispatch_rejects_invalid_argument_types(tool_name, args, error_message):
    client = _FakeClient()
    body = _payload(tool_name, args, client)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert error_message in body["error"]["message"]


def test_dispatch_unknown_tool_returns_error():
    client = _FakeClient()
    body = _payload("no_such_tool", {}, client)

    assert body["ok"] is False
    assert body["error"]["code"] == "TOOL_NOT_FOUND"


def test_dispatch_client_exception_is_wrapped():
    class _BrokenClient:
        def read(self, *args, **kwargs):
            raise RuntimeError("boom")

    body = _payload("openviking_read", {"uri": "viking://resources/a.md"}, _BrokenClient())

    assert body["ok"] is False
    assert body["error"]["code"] == "INTERNAL"
