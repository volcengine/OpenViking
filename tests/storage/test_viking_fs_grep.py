# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import time

import pytest

import openviking.storage.viking_fs as viking_fs_module
from openviking.storage.expr import And, PathScope, RawDSL
from openviking.storage.viking_fs import _DEFAULT_GREP_FILE_CONCURRENCY, VikingFS
from openviking_cli.utils.config.grep_config import GrepConfig


class _DummyAgfs:
    pass


class _DummyVectorStore:
    def __init__(self, results=None):
        self.calls = []
        self.results = results or []

    async def search_by_keywords(self, **kwargs):
        self.calls.append(kwargs)
        return self.results


class _FailingVectorStore:
    async def search_by_keywords(self, **kwargs):
        raise RuntimeError("remote keyword search failed")


@pytest.fixture
def fs(monkeypatch):
    viking_fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(viking_fs, "stat", _fake_stat)
    monkeypatch.setattr(
        viking_fs,
        "_uri_to_path",
        lambda uri, ctx=None: uri.replace("viking://", "/"),
    )
    monkeypatch.setattr(
        viking_fs,
        "_path_to_uri",
        lambda path, ctx=None: path.replace("/", "viking://", 1),
    )
    return viking_fs


async def _fake_stat(uri, ctx=None, skip_count=False):
    return {"name": uri.rsplit("/", 1)[-1], "isDir": True}


def test_grep_config_default_switch_to_remote_threshold_is_10000():
    assert GrepConfig().switch_to_remote_threshold == 10000


@pytest.mark.asyncio
async def test_grep_without_config_uses_documented_remote_threshold(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _DummyVectorStore()
    vector_store._backend_type = "vikingdb"
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)

    async def fake_collection_has_fulltext(vector_store, ctx):
        return True

    monkeypatch.setattr(fs, "_collection_has_fulltext", fake_collection_has_fulltext)

    async def fake_count(uri, ctx):
        return 5000

    monkeypatch.setattr(fs, "_get_cached_count", fake_count)

    assert await fs._resolve_grep_engine("auto", "viking://resources", None) == "fs"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("node_limit", "expected_remote_limit"),
    [
        (7, 35),
        (None, 100000),
        (50000, 100000),
    ],
)
async def test_grep_vikingdb_auto_remote_limit_uses_five_times_node_limit(
    monkeypatch, node_limit, expected_remote_limit
):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _DummyVectorStore()
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)

    result = await fs._grep_vikingdb_then_fs(
        uri="viking://resources",
        pattern="needle",
        exclude_uri=None,
        case_insensitive=False,
        node_limit=node_limit,
        level_limit=10,
        ctx=None,
    )

    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    assert vector_store.calls[0]["limit"] == expected_remote_limit


@pytest.mark.asyncio
async def test_grep_vikingdb_remote_error_falls_back_to_fs(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(fs, "_get_vector_store", lambda: _FailingVectorStore())

    calls = []

    async def fake_grep_fs(**kwargs):
        calls.append(kwargs)
        return {
            "matches": [{"uri": "viking://resources/a.md"}],
            "count": 1,
            "match_count": 1,
            "files_scanned": 1,
        }

    monkeypatch.setattr(fs, "_grep_fs", fake_grep_fs)

    result = await fs._grep_vikingdb_then_fs(
        uri="viking://resources",
        pattern="needle",
        exclude_uri="viking://resources/archive",
        case_insensitive=True,
        node_limit=10,
        level_limit=3,
        ctx=None,
    )

    assert result["count"] == 1
    assert calls == [
        {
            "uri": "viking://resources",
            "pattern": "needle",
            "exclude_uri": "viking://resources/archive",
            "case_insensitive": True,
            "node_limit": 10,
            "level_limit": 3,
            "ctx": None,
        }
    ]


@pytest.mark.asyncio
async def test_grep_vikingdb_pushes_exclude_uri_to_filter(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _DummyVectorStore()
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)
    monkeypatch.setattr(fs, "_ensure_access", lambda uri, ctx=None: None)

    result = await fs._grep_vikingdb_then_fs(
        uri="viking://resources",
        pattern="needle",
        exclude_uri="viking://resources/archive",
        case_insensitive=False,
        node_limit=10,
        level_limit=3,
        ctx=None,
    )

    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    filter_expr = vector_store.calls[0]["filter"]
    assert isinstance(filter_expr, And)
    assert filter_expr.conds[0] == PathScope("uri", "viking://resources", depth=3)
    assert isinstance(filter_expr.conds[1], RawDSL)
    assert filter_expr.conds[1].payload == {
        "op": "must_not",
        "field": "uri",
        "conds": ["viking://resources/archive"],
        "para": "-d=-1",
    }


@pytest.mark.asyncio
async def test_grep_vikingdb_keeps_local_exclude_uri_guard(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())
    vector_store = _DummyVectorStore(
        results=[
            {"uri": "viking://resources/archive/a.md"},
            {"uri": "viking://resources/keep.md"},
        ]
    )
    monkeypatch.setattr(fs, "_get_vector_store", lambda: vector_store)
    monkeypatch.setattr(fs, "_ensure_access", lambda uri, ctx=None: None)

    grep_in_files_calls = []

    async def fake_grep_in_files(file_uris, pattern, case_insensitive, node_limit, ctx):
        grep_in_files_calls.append(file_uris)
        return {"matches": [], "count": 0, "match_count": 0, "files_scanned": len(file_uris)}

    monkeypatch.setattr(fs, "_grep_in_files", fake_grep_in_files)

    await fs._grep_vikingdb_then_fs(
        uri="viking://resources",
        pattern="needle",
        exclude_uri="viking://resources/archive",
        case_insensitive=False,
        node_limit=10,
        level_limit=3,
        ctx=None,
    )

    assert grep_in_files_calls == [["viking://resources/keep.md"]]


@pytest.mark.asyncio
async def test_grep_preserves_dfs_order_and_node_limit(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())

    async def fake_stat(uri, ctx=None, skip_count=False):
        return {"isDir": True}

    async def fake_ls(uri, ctx=None, **kwargs):
        entries = {
            "viking://resources": [
                {"name": "dir_a", "isDir": True},
                {"name": "dir_b", "isDir": True},
            ],
            "viking://resources/dir_a": [
                {"name": "a1.md", "isDir": False},
                {"name": "a2.md", "isDir": False},
            ],
            "viking://resources/dir_b": [
                {"name": "b1.md", "isDir": False},
            ],
        }
        return entries.get(uri, [])

    def fake_agfs_read(path, offset=0, size=-1):
        contents = {
            "/resources/dir_a/a1.md": "match a1 line1\nskip\nmatch a1 line3",
            "/resources/dir_a/a2.md": "match a2 line1",
            "/resources/dir_b/b1.md": "match b1 line1",
        }
        return contents[path].encode()

    monkeypatch.setattr(fs, "stat", fake_stat)
    monkeypatch.setattr(fs, "ls", fake_ls)
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, ctx=None: uri.replace("viking://", "/"),
    )
    monkeypatch.setattr(fs.agfs, "read", fake_agfs_read, raising=False)

    result = await fs.grep("viking://resources", pattern="match", node_limit=3)

    assert result["count"] == 3
    assert result["files_scanned"] == 2
    assert result["matches"] == [
        {
            "line": 1,
            "uri": "viking://resources/dir_a/a1.md",
            "content": "match a1 line1",
        },
        {
            "line": 3,
            "uri": "viking://resources/dir_a/a1.md",
            "content": "match a1 line3",
        },
        {
            "line": 1,
            "uri": "viking://resources/dir_a/a2.md",
            "content": "match a2 line1",
        },
    ]


@pytest.mark.asyncio
async def test_grep_parallel_reads_respect_concurrency_limit(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())

    async def fake_stat(uri, ctx=None, skip_count=False):
        return {"isDir": True}

    async def fake_ls(uri, ctx=None, **kwargs):
        entries = {
            "viking://resources": [{"name": f"file{i}.md", "isDir": False} for i in range(12)]
        }
        return entries.get(uri, [])

    active_reads = 0
    max_active_reads = 0

    def fake_agfs_read(path, offset=0, size=-1):
        nonlocal active_reads, max_active_reads
        active_reads += 1
        max_active_reads = max(max_active_reads, active_reads)
        time.sleep(0.01)
        active_reads -= 1
        return f"match from {path}".encode()

    monkeypatch.setattr(fs, "stat", fake_stat)
    monkeypatch.setattr(fs, "ls", fake_ls)
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, ctx=None: uri.replace("viking://", "/"),
    )
    monkeypatch.setattr(fs.agfs, "read", fake_agfs_read, raising=False)

    result = await fs.grep("viking://resources", pattern="match")

    assert result["count"] == 12
    assert result["files_scanned"] == 12
    assert max_active_reads > 1
    assert max_active_reads <= min(12, _DEFAULT_GREP_FILE_CONCURRENCY)


@pytest.mark.asyncio
async def test_grep_parallel_reads_work_with_blocking_agfs_read(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())

    async def fake_stat(uri, ctx=None, skip_count=False):
        return {"isDir": True}

    async def fake_ls(uri, ctx=None, **kwargs):
        if uri == "viking://resources":
            return [{"name": f"file{i}.md", "isDir": False} for i in range(8)]
        return []

    def fake_agfs_read(path, offset=0, size=-1):
        time.sleep(0.05)
        return f"match from {path}".encode()

    monkeypatch.setattr(fs, "stat", fake_stat)
    monkeypatch.setattr(fs, "ls", fake_ls)
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, ctx=None: uri.replace("viking://", "/"),
    )
    monkeypatch.setattr(fs.agfs, "read", fake_agfs_read, raising=False)

    started = time.perf_counter()
    result = await fs.grep("viking://resources", pattern="match")
    elapsed = time.perf_counter() - started

    assert result["count"] == 8
    assert result["files_scanned"] == 8
    assert elapsed < 0.30


@pytest.mark.asyncio
async def test_grep_stops_scheduling_later_batches_after_node_limit(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())

    async def fake_stat(uri, ctx=None, skip_count=False):
        return {"isDir": True}

    async def fake_ls(uri, ctx=None, **kwargs):
        if uri == "viking://resources":
            return [{"name": f"file{i}.md", "isDir": False} for i in range(6)]
        return []

    read_paths = []

    def fake_agfs_read(path, offset=0, size=-1):
        read_paths.append(path)
        contents = {
            "/resources/file0.md": "match file0 line1\nmatch file0 line2",
            "/resources/file1.md": "match file1 line1",
            "/resources/file2.md": "match file2 line1",
            "/resources/file3.md": "match file3 line1",
            "/resources/file4.md": "match file4 line1",
            "/resources/file5.md": "match file5 line1",
        }
        return contents[path].encode()

    monkeypatch.setattr(fs, "stat", fake_stat)
    monkeypatch.setattr(fs, "ls", fake_ls)
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda uri, ctx=None: uri.replace("viking://", "/"),
    )
    monkeypatch.setattr(fs.agfs, "read", fake_agfs_read, raising=False)
    monkeypatch.setattr(viking_fs_module, "_DEFAULT_GREP_FILE_CONCURRENCY", 2)

    result = await fs.grep("viking://resources", pattern="match", node_limit=2)

    assert result["count"] == 2
    assert result["files_scanned"] == 1
    assert read_paths == ["/resources/file0.md", "/resources/file1.md"]


@pytest.mark.asyncio
async def test_grep_delegates_to_agfs_with_expected_filters(monkeypatch, fs):
    calls = []

    async def fake_grep(**kwargs):
        calls.append(kwargs)
        return {"matches": [], "files_scanned": 0}

    monkeypatch.setattr(fs._async_agfs, "grep", fake_grep)

    result = await fs.grep(
        "viking://resources",
        pattern="needle",
        exclude_uri="viking://resources/archive",
        case_insensitive=True,
        node_limit=10,
        level_limit=3,
    )

    assert result == {"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
    assert calls == [
        {
            "path": "/resources",
            "pattern": "needle",
            "recursive": True,
            "case_insensitive": True,
            "stream": False,
            "node_limit": 10,
            "exclude_path": "/resources/archive",
            "level_limit": 3,
        }
    ]


@pytest.mark.asyncio
async def test_grep_maps_agfs_matches_to_viking_uris(monkeypatch, fs):
    async def fake_grep(**kwargs):
        return {
            "matches": [
                {"file": "dir/a.md", "line": 2, "content": "first match"},
                {"file": "/dir/b.md", "line_number": 5, "content": "second match"},
            ],
            "files_scanned": 7,
        }

    monkeypatch.setattr(fs._async_agfs, "grep", fake_grep)

    result = await fs.grep("viking://resources", pattern="match")

    assert result == {
        "matches": [
            {
                "line": 2,
                "uri": "viking://resources/dir/a.md",
                "content": "first match",
            },
            {
                "line": 5,
                "uri": "viking://resources/dir/b.md",
                "content": "second match",
            },
        ],
        "count": 2,
        "match_count": 2,
        "files_scanned": 7,
    }


@pytest.mark.asyncio
async def test_grep_applies_node_limit_to_backend_results(monkeypatch, fs):
    async def fake_grep(**kwargs):
        return {
            "matches": [
                {"file": "a.md", "line": 1, "content": "a"},
                {"file": "b.md", "line": 1, "content": "b"},
                {"file": "c.md", "line": 1, "content": "c"},
            ]
        }

    monkeypatch.setattr(fs._async_agfs, "grep", fake_grep)

    result = await fs.grep("viking://resources", pattern="match", node_limit=2)

    assert result["count"] == 2
    assert result["match_count"] == 2
    assert result["files_scanned"] == 2
    assert [match["uri"] for match in result["matches"]] == [
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]
