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


def test_viking_fs_no_longer_exposes_python_encryption_api(fs: VikingFS):
    """VikingFS should not expose Python-side encryption helpers after ragfs migration."""
    assert not hasattr(fs, "_encrypt_content")
    assert not hasattr(fs, "_decrypt_content")
    assert not hasattr(fs, "encrypt_bytes")
    assert not hasattr(fs, "decrypt_bytes")


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

    ``rel_path`` is derived from ``path`` by stripping the account prefix, and
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
    batch_fetch=None,
):
    """Install the standard monkeypatch set for tree traversal tests.

    ``entries_or_fn`` may be a list of entries (returned by a generated fake
    ``tree_directory``) or a custom async ``tree_directory`` callable when a
    test needs to capture the arguments passed to Rust.
    """
    if callable(entries_or_fn):
        fake_tree = entries_or_fn
    else:

        async def fake_tree(_path, **_kwargs):
            return entries_or_fn

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree)
    monkeypatch.setattr(fs, "_uri_to_path", lambda _uri, **_kwargs: uri_to_path)
    monkeypatch.setattr(fs, "_ctx_or_default", lambda _ctx=None: _default_ctx())
    patch_visibility(monkeypatch, fs, is_accessible=is_accessible)
    if batch_fetch is not None:
        monkeypatch.setattr(fs, "_batch_fetch_abstracts", batch_fetch)


async def default_batch_fetch(entries, abs_limit, **_kwargs):
    """Shared abstract enrichment fake: dirs get a mock abstract (truncated to
    abs_limit), files get an empty abstract."""
    for entry in entries:
        abstract = "mock abstract" if entry.get("isDir") else ""
        if len(abstract) > abs_limit:
            abstract = abstract[: abs_limit - 3] + "..."
        entry["abstract"] = abstract


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
def test_is_name_visible_at_account_root(fs, name, parent_path, expected):
    """PY-FLT-001, PY-FLT-002: Account root LISTABLE_SCOPES whitelist."""
    assert fs._is_name_visible_at_path(name, parent_path) == expected


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
def test_is_name_visible_at_non_root(fs, name, parent_path, expected):
    """PY-FLT-004: Non-root _INTERNAL_NAMES blacklist."""
    assert fs._is_name_visible_at_path(name, parent_path) == expected


@pytest.mark.parametrize(
    "entry_path,base_path,expected",
    [
        ("/local/test_account/resources/a", "/local/test_account", False),
        ("/local/test_account/tasks/foo", "/local/test_account", True),
        ("/local/test_account/tasks/foo/bar.txt", "/local/test_account", True),
        ("/local/test_account/resources/_system/secret.txt", "/local/test_account", True),
        ("/local/test_account/resources/normal/file.txt", "/local/test_account", False),
        ("/local/test_account/resources/a/b/c", "/local/test_account", False),
    ],
)
def test_ancestor_is_filtered(fs, entry_path, base_path, expected):
    """PY-FLT-003, PY-FLT-006, PY-FLT-007: Ancestor chain filtering."""
    assert fs._ancestor_is_filtered(entry_path, base_path) == expected


# ── _is_tree_entry_visible tests ──


def test_is_tree_entry_visible_visible(monkeypatch, fs):
    """PY-FLT-005, PY-FLT-009: Normal visible entry."""
    patch_visibility(monkeypatch, fs, is_accessible=True)
    entry = make_entry("/local/test_account/resources/a", "a")
    assert fs._is_tree_entry_visible(entry, "/local/test_account", _default_ctx()) is True


def test_is_tree_entry_visible_acl_filtered(monkeypatch, fs):
    """PY-FLT-008: ACL filtering."""
    patch_visibility(monkeypatch, fs, is_accessible=False)
    entry = make_entry("/local/test_account/resources/secret", "secret")
    assert fs._is_tree_entry_visible(entry, "/local/test_account", _default_ctx()) is False


def test_is_tree_entry_visible_hidden_scope_filtered(monkeypatch, fs):
    """PY-FLT-007: tasks scope filtered at account root."""
    patch_visibility(monkeypatch, fs, is_accessible=True)
    entry = make_entry("/local/test_account/tasks/foo", "foo")
    assert fs._is_tree_entry_visible(entry, "/local/test_account", _default_ctx()) is False


def test_is_tree_entry_visible_path_ovlock_filtered(monkeypatch, fs):
    """PY-FLT-011: .path.ovlock is filtered."""
    patch_visibility(monkeypatch, fs, is_accessible=True)
    entry = make_entry("/local/test_account/resources/.path.ovlock", ".path.ovlock", is_dir=False)
    assert fs._is_tree_entry_visible(entry, "/local/test_account", _default_ctx()) is False


def test_is_tree_entry_visible_default_ctx(monkeypatch, fs):
    """PY-FLT-010: ctx=None uses default context."""
    patch_visibility(monkeypatch, fs, is_accessible=True)
    entry = make_entry("/local/test_account/resources/a", "a")
    assert fs._is_tree_entry_visible(entry, "/local/test_account", _default_ctx()) is True


# ── _iter_visible_tree_entries tests ──


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_node_limit_amplified_to_rust(monkeypatch, fs):
    """PY-ITER-001: node_limit is amplified (× overfetch factor) when pushed to
    Rust, keeping memory bounded while preserving the ACL-after-filter semantic."""
    captured = {}

    async def fake_tree_directory(_path, **kwargs):
        captured["node_limit"] = kwargs.get("node_limit")
        return []

    patch_tree_env(monkeypatch, fs, fake_tree_directory)

    async for _ in fs._iter_visible_tree_entries(
        "viking://resources", node_limit=10, ctx=_default_ctx()
    ):
        pass

    assert captured["node_limit"] == 10 * fs._TREE_OVERFETCH_FACTOR


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_node_limit_none_not_pushed(monkeypatch, fs):
    """PY-ITER-001b: node_limit=None (full-tree callers) pushes no raw limit."""
    captured = {"set": False, "value": "unset"}

    async def fake_tree_directory(_path, **kwargs):
        captured["set"] = True
        captured["value"] = kwargs.get("node_limit")
        return []

    patch_tree_env(monkeypatch, fs, fake_tree_directory)

    async for _ in fs._iter_visible_tree_entries(
        "viking://resources", node_limit=None, ctx=_default_ctx()
    ):
        pass

    assert captured["set"] is True
    assert captured["value"] is None


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_level_limit_passed_to_rust(monkeypatch, fs):
    """PY-ITER-003: level_limit IS passed to Rust layer."""
    captured = {}

    async def fake_tree_directory(_path, **kwargs):
        captured["level_limit"] = kwargs.get("level_limit")
        return []

    patch_tree_env(monkeypatch, fs, fake_tree_directory)

    async for _ in fs._iter_visible_tree_entries(
        "viking://resources", level_limit=3, ctx=_default_ctx()
    ):
        pass

    assert captured["level_limit"] == 3


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_node_limit_after_acl(monkeypatch, fs):
    """PY-ITER-002: node_limit applied AFTER ACL filtering."""
    visible_count = 0

    entries = [
        make_entry(f"/local/test_account/resources/{name}", name, is_dir=False)
        for name in ["a", "b", "c", "d", "e"]
    ]

    def fake_is_accessible(_uri, _ctx):
        nonlocal visible_count
        visible_count += 1
        return True

    patch_tree_env(monkeypatch, fs, entries, is_accessible=fake_is_accessible)

    results = []
    async for entry, _entry_uri in fs._iter_visible_tree_entries(
        "viking://resources", node_limit=3, ctx=_default_ctx()
    ):
        results.append(entry)

    assert len(results) == 3
    assert visible_count == 3, "only 3 entries should have ACL checked before limit hit"


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_refetch_when_under_limit(monkeypatch, fs):
    """PY-ITER-004: when ACL filtering leaves too few visible entries and Rust
    returned a full page, the raw limit is doubled and re-fetched (zero-regression
    bounded over-fetch)."""
    factor = fs._TREE_OVERFETCH_FACTOR
    node_limit = 2
    # First page (size == raw_limit) is entirely invisible; second page after
    # doubling contains the visible entries.
    invisible = [
        make_entry(f"/local/test_account/resources/h{i}/x", "x", is_dir=False)
        for i in range(node_limit * factor)
    ]
    visible = [
        make_entry(f"/local/test_account/resources/v{i}", f"v{i}", is_dir=False)
        for i in range(node_limit)
    ]
    captured_limits = []

    async def fake_tree_directory(_path, **kwargs):
        raw_limit = kwargs.get("node_limit")
        captured_limits.append(raw_limit)
        # First call returns a full page of invisible entries; subsequent call
        # (doubled limit) returns invisible + visible.
        if len(captured_limits) == 1:
            return invisible[:raw_limit]
        return invisible + visible

    def fake_is_accessible(uri, _ctx):
        # Entries under an "hN" directory are invisible (ACL denied).
        return "/h" not in uri

    patch_tree_env(monkeypatch, fs, fake_tree_directory, is_accessible=fake_is_accessible)

    results = []
    async for entry, _uri in fs._iter_visible_tree_entries(
        "viking://resources", node_limit=node_limit, ctx=_default_ctx()
    ):
        results.append(entry)

    assert len(results) == node_limit
    # Re-fetch happened: first raw_limit = node_limit*factor, then doubled.
    assert captured_limits[0] == node_limit * factor
    assert captured_limits[1] == node_limit * factor * 2


@pytest.mark.asyncio
async def test_iter_visible_tree_entries_show_hidden_passthrough(monkeypatch, fs):
    """PY-ITER-005: show_all_hidden passthrough."""
    captured = {}

    async def fake_tree_directory(_path, **kwargs):
        captured["show_hidden"] = kwargs.get("show_hidden")
        return []

    patch_tree_env(monkeypatch, fs, fake_tree_directory)

    async for _ in fs._iter_visible_tree_entries(
        "viking://resources", show_all_hidden=True, ctx=_default_ctx()
    ):
        pass

    assert captured["show_hidden"] is True


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
            extra={"meta": {"Name": "s3fs"}},
        )
    ]
    patch_tree_env(monkeypatch, fs, entries)

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
async def test_tree_original_extra_fields_preserved(monkeypatch, fs):
    """PY-ORIG-002: extra fields preserved."""
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

    result = await fs._tree_original("viking://resources", ctx=_default_ctx())
    assert result[0]["meta"] == {"Name": "s3fs", "Type": "s3"}


@pytest.mark.asyncio
async def test_tree_original_dfs_order(monkeypatch, fs):
    """PY-ORIG-006: DFS order preserved — directories before their children."""
    entries = [
        make_entry("/local/test_account/resources/sub", "sub", is_dir=True),
        make_entry(
            "/local/test_account/resources/sub/file.txt",
            "file.txt",
            size=100,
            mode=0o644,
            is_dir=False,
        ),
    ]
    patch_tree_env(monkeypatch, fs, entries)

    result = await fs._tree_original("viking://resources", ctx=_default_ctx())
    assert result[0]["name"] == "sub"
    assert result[0]["isDir"] is True
    assert result[1]["name"] == "file.txt"


# ── _tree_agent tests ──


@pytest.mark.asyncio
async def test_tree_agent_structure(monkeypatch, fs):
    """PY-AGENT-001: Agent output structure."""
    entries = [
        make_entry("/local/test_account/resources/a", "a", size=100, mode=0o644, is_dir=False),
        make_entry("/local/test_account/resources/sub", "sub", is_dir=True),
    ]
    patch_tree_env(monkeypatch, fs, entries, batch_fetch=default_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())

    assert len(result) == 2
    assert set(result[0].keys()) == {"uri", "size", "isDir", "modTime", "rel_path", "abstract"}
    assert "name" not in result[0]


@pytest.mark.asyncio
async def test_tree_agent_dir_size_zero(monkeypatch, fs):
    """PY-AGENT-002: Directory size is always 0."""
    entries = [make_entry("/local/test_account/resources/sub", "sub", size=999, is_dir=True)]
    patch_tree_env(monkeypatch, fs, entries, batch_fetch=default_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert result[0]["size"] == 0


@pytest.mark.asyncio
async def test_tree_agent_non_dir_abstract_empty(monkeypatch, fs):
    """PY-AGENT-004: Non-directory entries have empty abstract."""
    entries = [
        make_entry("/local/test_account/resources/a", "a", size=100, mode=0o644, is_dir=False)
    ]
    patch_tree_env(monkeypatch, fs, entries, batch_fetch=default_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert result[0]["abstract"] == ""


@pytest.mark.asyncio
async def test_tree_agent_modtime_formatted(monkeypatch, fs):
    """PY-AGENT-003: modTime is formatted via format_simplified."""
    entries = [
        make_entry("/local/test_account/resources/a", "a", size=100, mode=0o644, is_dir=False)
    ]
    patch_tree_env(monkeypatch, fs, entries, batch_fetch=default_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert result[0]["modTime"] == "2026-01-01"


@pytest.mark.asyncio
async def test_tree_agent_abs_limit_truncation(monkeypatch, fs):
    """PY-AGENT-005: abstract is truncated when exceeding abs_limit."""
    entries = [make_entry("/local/test_account/resources/sub", "sub", is_dir=True)]

    async def fake_batch_fetch(entries_arg, abs_limit, **_kwargs):
        for entry in entries_arg:
            abstract = "x" * (abs_limit + 10) if entry.get("isDir") else ""
            if len(abstract) > abs_limit:
                abstract = abstract[: abs_limit - 3] + "..."
            entry["abstract"] = abstract

    patch_tree_env(monkeypatch, fs, entries, batch_fetch=fake_batch_fetch)

    result = await fs._tree_agent("viking://resources", abs_limit=10, ctx=_default_ctx())
    assert len(result[0]["abstract"]) <= 10
    assert result[0]["abstract"].endswith("...")


@pytest.mark.asyncio
async def test_tree_agent_batch_fetch_input_order(monkeypatch, fs):
    """PY-AGENT-006: _batch_fetch_abstracts receives entries in correct order."""
    captured_entries = None

    entries = [
        make_entry("/local/test_account/resources/a", "a", size=100, mode=0o644, is_dir=False),
        make_entry(
            "/local/test_account/resources/b",
            "b",
            size=200,
            mode=0o644,
            mod_time="2026-01-02T00:00:00Z",
            is_dir=False,
        ),
    ]

    async def fake_batch_fetch(entries_arg, _abs_limit, **_kwargs):
        nonlocal captured_entries
        captured_entries = list(entries_arg)
        for entry in entries_arg:
            entry["abstract"] = ""

    patch_tree_env(monkeypatch, fs, entries, batch_fetch=fake_batch_fetch)

    await fs._tree_agent("viking://resources", abs_limit=256, ctx=_default_ctx())
    assert len(captured_entries) == 2
    assert captured_entries[0]["uri"] == "viking://resources/a"
    assert captured_entries[1]["uri"] == "viking://resources/b"


@pytest.mark.asyncio
async def test_tree_agent_node_limit_before_enrichment(monkeypatch, fs):
    """PY-AGENT-007: node_limit applied before abstract enrichment."""
    enriched_count = 0

    entries = [
        make_entry(f"/local/test_account/resources/{name}", name, is_dir=False)
        for name in ["a", "b", "c", "d", "e"]
    ]

    async def fake_batch_fetch(entries_arg, _abs_limit, **_kwargs):
        nonlocal enriched_count
        enriched_count = len(entries_arg)
        for entry in entries_arg:
            entry["abstract"] = ""

    patch_tree_env(monkeypatch, fs, entries, batch_fetch=fake_batch_fetch)

    result = await fs._tree_agent(
        "viking://resources", node_limit=2, abs_limit=256, ctx=_default_ctx()
    )
    assert len(result) == 2
    assert enriched_count == 2
