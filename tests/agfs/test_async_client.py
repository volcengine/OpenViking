# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

import openviking.pyagfs.async_client as async_client
from openviking.pyagfs import AsyncAGFSClient


class _SyncAGFS:
    """Minimal synchronous binding stub used by the async adapter tests."""

    def __init__(self):
        self.pathlock_acquire_calls = []

    def read(self, path, **kwargs):
        """Return read call arguments."""
        return ("read", path, kwargs)

    def write(self, path, data, **kwargs):
        """Return write call arguments."""
        return ("write", path, data, kwargs)

    def rm(self, path, **kwargs):
        """Return remove call arguments."""
        return ("rm", path, kwargs)

    def pathlock_is_locked(self, ctx, path, ignore_stale):
        """Return pathlock query arguments."""
        return ("pathlock_is_locked", ctx, path, ignore_stale)

    async def pathlock_acquire_batch_async(
        self, ctx, requests, timeout_secs, owner_lease_ref
    ):
        """Record a native async pathlock acquisition."""
        self.pathlock_acquire_calls.append((ctx, requests, timeout_secs, owner_lease_ref))
        return {"lease_ref": "lease-1", "owned": True}


@pytest.mark.asyncio
async def test_async_agfs_client_hides_threadpool(monkeypatch):
    to_thread_calls = []

    async def fake_to_thread(func, *args, **kwargs):
        to_thread_calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(async_client.asyncio, "to_thread", fake_to_thread)

    sync_agfs = _SyncAGFS()
    agfs = AsyncAGFSClient(sync_agfs)

    assert agfs._client is sync_agfs
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
    assert await agfs.pathlock_acquire_exact("/sessions/1", timeout_secs=30.0) == {
        "lease_ref": "lease-1",
        "owned": True,
    }
    assert sync_agfs.pathlock_acquire_calls == [
        (
            {"account_id": "_system"},
            [{"path": "/sessions/1", "kind": "exact"}],
            30.0,
            None,
        )
    ]

    assert to_thread_calls == [
        ("write", ("/tasks/1", b"data"), {"ctx": {"account_id": "_system"}}),
        ("read", ("/queue/dequeue",), {"ctx": {"account_id": "_system"}}),
        (
            "rm",
            ("/redo/id",),
            {"recursive": True, "ctx": {"account_id": "_system"}},
        ),
    ]
