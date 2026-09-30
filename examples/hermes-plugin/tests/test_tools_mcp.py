"""The openviking_* tools over MCP, with a fake session factory injected through Deps.mcp_session."""

import json
import time
import zipfile

import pytest

ENDPOINT = "http://127.0.0.1:19533"
SERVER_TOOLS = [
    {"name": name, "description": f"server {name}", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}
    for name in ("find", "search", "read", "list", "tree", "grep", "glob", "remember", "add_resource", "health", "write", "edit")
] + [{
    "name": "forget",
    "description": "server forget",
    "inputSchema": {"type": "object", "properties": {"uri": {"type": "string"}, "recursive": {"type": "boolean"}}},
}]


class Uploads:
    """REST client double: the header builder and temp_upload of the real client, no network."""

    def __init__(self, module, **identity):
        self.real = module._VikingClient(ENDPOINT, "user-key", transport=object(), **identity)
        self._endpoint = self.real._endpoint
        self._conn_snapshot = self.real._conn_snapshot
        self.uploads = []

    def upload_temp_file(self, path):
        entry = {"name": path.name}
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                entry["members"] = sorted(archive.namelist())
        self.uploads.append(entry)
        return "tmp-123"

    def get(self, path, **kwargs):
        return {"result": {"user": "alice"}}


@pytest.fixture
def wired(external_provider, inject_deps, fake_mcp, core_module):
    home, provider, module, _ = external_provider("tools-mcp")
    core_module(module, "tool_catalog").clear_memory_cache()
    fake_mcp.tools = SERVER_TOOLS
    inject_deps(module, provider, mcp_session=fake_mcp.factory)
    provider._client = Uploads(module, agent="hermes")
    provider._hermes_home = str(home)
    yield provider, module, fake_mcp
    core_module(module, "tool_catalog").clear_memory_cache()


def test_default_catalogue_names_and_schemas(wired):
    provider, _, fake = wired
    schemas = provider.get_tool_schemas()
    assert [s["name"] for s in schemas] == [
        f"openviking_{n}" for n in ("find", "search", "read", "list", "tree", "grep", "glob",
                                     "remember", "add_resource", "health", "forget")
    ]
    assert schemas[0]["description"] == "server find"
    assert schemas[0]["parameters"] == SERVER_TOOLS[0]["inputSchema"]
    assert "recursive" not in schemas[-1]["parameters"]["properties"]
    assert not any(s["name"].startswith("viking_") for s in schemas)


def test_second_answer_is_a_subset_of_the_first(wired, monkeypatch):
    provider, _, fake = wired
    first = {s["name"] for s in provider.get_tool_schemas()}
    # A setting added later cannot grow the routing table Hermes already fixed.
    monkeypatch.setenv("OPENVIKING_EXTRA_TOOLS", "write,edit")
    second = {s["name"] for s in provider.get_tool_schemas()}
    assert second <= first and "openviking_write" not in second


def test_extra_tools_setting_exposes_optional_tools(wired, monkeypatch):
    provider, _, fake = wired
    monkeypatch.setenv("OPENVIKING_EXTRA_TOOLS", "write, edit")
    names = {s["name"] for s in provider.get_tool_schemas()}
    assert {"openviking_write", "openviking_edit"} <= names
    assert provider.handle_tool_call("openviking_write", {"uri": "viking://x"}) == "write ok"


def test_catalogue_uses_disk_cache_before_live_list(wired, core_module):
    provider, module, fake = wired
    catalog = core_module(module, "tool_catalog")
    provider.get_tool_schemas()
    assert json.loads(catalog.cache_path(provider._hermes_home).read_text())["endpoint"]
    catalog.clear_memory_cache()
    fake.tools = []
    sessions = len(fake.sessions)
    provider._routed_tool_names = None
    assert len(provider.get_tool_schemas()) == 11
    assert len(fake.sessions) == sessions


def test_unreachable_server_returns_empty_within_budget(external_provider, core_module, monkeypatch):
    home, provider, module, _ = external_provider("tools-unreachable")
    core_module(module, "tool_catalog").clear_memory_cache()
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://10.255.255.1:9")
    started = time.monotonic()
    assert provider.get_tool_schemas() == []
    assert time.monotonic() - started < 3.0 + 1.5
    assert provider.system_prompt_block() == ""


def test_plain_forward_carries_rest_identity_headers(wired):
    provider, _, fake = wired
    assert provider.handle_tool_call("openviking_find", {"query": "x"}) == "find ok"
    assert fake.calls == [("find", {"query": "x"})]
    session = fake.sessions[-1]
    assert session["url"] == f"{ENDPOINT}/mcp"
    expected = provider._client.real._headers()
    assert session["headers"] == expected
    assert session["headers"]["Authorization"] == "Bearer user-key"
    assert session["headers"]["X-OpenViking-Actor-Peer"] == "hermes"


def test_mcp_connection_uses_the_rest_header_builder_and_plugin_version(wired, core_module):
    import re
    from pathlib import Path

    provider, module, _ = wired
    http = core_module(module, "http")
    version = re.search(r"^version:\s*(\S+)", (Path(module.__file__).parent / "plugin.yaml").read_text(), re.M).group(1)
    settings = {"endpoint": ENDPOINT, "api_key": "", "account": "acct", "user": "", "agent": "peer"}
    for client, expected in ((provider._client, provider._client.real._headers()), (None, http.build_openviking_headers(
            account="acct", user="default", trusted_identity=True, actor_peer_id="peer",
            user_agent=f"openviking-memory-hermes/{version}"))):
        conn = provider._mcp_connection(settings, client)
        assert conn.headers() == expected
        assert conn.client_version == version == http.plugin_version()


def test_unknown_or_unexposed_tool_is_rejected(wired):
    provider, _, fake = wired
    assert "Unknown tool" in json.loads(provider.handle_tool_call("viking_search", {"query": "x"}))["error"]
    assert "Unknown tool" in json.loads(provider.handle_tool_call("openviking_add_skill", {}))["error"]
    assert fake.calls == []


@pytest.mark.parametrize("context,expected", [("primary", {"query": "q", "session_id": "hermes-sid-1"}), ("cron", {"query": "q"})])
def test_search_injects_session_in_primary_context_only(wired, context, expected):
    provider, _, fake = wired
    provider._session_id, provider._agent_context = "sid-1", context
    provider.handle_tool_call("openviking_search", {"query": "q"})
    assert fake.calls == [("search", expected)]


def test_forget_validates_and_forces_non_recursive(wired):
    provider, _, fake = wired
    uri = "viking://user/alice/memories/preferences/a.md"
    assert provider.handle_tool_call("openviking_forget", {"uri": uri, "recursive": True}) == "forget ok"
    assert fake.calls == [("forget", {"uri": uri, "recursive": False})]
    error = json.loads(provider.handle_tool_call("openviking_forget", {"uri": "viking://resources/x"}))
    assert "openviking_forget" in error["error"]
    assert len(fake.calls) == 1


def test_add_resource_remote_url_is_forwarded(wired):
    provider, _, fake = wired
    provider.handle_tool_call("openviking_add_resource", {"path": "https://example.com/a.md", "to": "viking://resources/a"})
    assert fake.calls == [("add_resource", {"path": "https://example.com/a.md", "to": "viking://resources/a"})]
    assert provider._client.uploads == []


def test_add_resource_local_file_and_directory_are_uploaded(wired, tmp_path):
    provider, _, fake = wired
    note = tmp_path / "note.md"
    note.write_text("hello", encoding="utf-8")
    folder = tmp_path / "project"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "a.txt").write_text("a", encoding="utf-8")

    provider.handle_tool_call("openviking_add_resource", {"path": str(note), "description": "d"})
    provider.handle_tool_call("openviking_add_resource", {"path": str(folder)})

    assert provider._client.uploads == [{"name": "note.md"}, {"name": "project.zip", "members": ["sub/a.txt"]}]
    assert fake.calls == [
        ("add_resource", {"temp_file_id": "tmp-123", "description": "d"}),
        ("add_resource", {"temp_file_id": "tmp-123"}),
    ]
    missing = json.loads(provider.handle_tool_call("openviking_add_resource", {"path": str(tmp_path / "gone" / "x.md")}))
    assert "does not exist" in missing["error"]


def test_read_strips_front_matter_of_generated_summaries(wired):
    provider, _, fake = wired
    front = "---\nsource: /srv/openviking/data/x\n---\n"
    fake.reply = lambda name, args: {"content": [{"type": "text", "text": front + "Overview body"}]}
    assert provider.handle_tool_call("openviking_read", {"uris": "viking://resources/x/.overview.md"}) == "Overview body"
    plain = provider.handle_tool_call("openviking_read", {"uris": "viking://resources/x/a.md"})
    assert plain == front + "Overview body"

    combined = f"=== viking://r/.abstract.md ===\n{front}Abstract\n\n=== viking://r/a.md ===\n{front}Body"
    fake.reply = lambda name, args: {"content": [{"type": "text", "text": combined}]}
    result = provider.handle_tool_call("openviking_read", {"uris": ["viking://r/.abstract.md", "viking://r/a.md"]})
    assert result == f"=== viking://r/.abstract.md ===\nAbstract\n\n=== viking://r/a.md ===\n{front}Body"


def test_server_errors_come_back_as_tool_errors(wired):
    provider, _, fake = wired
    fake.reply = lambda name, args: {"isError": True, "content": [{"type": "text", "text": "bad uri"}]}
    result = json.loads(provider.handle_tool_call("openviking_read", {"uris": "viking://x"}))
    assert "bad uri" in result["error"]


def test_transport_error_carries_http_status(wired):
    provider, module, fake = wired

    def refuse(url, headers, on_http_status, timeout):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def session():
            on_http_status(401)
            raise RuntimeError("unauthorized")
            yield  # pragma: no cover

        return session()

    provider._deps = type(provider._deps)(**{**provider._deps.__dict__, "mcp_session": refuse})
    result = json.loads(provider.handle_tool_call("openviking_remember", {"content": "x"}))
    assert result["http_status"] == 401
    assert "401" in result["error"]


def test_setup_priming_fills_the_disk_cache(external_provider, fake_mcp, core_module):
    home, provider, module, _ = external_provider("tools-prime")
    catalog = core_module(module, "tool_catalog")
    catalog.clear_memory_cache()
    (home / ".env").write_text("OPENVIKING_ENDPOINT=http://127.0.0.1:19534\nOPENVIKING_API_KEY=k\n", encoding="utf-8")
    fake_mcp.tools = SERVER_TOOLS
    deps = type(provider._deps)(**{**provider._deps.__dict__, "mcp_session": fake_mcp.factory})

    assert core_module(module, "tools").prime_tool_cache(str(home), deps) is True

    cached = json.loads(catalog.cache_path(home).read_text())
    assert cached["endpoint"] == "http://127.0.0.1:19534"
    assert [t["name"] for t in cached["tools"]] == [t["name"] for t in SERVER_TOOLS]
    assert fake_mcp.sessions[0]["url"] == "http://127.0.0.1:19534/mcp"
    assert fake_mcp.sessions[0]["headers"]["Authorization"] == "Bearer k"
    assert "X-API-Key" not in fake_mcp.sessions[0]["headers"]
    catalog.clear_memory_cache()
