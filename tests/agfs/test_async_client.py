# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.storage.viking_vector_index_backend import _AsyncVectorAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["agfs", "agfs_cp", "vector"])
async def test_async_agfs_client_hides_threadpool(backend, monkeypatch):
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
    monkeypatch.setattr(
        "openviking.pyagfs.helpers.cp", lambda client, src, dst, **_: client.write(dst, b"data")
    )
    operation = asyncio.create_task(
        AsyncAGFSClient(writer).cp("/local/account/source", "/local/account/file")
        if backend == "agfs_cp"
        else AsyncAGFSClient(writer).write("/local/account/file", b"data")
        if backend == "agfs"
        else _AsyncVectorAdapter(writer).call("write", "record", b"data")
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        for _ in range(2):
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_replace_cancellation_settles_io_before_releasing_control():
    started, release = threading.Event(), threading.Event()
    replacements = []

    class Replacer:
        def replace(self, src, dst, *, ctx):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test did not release replacement")
            replacements.append((src, dst, ctx))

    task = asyncio.create_task(
        AsyncAGFSClient(Replacer()).replace("/local/acme/temp", "/local/acme/graph")
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(replacements) == 1


@pytest.mark.asyncio
async def test_replace_rejects_cross_account_before_io():
    class Replacer:
        def replace(self, *args, **kwargs):
            pytest.fail("cross-account replacement reached the backend")

    with pytest.raises(ValueError, match="cross-account"):
        await AsyncAGFSClient(Replacer()).replace("/local/red/temp", "/local/blue/graph")


@pytest.mark.asyncio
async def test_inflight_replace_requires_backend_fencing_after_lease_loss():
    """Diagnose the limit of a pre-I/O lease check, not claim remote fencing."""
    started, release = threading.Event(), threading.Event()

    class Replacer:
        current_lease = "old"
        graph = b"baseline"

        def replace(self, src, dst, *, ctx):
            if ctx["lease_ref"] != self.current_lease:
                raise RuntimeError("lease invalid")
            if ctx["lease_ref"] == "old":
                started.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("Test did not release old replacement")
            # An unconditional backend replacement cannot check a different
            # object's lock token atomically at its publication point.
            self.graph = src

    backend = Replacer()
    client = AsyncAGFSClient(backend)
    old = asyncio.create_task(
        client.replace(
            "/local/acme/old",
            "/local/acme/graph",
            fs_ctx={"account_id": "acme", "lease_ref": "old"},
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        backend.current_lease = "new"
        await client.replace(
            "/local/acme/new",
            "/local/acme/graph",
            fs_ctx={"account_id": "acme", "lease_ref": "new"},
        )
        assert backend.graph == "/local/acme/new"
    finally:
        release.set()
        await old
    assert backend.graph == "/local/acme/old"
