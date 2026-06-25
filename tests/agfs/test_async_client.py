# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

import openviking.pyagfs.async_client as async_client
from openviking.pyagfs import AsyncAGFSClient


class _SyncAGFS:
    def read(self, path, **kwargs):
        return ("read", path, kwargs)

    def write(self, path, data, **kwargs):
        return ("write", path, data, kwargs)

    def rm(self, path, **kwargs):
        return ("rm", path, kwargs)


class _SyncAGFSWithoutTree:
    def __init__(self):
        self.entries = {
            "/repo": [
                {"name": "pkg", "isDir": True, "size": 0, "modTime": "2026-01-01T00:00:00Z"},
                {"name": "README.md", "isDir": False, "size": 12, "modTime": "2026-01-01T00:00:01Z"},
                {"name": ".hidden", "isDir": False, "size": 1, "modTime": "2026-01-01T00:00:02Z"},
            ],
            "/repo/pkg": [
                {"name": "mod.py", "isDir": False, "size": 34, "modTime": "2026-01-01T00:00:03Z"},
            ],
        }

    def ls(self, path, **kwargs):
        return list(self.entries.get(path, []))


@pytest.mark.asyncio
async def test_async_agfs_client_hides_threadpool(monkeypatch):
    to_thread_calls = []

    async def fake_to_thread(func, *args, **kwargs):
        to_thread_calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(async_client.asyncio, "to_thread", fake_to_thread)

    sync_agfs = _SyncAGFS()
    agfs = AsyncAGFSClient(sync_agfs)

    assert agfs.sync_client is sync_agfs
    assert await agfs.write("/tasks/1", b"data") == (
        "write",
        "/tasks/1",
        b"data",
        {"ctx": {"account_id": "_system"}},
    )
    assert await agfs.read("/queue/dequeue") == (
        "read",
        "/queue/dequeue",
        {"ctx": {"account_id": "_system"}},
    )
    assert await agfs.rm("/redo/id", recursive=True) == (
        "rm",
        "/redo/id",
        {"recursive": True, "ctx": {"account_id": "_system"}},
    )

    assert to_thread_calls == [
        ("write", ("/tasks/1", b"data"), {"ctx": {"account_id": "_system"}}),
        ("read", ("/queue/dequeue",), {"ctx": {"account_id": "_system"}}),
        (
            "rm",
            ("/redo/id",),
            {"recursive": True, "ctx": {"account_id": "_system"}},
        ),
    ]


@pytest.mark.asyncio
async def test_tree_directory_falls_back_to_recursive_ls_when_binding_lacks_tree(monkeypatch):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(async_client.asyncio, "to_thread", fake_to_thread)

    agfs = AsyncAGFSClient(_SyncAGFSWithoutTree())

    entries = await agfs.tree_directory("/repo", show_hidden=False, level_limit=None)

    assert [entry["path"] for entry in entries] == [
        "/repo/README.md",
        "/repo/pkg",
        "/repo/pkg/mod.py",
    ]
    assert entries[0]["rel_path"] == "README.md"
    assert entries[0]["info"]["name"] == "README.md"
    assert entries[0]["info"]["isDir"] is False
    assert entries[1]["rel_path"] == "pkg"
    assert entries[1]["info"]["isDir"] is True


@pytest.mark.asyncio
async def test_tree_directory_fallback_honors_node_and_level_limits(monkeypatch):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(async_client.asyncio, "to_thread", fake_to_thread)

    agfs = AsyncAGFSClient(_SyncAGFSWithoutTree())

    assert [entry["path"] for entry in await agfs.tree_directory("/repo", node_limit=2)] == [
        "/repo/README.md",
        "/repo/pkg",
    ]
    assert [entry["path"] for entry in await agfs.tree_directory("/repo", level_limit=1)] == [
        "/repo/README.md",
        "/repo/pkg",
    ]
