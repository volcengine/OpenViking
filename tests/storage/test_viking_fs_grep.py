# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import time

import pytest

import openviking.storage.viking_fs as viking_fs_module
from openviking.storage.viking_fs import _DEFAULT_GREP_FILE_CONCURRENCY, VikingFS


class _DummyAgfs:
    pass


@pytest.mark.asyncio
async def test_grep_preserves_dfs_order_and_node_limit(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())

    async def fake_stat(uri, ctx=None):
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

    def fake_agfs_read(path, offset, size):
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

    async def fake_stat(uri, ctx=None):
        return {"isDir": True}

    async def fake_ls(uri, ctx=None, **kwargs):
        entries = {
            "viking://resources": [{"name": f"file{i}.md", "isDir": False} for i in range(12)]
        }
        return entries.get(uri, [])

    active_reads = 0
    max_active_reads = 0

    def fake_agfs_read(path, offset, size):
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

    async def fake_stat(uri, ctx=None):
        return {"isDir": True}

    async def fake_ls(uri, ctx=None, **kwargs):
        if uri == "viking://resources":
            return [{"name": f"file{i}.md", "isDir": False} for i in range(8)]
        return []

    def fake_agfs_read(path, offset, size):
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

    async def fake_stat(uri, ctx=None):
        return {"isDir": True}

    async def fake_ls(uri, ctx=None, **kwargs):
        if uri == "viking://resources":
            return [{"name": f"file{i}.md", "isDir": False} for i in range(6)]
        return []

    read_paths = []

    def fake_agfs_read(path, offset, size):
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
