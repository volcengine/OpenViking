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
    assert await agfs.write("/tasks/1", b"data") == ("write", "/tasks/1", b"data", {})
    assert await agfs.read("/queue/dequeue") == ("read", "/queue/dequeue", {})
    assert await agfs.rm("/redo/id", recursive=True) == (
        "rm",
        "/redo/id",
        {"recursive": True},
    )

    assert to_thread_calls == [
        ("write", ("/tasks/1", b"data"), {}),
        ("read", ("/queue/dequeue",), {}),
        ("rm", ("/redo/id",), {"recursive": True}),
    ]
