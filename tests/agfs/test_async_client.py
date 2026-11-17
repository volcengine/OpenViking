# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading

import pytest

from openviking.pyagfs import AsyncAGFSClient
from openviking.storage.viking_vector_index_backend import _AsyncVectorAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backend", ["agfs", "agfs_cp", "agfs_ls_pages", "agfs_tree_directory_pages", "vector"]
)
async def test_async_agfs_client_hides_threadpool(backend, monkeypatch):
    """Cancellation settles physical I/O before cleanup may release its resources."""
    started, release = threading.Event(), threading.Event()
    writes = []

    class Writer:
        def write(self, path, data, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test did not release the write")
            writes.append((path, data))

        def ls(self, path, **kwargs):
            self.write(path, b"listing", **kwargs)
            return []

        tree_directory = ls

    writer = Writer()
    monkeypatch.setattr(
        "openviking.pyagfs.helpers.cp", lambda client, src, dst, **_: client.write(dst, b"data")
    )
    operation = asyncio.create_task(
        getattr(AsyncAGFSClient(writer), backend.removeprefix("agfs_"))("/local/account")
        if backend.endswith("_pages")
        else AsyncAGFSClient(writer).cp("/local/account/source", "/local/account/file")
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
@pytest.mark.parametrize("method", ["ls_pages", "tree_directory_pages"])
async def test_cancel_listing_continuation_waits_for_enumeration(method):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    class Reader:
        def ls(self, path, **kwargs):
            return [{"name": "first"}]

        tree_directory = ls

        def ls_pages(self, path, **kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("Test did not release the enumeration")
            finished.set()
            return iter([[{"name": "second"}]])

        tree_directory_pages = ls_pages

    pages = await getattr(AsyncAGFSClient(Reader()), method)("/local/account", page_size=1)
    assert await anext(pages) == [{"name": "first"}]
    operation = asyncio.create_task(anext(pages))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert finished.is_set()
