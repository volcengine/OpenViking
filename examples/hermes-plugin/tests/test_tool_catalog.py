"""The openviking_* tool catalogue (core/tool_catalog.py): naming, exposure and cache order."""

import json

import pytest


@pytest.fixture
def catalog(external_provider, core_module):
    _, _, module, _ = external_provider("tool-catalog")
    mod = core_module(module, "tool_catalog")
    mod.clear_memory_cache()
    yield mod
    mod.clear_memory_cache()


SERVER_TOOLS = [
    {"name": name, "description": f"{name} tool", "inputSchema": {"type": "object", "properties": {"uri": {"type": "string"}}}}
    for name in ("find", "search", "read", "list", "tree", "grep", "glob", "remember", "add_resource", "health",
                 "write", "edit", "add_skill", "list_watches", "cancel_watch", "unknown_new_tool")
] + [{
    "name": "forget",
    "description": "Delete",
    "inputSchema": {"type": "object", "properties": {"uri": {"type": "string"}, "recursive": {"type": "boolean"}},
                    "required": ["uri", "recursive"]},
}]


def test_default_exposure_and_prefix(catalog):
    names = [s["name"] for s in catalog.build_schemas(SERVER_TOOLS)]
    assert names == [f"openviking_{n}" for n in ("find", "search", "read", "list", "tree", "grep", "glob",
                                                    "remember", "add_resource", "health", "forget")]
    find = catalog.build_schemas(SERVER_TOOLS)[0]
    assert find["description"] == "find tool"
    assert find["parameters"] == SERVER_TOOLS[0]["inputSchema"]


def test_extra_tools_setting_adds_optional_tools_only(catalog):
    extra = catalog.parse_extra_tools("write, openviking_edit, bogus, find")
    assert extra == ("write", "edit")
    names = {s["name"] for s in catalog.build_schemas(SERVER_TOOLS, extra)}
    assert {"openviking_write", "openviking_edit"} <= names
    assert "openviking_add_skill" not in names and "openviking_unknown_new_tool" not in names


def test_forget_loses_recursive(catalog):
    forget = next(s for s in catalog.build_schemas(SERVER_TOOLS) if s["name"] == "openviking_forget")
    assert "recursive" not in forget["parameters"]["properties"]
    assert forget["parameters"]["required"] == ["uri"]
    # The server's own schema is left untouched.
    assert "recursive" in SERVER_TOOLS[-1]["inputSchema"]["properties"]


def test_load_order_memory_then_disk_then_live(catalog, tmp_path):
    home = str(tmp_path / "home")
    calls = []

    def fetch(budget):
        calls.append(budget)
        return [{"name": "find", "description": "live", "inputSchema": {}}]

    key = catalog.fingerprint("http://ov.test", "k")
    tools = catalog.load(key, home, "http://ov.test", fetch)
    assert tools[0]["description"] == "live" and calls == [catalog.LIVE_LIST_BUDGET_SECONDS]
    cached = json.loads(catalog.cache_path(home).read_text())
    assert cached["endpoint"] == "http://ov.test"

    # In-process cache answers without fetching.
    assert catalog.load(key, home, "http://ov.test", fetch)[0]["description"] == "live"
    assert len(calls) == 1

    # A new process reads the disk cache for the same endpoint.
    catalog.clear_memory_cache()
    assert catalog.load(key, home, "http://ov.test", lambda b: pytest.fail("fetched"))[0]["description"] == "live"

    # A different endpoint ignores the disk cache; a failed fetch yields an empty list.
    catalog.clear_memory_cache()

    def unreachable(budget):
        raise RuntimeError("connection refused")

    assert catalog.load(catalog.fingerprint("http://other.test"), home, "http://other.test", unreachable) == []
