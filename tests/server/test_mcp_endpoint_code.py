# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the code-navigation MCP tools (code_outline / code_search /
code_expand) in openviking/server/mcp_endpoint.py.

The pure formatters in openviking.parse.parsers.code.ast.code_tools are
covered by tests/parse/test_code_tools.py. These tests cover the MCP wiring:
URI validation, service.fs.read / service.fs.ls plumbing, extension filtering,
the 1000-file cap, and error mapping.
"""

import json
from types import SimpleNamespace

import pytest

from openviking.parse.parsers.code.ast.code_tools import (
    CODE_SCAN_LS_LEVEL_LIMIT,
    CODE_SCAN_LS_NODE_LIMIT,
    CODE_SEARCH_FILE_CAP,
)
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_server_config, set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.mcp_endpoint import (
    _filter_code_uris,
    _mcp_ctx,
    _require_viking_uri,
    code_expand,
    code_locate,
    code_outline,
    code_search,
)
from openviking_cli.session.user_id import UserIdentifier

DEFAULT_CTX = RequestContext(
    user=UserIdentifier.the_default_user("test_user"),
    role=Role.ROOT,
)


PY_SAMPLE = '''"""Module top doc."""


class Greeter:
    def greet(self, who: str) -> str:
        return f"Hello {who}"


def make_greeter() -> Greeter:
    return Greeter()
'''


@pytest.fixture(autouse=True)
def _set_mcp_identity(service):
    """Set identity contextvar and wire service for all tests."""
    set_service(service)
    set_server_config(ServerConfig())
    token = _mcp_ctx.set(DEFAULT_CTX)
    yield
    _mcp_ctx.reset(token)
    set_server_config(ServerConfig())


def _patch_fs(monkeypatch, service, *, read=None, ls=None):
    """Replace service.fs.read / .ls with async fakes."""
    if read is not None:
        monkeypatch.setattr(service.fs, "read", read)
    if ls is not None:
        monkeypatch.setattr(service.fs, "ls", ls)


# ---------------------------------------------------------------------------
# _require_viking_uri (internal helper, but small contract worth pinning)
# ---------------------------------------------------------------------------


class TestRequireVikingUri:
    def test_accepts_viking_uri(self):
        assert _require_viking_uri("viking://resources/foo.py") is None

    def test_rejects_local_path(self):
        msg = _require_viking_uri("/tmp/foo.py")
        assert msg is not None
        assert "viking://" in msg

    def test_rejects_http_url(self):
        msg = _require_viking_uri("https://example.com/foo.py")
        assert msg is not None

    def test_rejects_non_string(self):
        assert _require_viking_uri(None) is not None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _filter_code_uris
# ---------------------------------------------------------------------------


class TestFilterCodeUris:
    def test_keeps_supported_extensions(self):
        entries = [
            {"uri": "viking://r/a.py", "isDir": False},
            {"uri": "viking://r/b.md", "isDir": False},
            {"uri": "viking://r/c.ts", "isDir": False},
            {"uri": "viking://r/d.txt", "isDir": False},
        ]
        uris, capped = _filter_code_uris(entries)
        assert uris == ["viking://r/a.py", "viking://r/c.ts"]
        assert capped is False

    def test_skips_directories(self):
        entries = [
            {"uri": "viking://r/sub", "isDir": True},
            {"uri": "viking://r/a.py", "isDir": False},
        ]
        uris, capped = _filter_code_uris(entries)
        assert uris == ["viking://r/a.py"]
        assert capped is False

    def test_supports_object_entries(self):
        entries = [
            SimpleNamespace(uri="viking://r/a.py", is_dir=False),
            SimpleNamespace(uri="viking://r/sub", is_dir=True),
        ]
        uris, capped = _filter_code_uris(entries)
        assert uris == ["viking://r/a.py"]
        assert capped is False

    def test_caps_at_search_file_cap(self):
        entries = [
            {"uri": f"viking://r/f{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP + 1)
        ]
        uris, capped = _filter_code_uris(entries)
        assert len(uris) == CODE_SEARCH_FILE_CAP
        assert capped is True

    def test_exactly_search_file_cap_not_capped(self):
        entries = [
            {"uri": f"viking://r/f{i}.py", "isDir": False}
            for i in range(CODE_SEARCH_FILE_CAP)
        ]
        uris, capped = _filter_code_uris(entries)
        assert len(uris) == CODE_SEARCH_FILE_CAP
        assert capped is False


# ---------------------------------------------------------------------------
# code_outline
# ---------------------------------------------------------------------------


class TestCodeOutline:
    async def test_rejects_non_viking_uri(self, service):
        out = await code_outline("/tmp/foo.py")
        assert "viking://" in out

    async def test_outline_via_fs_read(self, service, monkeypatch):
        captured = {}

        async def fake_read(uri, ctx=None, **_):
            captured["uri"] = uri
            captured["ctx"] = ctx
            return PY_SAMPLE

        _patch_fs(monkeypatch, service, read=fake_read)

        out = await code_outline("viking://resources/greeter.py")
        assert captured["uri"] == "viking://resources/greeter.py"
        assert captured["ctx"] == DEFAULT_CTX
        assert "class Greeter" in out
        assert "def make_greeter" in out
        assert "  L" in out  # line spans rendered

    async def test_unsupported_language_returns_sentinel(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return "# heading"

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_outline("viking://resources/notes.md")
        assert out.startswith("Error: unsupported language")

    async def test_read_failure(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            raise RuntimeError("boom")

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_outline("viking://resources/x.py")
        assert out.startswith("Error: failed to read")
        assert "boom" in out

    async def test_non_text_content(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return b"\x00\x01binary"

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_outline("viking://resources/x.py")
        assert out.endswith("is not text")


# ---------------------------------------------------------------------------
# code_search
# ---------------------------------------------------------------------------


class TestCodeSearch:
    async def test_rejects_non_viking_uri(self, service):
        out = await code_search("foo", "/tmp/dir")
        assert "viking://" in out

    async def test_empty_query(self, service):
        out = await code_search("", "viking://resources")
        assert out == "Error: empty query"

    async def test_lists_and_searches(self, service, monkeypatch):
        ls_calls = {}
        read_uris: list[str] = []

        async def fake_ls(uri, ctx=None, recursive=False, output=None, **kwargs):
            ls_calls["uri"] = uri
            ls_calls["ctx"] = ctx
            ls_calls["recursive"] = recursive
            ls_calls["output"] = output
            ls_calls["node_limit"] = kwargs.get("node_limit")
            ls_calls["level_limit"] = kwargs.get("level_limit")
            return [
                {"uri": "viking://r/a.py", "isDir": False},
                {"uri": "viking://r/sub", "isDir": True},
                {"uri": "viking://r/b.md", "isDir": False},
                {"uri": "viking://r/c.py", "isDir": False},
            ]

        async def fake_read(uri, ctx=None, **_):
            read_uris.append(uri)
            if uri.endswith("a.py"):
                return PY_SAMPLE
            if uri.endswith("c.py"):
                return "def other():\n    pass\n"
            return ""

        _patch_fs(monkeypatch, service, ls=fake_ls, read=fake_read)

        out = await code_search("greet", "viking://r")
        assert ls_calls["recursive"] is True
        assert ls_calls["output"] == "original"
        assert ls_calls["ctx"] == DEFAULT_CTX
        assert ls_calls["node_limit"] == CODE_SCAN_LS_NODE_LIMIT
        assert ls_calls["level_limit"] == CODE_SCAN_LS_LEVEL_LIMIT
        # b.md must be filtered out before any read happens
        assert "viking://r/b.md" not in read_uris
        assert set(read_uris) == {"viking://r/a.py", "viking://r/c.py"}
        # search hits Greeter + Greeter.greet in a.py
        assert "viking://r/a.py" in out
        assert "Greeter" in out

    async def test_empty_directory(self, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return []

        _patch_fs(monkeypatch, service, ls=fake_ls)
        out = await code_search("greet", "viking://empty")
        assert "No supported source files" in out

    async def test_no_code_files(self, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [{"uri": "viking://r/notes.md", "isDir": False}]

        _patch_fs(monkeypatch, service, ls=fake_ls)
        out = await code_search("greet", "viking://r")
        assert "No supported source files" in out

    async def test_ls_failure(self, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            raise RuntimeError("ls denied")

        _patch_fs(monkeypatch, service, ls=fake_ls)
        out = await code_search("greet", "viking://r")
        assert out.startswith("Error: failed to list")
        assert "ls denied" in out

    async def test_read_failures_are_skipped(self, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [
                {"uri": "viking://r/a.py", "isDir": False},
                {"uri": "viking://r/b.py", "isDir": False},
            ]

        async def fake_read(uri, ctx=None, **_):
            if uri.endswith("b.py"):
                raise RuntimeError("denied")
            return PY_SAMPLE

        _patch_fs(monkeypatch, service, ls=fake_ls, read=fake_read)
        out = await code_search("greet", "viking://r")
        # Search should still report the matches from a.py despite b.py failing.
        assert "viking://r/a.py" in out
        assert "Greeter" in out

    async def test_file_cap_warning(self, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [
                {"uri": f"viking://r/f{i}.py", "isDir": False}
                for i in range(CODE_SEARCH_FILE_CAP + 1)
            ]

        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        _patch_fs(monkeypatch, service, ls=fake_ls, read=fake_read)
        out = await code_search("greet", "viking://r")
        assert "1000-file cap" in out

    async def test_no_cap_warning_below_threshold(self, service, monkeypatch):
        async def fake_ls(uri, ctx=None, recursive=False, output=None, **_):
            return [{"uri": "viking://r/a.py", "isDir": False}]

        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        _patch_fs(monkeypatch, service, ls=fake_ls, read=fake_read)
        out = await code_search("greet", "viking://r")
        assert "1000-file cap" not in out


# ---------------------------------------------------------------------------
# code_locate
# ---------------------------------------------------------------------------


class TestCodeLocate:
    async def test_local_source_requires_explicit_server_switch(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "greeter.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

        out = await code_locate(
            "changed greet behavior",
            {"type": "local", "path": str(repo)},
            output_format="json",
        )

        assert "local code source paths are disabled" in out

    async def test_viking_source_json_lists_deep_code_tree(self, service, monkeypatch):
        ls_calls = {}

        async def fake_ls(uri, ctx=None, recursive=False, output=None, **kwargs):
            ls_calls["uri"] = uri
            ls_calls["ctx"] = ctx
            ls_calls["recursive"] = recursive
            ls_calls["output"] = output
            ls_calls["node_limit"] = kwargs.get("node_limit")
            ls_calls["level_limit"] = kwargs.get("level_limit")
            return [
                {"uri": "viking://r/sklearn/utils/_pprint.py", "isDir": False},
                {"uri": "viking://r/sklearn/utils/tests/test_pprint.py", "isDir": False},
            ]

        async def fake_read(uri, ctx=None, **_):
            if uri.endswith("test_pprint.py"):
                return "def test_changed_only_array_repr():\n    assert True\n"
            return "def _changed_params():\n    if value != init_value:\n        return True\n"

        _patch_fs(monkeypatch, service, ls=fake_ls, read=fake_read)

        out = await code_locate(
            "print_changed_only array repr",
            {"type": "viking", "uri": "viking://r"},
            output_format="json",
        )
        payload = json.loads(out)

        assert ls_calls == {
            "uri": "viking://r",
            "ctx": DEFAULT_CTX,
            "recursive": True,
            "output": "original",
            "node_limit": CODE_SCAN_LS_NODE_LIMIT,
            "level_limit": CODE_SCAN_LS_LEVEL_LIMIT,
        }
        assert payload["edit_candidates"][0]["location"]["type"] == "viking"
        assert payload["edit_candidates"][0]["location"]["uri"] == (
            "viking://r/sklearn/utils/_pprint.py"
        )
        assert "path" not in payload["edit_candidates"][0]["location"]

    async def test_local_source_json_reads_current_checkout(self, tmp_path):
        set_server_config(ServerConfig(allow_local_code_source_paths=True))
        repo = tmp_path / "repo"
        repo.mkdir()
        impl = repo / "greeter.py"
        impl.write_text(
            "class Greeter:\n    def greet(self):\n        return 'hello changed behavior'\n",
            encoding="utf-8",
        )

        out = await code_locate(
            "changed greet behavior",
            {"type": "local", "path": str(repo)},
            output_format="json",
        )
        payload = json.loads(out)

        location = payload["edit_candidates"][0]["location"]
        assert location["type"] == "local"
        assert location["path"] == str(impl)
        assert "uri" not in location


# ---------------------------------------------------------------------------
# code_expand
# ---------------------------------------------------------------------------


class TestCodeExpand:
    async def test_rejects_non_viking_uri(self, service):
        out = await code_expand("/tmp/foo.py", "Greeter")
        assert "viking://" in out

    async def test_empty_symbol(self, service):
        out = await code_expand("viking://r/a.py", "")
        assert out == "Error: empty symbol"

    async def test_expand_bare_symbol(self, service, monkeypatch):
        captured = {}

        async def fake_read(uri, ctx=None, **_):
            captured["uri"] = uri
            captured["ctx"] = ctx
            return PY_SAMPLE

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_expand("viking://r/a.py", "make_greeter")
        assert captured["uri"] == "viking://r/a.py"
        assert captured["ctx"] == DEFAULT_CTX
        assert "(make_greeter)" in out
        assert "def make_greeter" in out

    async def test_expand_qualified_symbol(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_expand("viking://r/a.py", "Greeter.greet")
        assert "(Greeter.greet)" in out
        assert "def greet" in out

    async def test_missing_symbol(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return PY_SAMPLE

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_expand("viking://r/a.py", "does_not_exist")
        assert "not found" in out
        assert "does_not_exist" in out

    async def test_read_failure(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            raise RuntimeError("boom")

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_expand("viking://r/a.py", "Greeter")
        assert out.startswith("Error: failed to read")
        assert "boom" in out

    async def test_non_text_content(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return b"\x00binary"

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_expand("viking://r/a.py", "Greeter")
        assert out.endswith("is not text")

    async def test_unsupported_language(self, service, monkeypatch):
        async def fake_read(uri, ctx=None, **_):
            return "# heading"

        _patch_fs(monkeypatch, service, read=fake_read)
        out = await code_expand("viking://r/notes.md", "anything")
        assert out.startswith("Error: unsupported language")
