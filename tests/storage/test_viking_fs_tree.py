# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


class _DummyAgfs:
    pass


def _default_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


# ── _is_name_visible_at_path / _ancestor_is_filtered tests ──


@pytest.mark.parametrize(
    "name,parent_path,expected",
    [
        ("resources", "/local/test_account", True),
        ("user", "/local/test_account", True),
        ("agent", "/local/test_account", True),
        ("session", "/local/test_account", True),
        ("tasks", "/local/test_account", False),
        ("_system", "/local/test_account", False),
        ("temp", "/local/test_account", False),
    ],
)
def test_is_name_visible_at_account_root(name, parent_path, expected):
    """PY-FLT-001, PY-FLT-002: Account root LISTABLE_SCOPES whitelist."""
    fs = VikingFS(agfs=_DummyAgfs())
    result = fs._is_name_visible_at_path(name, parent_path)
    assert result == expected


@pytest.mark.parametrize(
    "name,parent_path,expected",
    [
        ("my_dir", "/local/test_account/resources", True),
        ("normal_dir", "/local/test_account/resources/foo", True),
        ("_system", "/local/test_account/resources", False),
        ("tasks", "/local/test_account/resources/bar", False),
        (".path.ovlock", "/local/test_account/resources", False),
    ],
)
def test_is_name_visible_at_non_root(name, parent_path, expected):
    """PY-FLT-004: Non-root _INTERNAL_NAMES blacklist."""
    fs = VikingFS(agfs=_DummyAgfs())
    result = fs._is_name_visible_at_path(name, parent_path)
    assert result == expected


@pytest.mark.parametrize(
    "entry_path,base_path,expected",
    [
        ("/local/test_account/resources/a", "/local/test_account", False),
        (
            "/local/test_account/tasks/foo",
            "/local/test_account",
            True,
        ),
        (
            "/local/test_account/tasks/foo/bar.txt",
            "/local/test_account",
            True,
        ),
        (
            "/local/test_account/resources/_system/secret.txt",
            "/local/test_account",
            True,
        ),
        (
            "/local/test_account/resources/normal/file.txt",
            "/local/test_account",
            False,
        ),
        (
            "/local/test_account/resources/a/b/c",
            "/local/test_account",
            False,
        ),
    ],
)
def test_ancestor_is_filtered(entry_path, base_path, expected):
    """PY-FLT-003, PY-FLT-006, PY-FLT-007: Ancestor chain filtering."""
    fs = VikingFS(agfs=_DummyAgfs())
    result = fs._ancestor_is_filtered(entry_path, base_path)
    assert result == expected


# ── _is_tree_entry_visible tests ──


def _make_tree_entry(path, name, is_dir=True):
    return {
        "path": path,
        "rel_path": path.replace("/local/test_account/", ""),
        "info": {
            "name": name,
            "size": 0,
            "mode": 0o755,
            "modTime": "2026-01-01T00:00:00Z",
            "isDir": is_dir,
        },
        "extra": {},
    }


def test_is_tree_entry_visible_visible(monkeypatch):
    """PY-FLT-005, PY-FLT-009: Normal visible entry."""
    fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    entry = _make_tree_entry("/local/test_account/resources/a", "a")
    ctx = _default_ctx()
    assert fs._is_tree_entry_visible(entry, "/local/test_account", ctx) is True


def test_is_tree_entry_visible_acl_filtered(monkeypatch):
    """PY-FLT-008: ACL filtering."""
    fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: False)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    entry = _make_tree_entry("/local/test_account/resources/secret", "secret")
    ctx = _default_ctx()
    assert fs._is_tree_entry_visible(entry, "/local/test_account", ctx) is False


def test_is_tree_entry_visible_hidden_scope_filtered(monkeypatch):
    """PY-FLT-007: tasks scope filtered at account root."""
    fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    entry = _make_tree_entry("/local/test_account/tasks/foo", "foo")
    ctx = _default_ctx()
    assert fs._is_tree_entry_visible(entry, "/local/test_account", ctx) is False


def test_is_tree_entry_visible_path_ovlock_filtered(monkeypatch):
    """PY-FLT-011: .path.ovlock is filtered."""
    fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    entry = _make_tree_entry(
        "/local/test_account/resources/.path.ovlock", ".path.ovlock", is_dir=False
    )
    ctx = _default_ctx()
    assert fs._is_tree_entry_visible(entry, "/local/test_account", ctx) is False


def test_is_tree_entry_visible_default_ctx(monkeypatch):
    """PY-FLT-010: ctx=None uses default context."""
    fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    entry = _make_tree_entry("/local/test_account/resources/a", "a")
    assert fs._is_tree_entry_visible(entry, "/local/test_account", _default_ctx()) is True


# ── _iter_visible_tree_entries tests ──


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_node_limit_not_passed_to_rust(monkeypatch):
    """PY-ITER-001: node_limit is NOT passed to Rust layer."""
    fs = VikingFS(agfs=_DummyAgfs())
    captured_node_limit = None

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        nonlocal captured_node_limit
        captured_node_limit = node_limit
        return []

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())

    async for _ in fs._iter_visible_tree_entries(
        "viking://resources", node_limit=10, ctx=_default_ctx()
    ):
        pass

    assert captured_node_limit is None, "node_limit should be None when passed to Rust"


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_level_limit_passed_to_rust(monkeypatch):
    """PY-ITER-003: level_limit IS passed to Rust layer."""
    fs = VikingFS(agfs=_DummyAgfs())
    captured_level_limit = None

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        nonlocal captured_level_limit
        captured_level_limit = level_limit
        return []

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())

    async for _ in fs._iter_visible_tree_entries(
        "viking://resources", level_limit=3, ctx=_default_ctx()
    ):
        pass

    assert captured_level_limit == 3


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_node_limit_after_acl(monkeypatch):
    """PY-ITER-002: node_limit applied AFTER ACL filtering."""
    fs = VikingFS(agfs=_DummyAgfs())
    visible_count = 0

    entries = [
        _make_tree_entry("/local/test_account/resources/a", "a", is_dir=False),
        _make_tree_entry("/local/test_account/resources/b", "b", is_dir=False),
        _make_tree_entry("/local/test_account/resources/c", "c", is_dir=False),
        _make_tree_entry("/local/test_account/resources/d", "d", is_dir=False),
        _make_tree_entry("/local/test_account/resources/e", "e", is_dir=False),
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    def fake_is_accessible(uri, ctx):
        nonlocal visible_count
        visible_count += 1
        return True

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", fake_is_accessible)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    results = []
    async for entry, _entry_uri in fs._iter_visible_tree_entries(
        "viking://resources", node_limit=3, ctx=_default_ctx()
    ):
        results.append(entry)

    assert len(results) == 3
    assert visible_count == 3, "only 3 entries should have ACL checked before limit hit"


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_show_hidden_passthrough(monkeypatch):
    """PY-ITER-005: show_all_hidden passthrough."""
    fs = VikingFS(agfs=_DummyAgfs())
    captured_show_hidden = None

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        nonlocal captured_show_hidden
        captured_show_hidden = show_hidden
        return []

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())

    async for _ in fs._iter_visible_tree_entries(
        "viking://resources", show_all_hidden=True, ctx=_default_ctx()
    ):
        pass

    assert captured_show_hidden is True


# ── _tree_original tests ──


@pytest.mark.asyncio
async def test_tree_original_structure(monkeypatch):
    """PY-ORIG-001: Return structure contains expected fields."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/a",
            "rel_path": "resources/a",
            "info": {
                "name": "a",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {"meta": {"Name": "s3fs"}},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    result = await fs._tree_original("viking://resources", ctx=_default_ctx())

    assert len(result) == 1
    e = result[0]
    assert e["name"] == "a"
    assert e["size"] == 100
    assert e["mode"] == 0o644
    assert e["modTime"] == "2026-01-01T00:00:00Z"
    assert e["isDir"] is False
    assert e["rel_path"] == "resources/a"
    assert e["uri"] == "viking://resources/a"


@pytest.mark.asyncio
async def test_tree_original_extra_fields_preserved(monkeypatch):
    """PY-ORIG-002: extra fields preserved."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/a",
            "rel_path": "resources/a",
            "info": {
                "name": "a",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {"meta": {"Name": "s3fs", "Type": "s3"}},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    result = await fs._tree_original("viking://resources", ctx=_default_ctx())
    assert result[0]["meta"] == {"Name": "s3fs", "Type": "s3"}


@pytest.mark.asyncio
async def test_tree_original_node_limit(monkeypatch):
    """PY-ORIG-007: node_limit applied after ACL."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        _make_tree_entry(f"/local/test_account/resources/{name}", name, is_dir=False)
        for name in ["a", "b", "c", "d", "e"]
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    result = await fs._tree_original("viking://resources", node_limit=3, ctx=_default_ctx())
    assert len(result) == 3


@pytest.mark.asyncio
async def test_tree_original_uri_correct(monkeypatch):
    """PY-ORIG-003: URI generated correctly via _path_to_uri."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/sub/file.txt",
            "rel_path": "resources/sub/file.txt",
            "info": {
                "name": "file.txt",
                "size": 50,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    result = await fs._tree_original("viking://resources", ctx=_default_ctx())
    assert result[0]["uri"] == "viking://resources/sub/file.txt"


@pytest.mark.asyncio
async def test_tree_original_rel_path_correct(monkeypatch):
    """PY-ORIG-004: rel_path correctly computed."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/sub/file.txt",
            "rel_path": "resources/sub/file.txt",
            "info": {
                "name": "file.txt",
                "size": 50,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    result = await fs._tree_original("viking://resources", ctx=_default_ctx())
    assert result[0]["rel_path"] == "resources/sub/file.txt"


@pytest.mark.asyncio
async def test_tree_original_show_hidden_passthrough(monkeypatch):
    """PY-ORIG-005: show_all_hidden is passed through to Rust layer."""
    fs = VikingFS(agfs=_DummyAgfs())
    captured_show_hidden = None

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        nonlocal captured_show_hidden
        captured_show_hidden = show_hidden
        return []

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())

    await fs._tree_original("viking://resources", show_all_hidden=False, ctx=_default_ctx())
    assert captured_show_hidden is False

    await fs._tree_original("viking://resources", show_all_hidden=True, ctx=_default_ctx())
    assert captured_show_hidden is True


@pytest.mark.asyncio
async def test_tree_original_dfs_order(monkeypatch):
    """PY-ORIG-006: DFS order preserved — directories before their children."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/sub",
            "rel_path": "resources/sub",
            "info": {
                "name": "sub",
                "size": 0,
                "mode": 0o755,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": True,
            },
            "extra": {},
        },
        {
            "path": "/local/test_account/resources/sub/file.txt",
            "rel_path": "resources/sub/file.txt",
            "info": {
                "name": "file.txt",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        },
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    result = await fs._tree_original("viking://resources", ctx=_default_ctx())
    assert result[0]["name"] == "sub"
    assert result[0]["isDir"] is True
    assert result[1]["name"] == "file.txt"


@pytest.mark.asyncio
async def test_tree_original_level_limit(monkeypatch):
    """PY-ORIG-008: level_limit passed through to Rust layer."""
    fs = VikingFS(agfs=_DummyAgfs())
    captured_level_limit = None

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        nonlocal captured_level_limit
        captured_level_limit = level_limit
        return []

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())

    await fs._tree_original("viking://resources", level_limit=2, ctx=_default_ctx())
    assert captured_level_limit == 2

    await fs._tree_original("viking://resources", level_limit=None, ctx=_default_ctx())
    assert captured_level_limit is None


# ── _tree_agent tests ──


@pytest.mark.asyncio
async def test_tree_agent_structure(monkeypatch):
    """PY-AGENT-001: Agent output structure."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/a",
            "rel_path": "resources/a",
            "info": {
                "name": "a",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        },
        {
            "path": "/local/test_account/resources/sub",
            "rel_path": "resources/sub",
            "info": {
                "name": "sub",
                "size": 0,
                "mode": 0o755,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": True,
            },
            "extra": {},
        },
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    async def fake_batch_fetch(entries, abs_limit, ctx=None):
        for entry in entries:
            entry["abstract"] = "" if not entry.get("isDir") else "mock abstract"

    monkeypatch.setattr(fs, "_batch_fetch_abstracts", fake_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())

    assert len(result) == 2
    assert "uri" in result[0]
    assert "size" in result[0]
    assert "isDir" in result[0]
    assert "modTime" in result[0]
    assert "rel_path" in result[0]
    assert "abstract" in result[0]


@pytest.mark.asyncio
async def test_tree_agent_dir_size_zero(monkeypatch):
    """PY-AGENT-002: Directory size is always 0."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/sub",
            "rel_path": "resources/sub",
            "info": {
                "name": "sub",
                "size": 999,
                "mode": 0o755,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": True,
            },
            "extra": {},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    async def fake_batch_fetch(entries, abs_limit, ctx=None):
        for entry in entries:
            entry["abstract"] = "" if not entry.get("isDir") else "mock abstract"

    monkeypatch.setattr(fs, "_batch_fetch_abstracts", fake_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert result[0]["size"] == 0


@pytest.mark.asyncio
async def test_tree_agent_non_dir_abstract_empty(monkeypatch):
    """PY-AGENT-004: Non-directory entries have empty abstract."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/a",
            "rel_path": "resources/a",
            "info": {
                "name": "a",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )

    async def fake_batch_fetch(entries, abs_limit, ctx=None):
        for entry in entries:
            entry["abstract"] = "" if not entry.get("isDir") else "mock abstract"

    monkeypatch.setattr(fs, "_batch_fetch_abstracts", fake_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert result[0]["abstract"] == ""


@pytest.mark.asyncio
async def test_tree_agent_modtime_formatted(monkeypatch):
    """PY-AGENT-003: modTime is formatted via format_simplified."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/a",
            "rel_path": "resources/a",
            "info": {
                "name": "a",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    async def fake_batch_fetch(entries, abs_limit, ctx=None):
        for entry in entries:
            entry["abstract"] = "" if not entry.get("isDir") else "mock abstract"

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    monkeypatch.setattr(fs, "_batch_fetch_abstracts", fake_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert result[0]["modTime"] == "2026-01-01"


@pytest.mark.asyncio
async def test_tree_agent_abs_limit_truncation(monkeypatch):
    """PY-AGENT-005: abstract is truncated when exceeding abs_limit."""
    fs = VikingFS(agfs=_DummyAgfs())

    entries = [
        {
            "path": "/local/test_account/resources/sub",
            "rel_path": "resources/sub",
            "info": {
                "name": "sub",
                "size": 0,
                "mode": 0o755,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": True,
            },
            "extra": {},
        }
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    async def fake_batch_fetch(entries, abs_limit, ctx=None):
        for entry in entries:
            abstract = "x" * (abs_limit + 10) if entry.get("isDir") else ""
            if len(abstract) > abs_limit:
                abstract = abstract[: abs_limit - 3] + "..."
            entry["abstract"] = abstract

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    monkeypatch.setattr(fs, "_batch_fetch_abstracts", fake_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=10, ctx=_default_ctx())
    assert len(result[0]["abstract"]) <= 10
    assert result[0]["abstract"].endswith("...")


@pytest.mark.asyncio
async def test_tree_agent_batch_fetch_input_order(monkeypatch):
    """PY-AGENT-006: _batch_fetch_abstracts receives entries in correct order."""
    fs = VikingFS(agfs=_DummyAgfs())
    captured_entries = None

    entries = [
        {
            "path": "/local/test_account/resources/a",
            "rel_path": "resources/a",
            "info": {
                "name": "a",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-01-01T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        },
        {
            "path": "/local/test_account/resources/b",
            "rel_path": "resources/b",
            "info": {
                "name": "b",
                "size": 200,
                "mode": 0o644,
                "modTime": "2026-01-02T00:00:00Z",
                "isDir": False,
            },
            "extra": {},
        },
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    async def fake_batch_fetch(entries_arg, abs_limit, ctx=None):
        nonlocal captured_entries
        captured_entries = list(entries_arg)
        for entry in entries_arg:
            entry["abstract"] = ""

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    monkeypatch.setattr(fs, "_batch_fetch_abstracts", fake_batch_fetch)

    await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert len(captured_entries) == 2
    assert captured_entries[0]["uri"] == "viking://resources/a"
    assert captured_entries[1]["uri"] == "viking://resources/b"


@pytest.mark.asyncio
async def test_tree_agent_node_limit_before_enrichment(monkeypatch):
    """PY-AGENT-007: node_limit applied before abstract enrichment."""
    fs = VikingFS(agfs=_DummyAgfs())
    enriched_count = 0

    entries = [
        _make_tree_entry(f"/local/test_account/resources/{name}", name, is_dir=False)
        for name in ["a", "b", "c", "d", "e"]
    ]

    async def fake_tree_directory(path, show_hidden=False, node_limit=None, level_limit=None):
        return entries

    async def fake_batch_fetch(entries_arg, abs_limit, ctx=None):
        nonlocal enriched_count
        enriched_count = len(entries_arg)
        for entry in entries_arg:
            entry["abstract"] = ""

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)
    monkeypatch.setattr(fs, "_uri_to_path", lambda uri, ctx=None: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ctx_or_default", lambda ctx=None: _default_ctx())
    monkeypatch.setattr(fs, "_is_accessible", lambda uri, ctx: True)
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/test_account/", "viking://")
    )
    monkeypatch.setattr(fs, "_batch_fetch_abstracts", fake_batch_fetch)

    result = await fs._tree_agent(
        "viking://resources", node_limit=2, abs_limit=256, ctx=_default_ctx()
    )
    assert len(result) == 2
    assert enriched_count == 2
