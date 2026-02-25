# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenViking MCP tool definitions and dispatch."""

import json

import pytest

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

    def ls(self, uri="viking://", simple=False, recursive=False, output="agent"):
        self.calls.append(("ls", uri, simple, recursive, output))
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


def _tool_names(include_write: bool = False):
    return {tool["name"] for tool in get_tool_definitions(include_write=include_write)}


def _payload(tool_name, arguments, client, allow_write=False):
    return json.loads(dispatch_tool(tool_name, arguments, client, allow_write=allow_write))


def test_tool_definitions_have_v1_read_set():
    names = _tool_names()
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


def test_get_tool_definitions_respects_write_flag():
    read_only_names = _tool_names(include_write=False)
    writable_names = _tool_names(include_write=True)

    assert "openviking_add_resource" not in read_only_names
    assert "openviking_add_resource" in writable_names


def test_dispatch_find_success():
    client = _FakeClient()
    body = _payload("openviking_find", {"query": "what is openviking"}, client)

    assert body["ok"] is True
    assert body["result"]["total"] == 0
    assert client.calls[0] == ("find", "what is openviking", "", 10, None)


def test_dispatch_search_success():
    client = _FakeClient()
    body = _payload(
        "openviking_search",
        {"query": "design", "uri": "viking://resources", "session_id": "s1", "threshold": 0.5},
        client,
    )

    assert body["ok"] is True
    assert client.calls[0] == ("search", "design", "viking://resources", "s1", 10, 0.5)


def test_dispatch_read_applies_default_limit_cap():
    client = _FakeClient()
    _payload("openviking_read", {"uri": "viking://resources/a.md"}, client)
    assert client.calls[0] == ("read", "viking://resources/a.md", 0, 200)


def test_dispatch_read_rejects_oversized_limit():
    client = _FakeClient()
    body = _payload("openviking_read", {"uri": "viking://resources/a.md", "limit": 5000}, client)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "args,key,message",
    [
        ({"uri": "viking://resources/a.md", "limit": True}, "limit", "'limit' must be an integer"),
        ({"uri": "viking://resources/a.md", "offset": False}, "offset", "'offset' must be an integer"),
        (
            {"query": "x", "threshold": True},
            "threshold",
            "'threshold' must be a number",
        ),
    ],
)
def test_dispatch_rejects_boolean_for_numeric_fields(args, key, message):
    client = _FakeClient()
    body = _payload("openviking_search" if key == "threshold" else "openviking_read", args, client)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert body["error"]["message"] == message


def test_dispatch_find_requires_query():
    client = _FakeClient()
    body = _payload("openviking_find", {}, client)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_dispatch_add_resource_is_denied_in_readonly_mode():
    client = _FakeClient()
    body = _payload("openviking_add_resource", {"path": "./demo"}, client, allow_write=False)

    assert body["ok"] is False
    assert body["error"]["code"] == "PERMISSION_DENIED"
    assert client.calls == []


def test_dispatch_add_resource_success_when_write_enabled():
    client = _FakeClient()
    body = _payload(
        "openviking_add_resource",
        {
            "path": "./demo",
            "to": "viking://resources/target",
            "reason": "import",
            "instruction": "keep structure",
            "wait": True,
            "timeout": 30.5,
        },
        client,
        allow_write=True,
    )

    assert body["ok"] is True
    assert body["result"]["root_uri"] == "viking://resources/demo"
    assert client.calls[0] == (
        "add_resource",
        "./demo",
        "viking://resources/target",
        "import",
        "keep structure",
        True,
        30.5,
    )


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
        ("openviking_wait_processed", {"timeout": 5.0}, ("wait_processed", 5.0)),
        ("openviking_stat", {"uri": "viking://resources/a.md"}, ("stat", "viking://resources/a.md")),
        (
            "openviking_tree",
            {
                "uri": "viking://resources",
                "abs_limit": 256,
                "show_all_hidden": True,
                "node_limit": 50,
            },
            ("tree", "viking://resources", "agent", 256, True, 50),
        ),
        (
            "openviking_grep",
            {"uri": "viking://resources", "pattern": "OpenViking", "ignore_case": True},
            ("grep", "viking://resources", "OpenViking", True),
        ),
        (
            "openviking_glob",
            {"pattern": "*.md", "uri": "viking://resources"},
            ("glob", "*.md", "viking://resources"),
        ),
        ("openviking_status", {}, ("status",)),
        ("openviking_health", {}, ("health",)),
    ],
)
def test_dispatch_v1_tools_success(tool_name, args, expected_call):
    client = _FakeClient()
    body = _payload(tool_name, args, client)

    assert body["ok"] is True
    assert client.calls[0] == expected_call
    if tool_name == "openviking_health":
        assert body["result"]["healthy"] is True


@pytest.mark.parametrize(
    "args,field",
    [
        ({"uri": "viking://resources", "abs_limit": -1}, "abs_limit"),
        ({"uri": "viking://resources", "abs_limit": 5000}, "abs_limit"),
        ({"uri": "viking://resources", "node_limit": 0}, "node_limit"),
        ({"uri": "viking://resources", "node_limit": 6000}, "node_limit"),
    ],
)
def test_dispatch_tree_rejects_out_of_range_limits(args, field):
    client = _FakeClient()
    body = _payload("openviking_tree", args, client)

    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    assert field in body["error"]["message"]


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
