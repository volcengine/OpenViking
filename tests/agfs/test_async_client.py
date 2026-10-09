# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading
from typing import Any

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.storage.viking_vector_index_backend import _AsyncVectorAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "vector"])
async def test_async_agfs_client_hides_threadpool(backend):
    """Cancellation settles physical writes before cleanup may remove their data."""
    started, release = threading.Event(), threading.Event()
    writes = []

    class Writer:
        def write(self, path, data, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test did not release the write")
            writes.append((path, data))

    writer = Writer()
    operation = asyncio.create_task(
        AsyncAGFSClient(writer).write("/local/account/file", b"data")
        if backend == "agfs"
        else _AsyncVectorAdapter(writer).call("write", "record", b"data")
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not operation.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_async_agfs_client_releases_pathlock_on_cancellation():
    """Cancellation during lock acquisition releases the newly acquired lease."""
    acquired, release_block = threading.Event(), threading.Event()
    released_leases = []

    class LockClient:
        def pathlock_acquire_exact(self, ctx, path, timeout_secs=0.0, owner_lease_ref=None):
            acquired.set()
            if not release_block.wait(timeout=5):
                raise TimeoutError("Test did not unblock acquire")
            return {"lease_ref": "lease-123", "path": path}

        def pathlock_release(self, ctx, lease):
            released_leases.append((ctx, lease))
            return "released"

    client: Any = LockClient()
    agfs = AsyncAGFSClient(client)

    task = asyncio.create_task(
        agfs.pathlock_acquire_exact("/local/account/file", fs_ctx={"account_id": "account"})
    )

    try:
        assert await asyncio.to_thread(acquired.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release_block.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(released_leases) == 1
    ctx, lease = released_leases[0]
    assert lease["lease_ref"] == "lease-123"
    assert ctx["account_id"] == "account"
