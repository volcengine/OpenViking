# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage import viking_fs as viking_fs_module
from openviking.storage.acl import AclLevel
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


class _DummyAgfs:
    def stat(self, _path, ctx=None):
        """Return a minimal stat payload for paths assumed to exist in tree tests."""
        return {}


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 11, 1, 0, 0, tzinfo=timezone.utc)


def _default_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


@pytest.mark.asyncio
async def test_stat_queries_lock_status_only_when_requested(monkeypatch, fs):
    path = "/local/default/resources/example.md"
    lock_queries = []

    async def resolve_uri(uri, _ctx):
        return uri

    async def ensure_access(_uri, _ctx):
        return None

    async def stat_path(_path):
        return {"name": "example.md", "isDir": False}

    async def is_path_locked(queried_path):
        lock_queries.append(queried_path)
        return True

    monkeypatch.setattr(fs, "resolve_uri", resolve_uri)
    monkeypatch.setattr(fs, "_ensure_access", ensure_access)
    monkeypatch.setattr(fs, "_uri_to_path", lambda _uri, **_kwargs: path)
    monkeypatch.setattr(fs._async_agfs, "stat", stat_path)
    monkeypatch.setattr(fs, "_is_path_locked_async", is_path_locked)

    result = await fs.stat("viking://resources/example.md", ctx=_default_ctx())

    assert "isLocked" not in result
    assert lock_queries == []

    result = await fs.stat(
        "viking://resources/example.md",
        ctx=_default_ctx(),
        include_lock_status=True,
    )

    assert result["isLocked"] is True
    assert lock_queries == [path]


@pytest.mark.asyncio
async def test_stat_returns_l2_vector_count_as_count(monkeypatch, fs):
    path = "/local/default/resources/docs"
    vector_store = SimpleNamespace(
        count=AsyncMock(return_value=4),
    )

    async def resolve_uri(uri, _ctx):
        return uri

    async def stat_path(_path):
        return {"name": "docs", "isDir": True}

    monkeypatch.setattr(fs, "resolve_uri", resolve_uri)
    monkeypatch.setattr(fs, "_uri_to_path", lambda _uri, **_kwargs: path)
    monkeypatch.setattr(fs._async_agfs, "stat", stat_path)
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)

    result = await fs.stat("viking://resources/docs", ctx=_default_ctx())

    assert result["count"] == 4
    assert "l2_count" not in result
    vector_store.count.assert_awaited_once_with(
        filter=And(
            [
                PathScope("uri", "viking://resources/docs", depth=-1),
                Eq("level", 2),
            ]
        ),
        ctx=_default_ctx(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_fields", "expected", "count_calls"),
    [
        (["count"], {"count": 4}, 1),
        (["l2_count"], {}, 0),
        (["count", "l2_count"], {"count": 4}, 1),
    ],
)
async def test_directory_count_is_l2_only_and_legacy_l2_count_is_ignored(
    monkeypatch,
    fs,
    extra_fields,
    expected,
    count_calls,
):
    vector_store = SimpleNamespace(
        count=AsyncMock(return_value=4),
    )
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)
    entry = {
        "name": "docs",
        "isDir": True,
        "uri": "viking://resources/docs",
    }

    await fs._augment_entries_extra_fields([entry], extra_fields, ctx=_default_ctx())

    assert {key: entry[key] for key in expected} == expected
    assert "l2_count" not in entry
    assert vector_store.count.await_count == count_calls
    if count_calls:
        vector_store.count.assert_awaited_once_with(
            filter=And(
                [
                    PathScope("uri", "viking://resources/docs", depth=-1),
                    Eq("level", 2),
                ]
            ),
            ctx=_default_ctx(),
        )


# ── Shared fixtures / builders ──────────────────────────────────────────────


@pytest.fixture
def fs() -> VikingFS:
    """Fresh VikingFS backed by a dummy AGFS client."""
    return VikingFS(agfs=_DummyAgfs())


def _std_path_to_uri(path, **_kwargs):
    """Map account-isolated path back to a viking:// URI (test convention)."""
    return path.replace("/local/test_account/", "viking://")


def make_entry(
    path,
    name=None,
    *,
    size=0,
    mode=0o755,
    mod_time="2026-01-01T00:00:00Z",
    is_dir=True,
    extra=None,
):
    """Build a Rust-shaped TreeEntry dict for tests.

    ``rel_path`` is normalized by ``patch_tree_env`` for tree traversal tests,
    matching the binding contract where it is relative to the query root.
    ``name`` defaults to the last path component when not given.
    """
    if name is None:
        name = path.rstrip("/").rsplit("/", 1)[-1]
    return {
        "path": path,
        "rel_path": path.replace("/local/test_account/", ""),
        "info": {
            "name": name,
            "size": size,
            "mode": mode,
            "modTime": mod_time,
            "isDir": is_dir,
        },
        "extra": extra or {},
    }


def _query_relative_path(path: str, query_root: str) -> str:
    base = query_root.rstrip("/")
    normalized = path.rstrip("/")
    if normalized == base:
        return ""
    if normalized.startswith(f"{base}/"):
        return normalized[len(base) + 1 :]
    return path.replace("/local/test_account/", "")


def _with_query_relative_paths(entries, query_root: str):
    normalized_entries = []
    for entry in entries:
        normalized = dict(entry)
        normalized["rel_path"] = _query_relative_path(str(entry["path"]), query_root)
        normalized_entries.append(normalized)
    return normalized_entries


def patch_visibility(monkeypatch, fs, *, is_accessible=True):
    """Patch ACL + URI mapping used by visibility checks."""
    monkeypatch.setattr(
        fs,
        "_is_accessible",
        is_accessible if callable(is_accessible) else (lambda _uri, _ctx: is_accessible),
    )
    monkeypatch.setattr(fs, "_path_to_uri", _std_path_to_uri)


def patch_tree_env(
    monkeypatch,
    fs,
    entries_or_fn,
    *,
    is_accessible=True,
    uri_to_path="/local/test_account/resources",
):
    """Install the standard monkeypatch set for tree traversal tests.

    ``entries_or_fn`` may be a list of entries (returned by a generated fake
    ``tree_directory``) or a custom async ``tree_directory`` callable when a
    test needs to capture the arguments passed to Rust.
    """
    if callable(entries_or_fn):
        fake_tree = entries_or_fn
    else:

        async def fake_tree(path, **_kwargs):
            return _with_query_relative_paths(entries_or_fn, path)

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree)
    monkeypatch.setattr(fs, "_uri_to_path", lambda _uri, **_kwargs: uri_to_path)
    monkeypatch.setattr(fs, "_ctx_or_default", lambda _ctx=None: _default_ctx())
    patch_visibility(monkeypatch, fs, is_accessible=is_accessible)


@pytest.mark.asyncio
async def test_tree_filters_hidden_subtrees_and_preserves_nested_user_names(monkeypatch, fs):
    root = "/local/test_account"
    visible_paths = [
        "resources",
        "resources/tasks",
        "resources/tasks/demo.md",
        "resources/_system",
        "resources/_system/notes.md",
        "user",
        "agent",
        "system",
    ]
    hidden_paths = [
        "session",
        "tasks/hidden.md",
        "_system",
        "temp",
        "resources/.path.ovlock/child",
        "resources/.exact.ovlock.notes.md.0123abcd",
        "resources/.sync_log.json",
        "resources/.redirect.json",
        "resources/secret.md",
    ]
    entries = [
        make_entry(f"{root}/{path}", is_dir=not path.endswith(".md"))
        for path in visible_paths + hidden_paths
    ]
    patch_tree_env(
        monkeypatch,
        fs,
        entries,
        is_accessible=lambda uri, _ctx: uri != "viking://resources/secret.md",
        uri_to_path=root,
    )

    result = await fs.tree("viking://", show_all_hidden=True, ctx=_default_ctx())

    assert [entry["uri"] for entry in result] == [f"viking://{path}" for path in visible_paths]


# ── _iter_visible_tree_entries tests ──


@pytest.mark.asyncio
async def test_tree_offsets_count_visible_entries_across_backend_pages(monkeypatch, fs):
    invisible = [
        make_entry(f"/local/test_account/resources/hidden-{i}.md", is_dir=False) for i in range(256)
    ]
    visible = [
        make_entry(f"/local/test_account/resources/{name}.md", is_dir=False)
        for name in ("skip", "first", "second", "last")
    ]
    entries = invisible + visible
    pages = []

    async def fake_tree_directory(path, *, offset, node_limit, **kwargs):
        pages.append((offset, node_limit))
        end = offset + node_limit if node_limit is not None else None
        return _with_query_relative_paths(entries[offset:end], path)

    patch_tree_env(
        monkeypatch,
        fs,
        fake_tree_directory,
        is_accessible=lambda uri, _ctx: "/hidden-" not in uri,
    )
    ctx = _default_ctx()

    limited = await fs.tree("viking://resources", offset=1, node_limit=2, ctx=ctx)
    assert [entry["name"] for entry in limited] == ["first.md", "second.md"]
    assert pages == [(0, 256), (256, 256)]
    pages.clear()

    unbounded = await fs.tree("viking://resources", node_limit=None, ctx=ctx)
    assert [entry["name"] for entry in unbounded] == ["skip.md", "first.md", "second.md", "last.md"]
    assert pages == [(0, None)]


# ── _tree_original tests ──


@pytest.mark.asyncio
async def test_tree_original_structure(monkeypatch, fs):
    """PY-ORIG-001: Return structure contains expected fields."""
    entries = [
        make_entry(
            "/local/test_account/resources/a",
            "a",
            size=100,
            mode=0o644,
            is_dir=False,
            extra={"meta": {"Name": "s3fs", "Type": "s3"}},
        )
    ]
    patch_tree_env(monkeypatch, fs, entries)

    result = await fs.tree("viking://resources", output="original", ctx=_default_ctx())

    assert len(result) == 1
    e = result[0]
    assert e["name"] == "a"
    assert e["size"] == 100
    assert e["mode"] == 0o644
    assert e["modTime"] == "2026-01-01T00:00:00Z"
    assert e["isDir"] is False
    assert e["rel_path"] == "a"
    assert e["uri"] == "viking://resources/a"
    assert "abstract" not in e
    assert "overview" not in e

    assert e["meta"] == {"Name": "s3fs", "Type": "s3"}


@pytest.mark.asyncio
async def test_tree_preserves_order_and_only_enriches_accessible_entries(monkeypatch, fs):
    entries = [
        make_entry("/local/test_account/resources/sub", "sub", is_dir=True),
        make_entry(
            "/local/test_account/resources/sub/file.txt",
            "file.txt",
            size=100,
            mode=0o644,
            is_dir=False,
        ),
        make_entry("/local/test_account/resources/restricted", "restricted", is_dir=True),
        make_entry(
            "/local/test_account/resources/restricted/hidden.txt",
            "hidden.txt",
            is_dir=False,
        ),
    ]
    patch_tree_env(monkeypatch, fs, entries)

    async def acl_enabled(_account_id):
        return True

    fs.acl_manager = SimpleNamespace(is_enabled=acl_enabled)

    async def fake_permissions_many(uris, _ctx):
        return {uri: None if "/restricted" in uri else AclLevel.READ for uri in uris}

    monkeypatch.setattr(fs, "_permissions_many", fake_permissions_many)

    lookup_lock = AsyncMock(return_value=True)
    monkeypatch.setattr(fs, "_is_path_locked_async", lookup_lock)

    result = await fs.tree("viking://resources", extra_fields=["locked", "id"], ctx=_default_ctx())
    assert lookup_lock.await_count == 2
    assert [entry.get("isLocked") for entry in result] == [True, True, None]
    assert "id" in result[1]
    assert result[0]["name"] == "sub"
    assert result[0]["isDir"] is True
    assert result[0]["rel_path"] == "sub"
    assert result[1]["name"] == "file.txt"
    assert result[1]["rel_path"] == "sub/file.txt"
    assert result[2] == {
        "name": "restricted",
        "isDir": True,
        "rel_path": "restricted",
        "uri": "viking://resources/restricted",
        "access": "denied",
    }
    assert len(result) == 3


# ── tree agent output tests ──


@pytest.mark.asyncio
async def test_tree_agent_structure(monkeypatch, fs):
    """Agent defaults and explicit summary controls preserve the listing contract."""
    entries = [
        make_entry(
            "/local/test_account/resources/a",
            "a",
            size=100,
            is_dir=False,
            mod_time="2026-06-11T00:30:17+08:00",
        ),
        make_entry("/local/test_account/resources/sub", "sub", size=999, is_dir=True),
    ]
    patch_tree_env(monkeypatch, fs, entries)
    summary = AsyncMock(
        side_effect=lambda uri, field, ctx: "L0 summary" if field == "abstract" else "L1 overview"
    )
    monkeypatch.setattr(fs, "_read_directory_summary", summary)

    result = await fs.tree("viking://resources", output="agent", ctx=_default_ctx())

    assert result == [
        {
            "uri": "viking://resources/a",
            "size": 100,
            "isDir": False,
            "modTime": "2026-06-10T16:30:17.000Z",
            "rel_path": "a",
            "abstract": "",
            "access": "manage",
        },
        {
            "uri": "viking://resources/sub",
            "size": 0,
            "isDir": True,
            "modTime": "2026-01-01T00:00:00.000Z",
            "rel_path": "sub",
            "abstract": "L0 summary",
            "access": "manage",
        },
    ]
    summary.assert_awaited_once_with("viking://resources/sub", "abstract", ctx=_default_ctx())
    summary.reset_mock()

    result = await fs.tree(
        "viking://resources",
        output="agent",
        include_abstract=False,
        include_overview=True,
        ctx=_default_ctx(),
    )

    assert all("abstract" not in entry for entry in result)
    assert [entry["overview"] for entry in result] == ["", "L1 overview"]
    summary.assert_awaited_once_with("viking://resources/sub", "overview", ctx=_default_ctx())


@pytest.mark.asyncio
async def test_ls_agent_preserves_metadata_acl_and_summary_controls(monkeypatch, fs):
    async def fake_ls_entries(_path, **_kwargs):
        return [
            {
                "name": "a.md",
                "size": 100,
                "mode": 0o644,
                "modTime": "2026-06-11T00:30:17+08:00",
                "isDir": False,
            },
            {
                "name": "restricted",
                "size": 4096,
                "mode": 0o755,
                "modTime": "2026-06-11T00:30:18+08:00",
                "isDir": True,
            },
            {"name": "docs", "size": 4096, "mode": 0o755, "modTime": "", "isDir": True},
        ]

    monkeypatch.setattr(fs, "_uri_to_path", lambda _uri, **_kwargs: "/local/test_account/resources")
    monkeypatch.setattr(fs, "_ls_entries", fake_ls_entries)
    monkeypatch.setattr(fs, "_path_to_uri", _std_path_to_uri)
    monkeypatch.setattr(fs, "_is_accessible", lambda _uri, _ctx: True)
    monkeypatch.setattr(viking_fs_module, "datetime", _FixedDatetime)

    async def acl_enabled(_account_id):
        return True

    fs.acl_manager = SimpleNamespace(is_enabled=acl_enabled)

    async def fake_permissions_many(uris, _ctx):
        return {uri: None if uri.endswith("/restricted") else AclLevel.WRITE for uri in uris}

    monkeypatch.setattr(fs, "_permissions_many", fake_permissions_many)
    summary = AsyncMock(
        side_effect=lambda uri, field, ctx: "L0 summary" if field == "abstract" else "L1 overview"
    )
    monkeypatch.setattr(fs, "_read_directory_summary", summary)

    result = await fs.ls(
        "viking://resources",
        output="agent",
        abs_limit=256,
        show_all_hidden=False,
        ctx=_default_ctx(),
    )

    assert set(result[0].keys()) == {"uri", "size", "isDir", "modTime", "abstract", "access"}
    assert result[0]["access"] == "write"
    assert result[0]["modTime"] == "2026-06-10T16:30:17.000Z"
    assert result[1] == {
        "name": "restricted",
        "uri": "viking://resources/restricted",
        "isDir": True,
        "access": "denied",
    }
    assert result[2]["abstract"] == "L0 summary"
    summary.assert_awaited_once_with("viking://resources/docs", "abstract", ctx=_default_ctx())
    summary.reset_mock()

    result = await fs.ls(
        "viking://resources",
        output="agent",
        include_abstract=False,
        include_overview=True,
        ctx=_default_ctx(),
    )
    assert all("abstract" not in entry for entry in result)
    assert result[0]["overview"] == ""
    assert "overview" not in result[1]
    assert result[2]["overview"] == "L1 overview"
    summary.assert_awaited_once_with("viking://resources/docs", "overview", ctx=_default_ctx())


@pytest.mark.asyncio
async def test_tree_filters_and_paginates_before_summary_reads(monkeypatch, fs):
    """Only selected directories consume node slots and summary reads."""
    entries = [
        make_entry(f"/local/test_account/resources/{name}", is_dir=is_dir)
        for name, is_dir in [
            ("file-a.md", False),
            ("dir-a", True),
            ("dir-a/.abstract.md", False),
            ("dir-a/.overview.md", False),
            ("file-b.md", False),
            ("dir-b", True),
            ("dir-c", True),
            ("dir-d", True),
        ]
    ]

    async def fake_tree_directory(path, **kwargs):
        assert kwargs["show_hidden"] is True
        assert kwargs["level_limit"] == 2
        filtered = entries
        if kwargs.get("directories_only"):
            filtered = [entry for entry in entries if entry["info"]["isDir"]]
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("node_limit")
        page = filtered[offset : offset + limit if limit is not None else None]
        return _with_query_relative_paths(page, path)

    patch_tree_env(monkeypatch, fs, fake_tree_directory)
    summary = AsyncMock(
        side_effect=lambda uri, field, ctx: "L0 summary" if field == "abstract" else "L1 overview"
    )
    monkeypatch.setattr(fs, "_read_directory_summary", summary)

    result = await fs.tree(
        "viking://resources",
        output="original",
        directories_only=True,
        show_all_hidden=True,
        offset=1,
        node_limit=2,
        level_limit=2,
        include_abstract=True,
        include_overview=True,
        abs_limit=5,
        overview_limit=7,
        ctx=_default_ctx(),
    )

    assert [entry["uri"] for entry in result] == [
        "viking://resources/dir-b",
        "viking://resources/dir-c",
    ]
    assert all(entry["isDir"] for entry in result)
    assert [(entry["abstract"], entry["overview"]) for entry in result] == [
        ("L0...", "L1 o..."),
        ("L0...", "L1 o..."),
    ]
    for field in ("abstract", "overview"):
        assert sorted(
            item.args[0] for item in summary.await_args_list if item.args[1] == field
        ) == [
            "viking://resources/dir-b",
            "viking://resources/dir-c",
        ]
