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


class _LegacyTreeAGFS:
    def __init__(self):
        self.calls = []
        self.entries = {
            "/root": [
                {"name": ".hidden", "isDir": False, "size": 1, "modTime": "hidden-time"},
                {"name": "a.txt", "isDir": False, "size": 2, "modTime": "a-time"},
                {"name": "sub", "isDir": True, "size": 0, "modTime": "sub-time"},
            ],
            "/root/sub": [
                {"name": "b.txt", "isDir": False, "size": 3, "modTime": "b-time"},
            ],
        }

    def ls(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.entries.get(path, [])


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
async def test_tree_directory_falls_back_to_recursive_ls_for_legacy_bindings():
    sync_agfs = _LegacyTreeAGFS()
    agfs = AsyncAGFSClient(sync_agfs)

    result = await agfs.tree_directory(
        "/root",
        node_limit=3,
        level_limit=2,
        fs_ctx={"account_id": "acct"},
    )

    assert [entry["rel_path"] for entry in result] == ["a.txt", "sub", "sub/b.txt"]
    assert result[0]["path"] == "/root/a.txt"
    assert result[0]["info"] == {
        "name": "a.txt",
        "size": 2,
        "mode": 0o644,
        "modTime": "a-time",
        "isDir": False,
    }
    assert result[1]["info"]["isDir"] is True
    assert sync_agfs.calls == [
        ("/root", {"ctx": {"account_id": "acct"}}),
        ("/root/sub", {"ctx": {"account_id": "acct"}}),
    ]


@pytest.mark.asyncio
async def test_tree_directory_fallback_honors_level_limit_zero():
    agfs = AsyncAGFSClient(_LegacyTreeAGFS())

    result = await agfs.tree_directory("/root", level_limit=0)

    assert result == []
