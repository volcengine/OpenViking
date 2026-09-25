# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs import async_client
from openviking.storage.viking_vector_index_backend import _AsyncVectorAdapter


class _LegacyRmAGFS:
    """Synchronous binding stub with the older rm signature."""

    def rm(self, path, recursive=False):
        """Return remove call arguments without accepting force."""
        return ("rm", path, recursive)


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
async def test_async_agfs_client_rm_tolerates_legacy_binding_without_force(monkeypatch):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(async_client.asyncio, "to_thread", fake_to_thread)

    agfs = AsyncAGFSClient(_LegacyRmAGFS())

    assert await agfs.rm("/redo/id", recursive=True, force=False) == (
        "rm",
        "/redo/id",
        True,
    )
