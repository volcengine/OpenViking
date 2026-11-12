# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.pyagfs.copy_progress import record_copy_success, track_copy_progress
from openviking.pyagfs.helpers import cp


class _Client:
    def __init__(self, *, stream_supported=True, fail_write=False):
        self.stream_supported = stream_supported
        self.fail_write = fail_write
        self.writes = []

    def stat(self, path, *, ctx=None):
        return {"isDir": path.endswith("/dir")}

    def cat(self, path, *, stream=False, ctx=None):
        if stream and not self.stream_supported:
            raise RuntimeError("streaming not supported")
        return iter([b"one", b"two"]) if stream else b"onetwo"

    def write(self, path, data, *, ctx=None):
        if self.fail_write:
            raise OSError("write failed")
        self.writes.append((path, data if isinstance(data, bytes) else b"".join(data)))


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("stream_supported", [False, True])
@pytest.mark.parametrize("fail_write", [False, True])
def test_copy_progress_records_only_acknowledged_stream_or_buffer_writes(
    stream, stream_supported, fail_write
):
    client = _Client(stream_supported=stream_supported, fail_write=fail_write)
    with track_copy_progress() as paths:
        if fail_write:
            with pytest.raises(OSError, match="write failed"):
                cp(client, "/local/a/dir/source", "/local/a/dir/target", stream=stream)
        else:
            cp(client, "/local/a/dir/source", "/local/a/dir/target", stream=stream)
    assert paths == (set() if fail_write else {"/local/a/dir/target"})
    assert client.writes == ([] if fail_write else [("/local/a/dir/target", b"onetwo")])


def test_nested_and_disabled_trackers_restore_parent_context():
    with track_copy_progress() as outer:
        record_copy_success("/outer/first")
        with track_copy_progress() as inner:
            record_copy_success("/inner")
        with track_copy_progress(enabled=False) as disabled:
            record_copy_success("/disabled")
        record_copy_success("/outer/second/")
    record_copy_success("/outside")
    assert outer == {"/outer/first", "/outer/second"}
    assert inner == {"/inner"}
    assert disabled == set()


@pytest.mark.asyncio
async def test_concurrent_async_copies_keep_separate_thread_progress():
    barrier = threading.Barrier(2)

    class _ConcurrentClient(_Client):
        def write(self, path, data, *, ctx=None):
            barrier.wait(timeout=5)
            super().write(path, data, ctx=ctx)

    agfs = AsyncAGFSClient(_ConcurrentClient())

    async def copy_one(name):
        with track_copy_progress() as paths:
            await agfs.cp("/local/a/dir/source", "/local/a/dir/" + name)
        return paths

    first, second = await asyncio.gather(copy_one("first"), copy_one("second"))
    assert first == {"/local/a/dir/first"}
    assert second == {"/local/a/dir/second"}


@pytest.mark.parametrize("method", ["write", "mkdir"])
@pytest.mark.asyncio
async def test_cancelled_copy_primitive_retains_worker_acknowledgement(method):
    started = threading.Event()
    resume = threading.Event()

    class _BlockingClient:
        def _commit(self):
            started.set()
            if not resume.wait(timeout=5):
                raise TimeoutError("test did not release I/O")
            return "committed"

        def write(self, path, data, *, ctx=None):
            return self._commit()

        def mkdir(self, path, *, ctx=None):
            return self._commit()

    agfs = AsyncAGFSClient(_BlockingClient())
    path = "/local/a/dir/target"

    async def run():
        with track_copy_progress() as paths:
            try:
                await (agfs.write(path, b"x") if method == "write" else agfs.mkdir(path))
            except asyncio.CancelledError:
                return paths
        raise AssertionError("test must cancel the operation")

    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        resume.set()
        assert await asyncio.wait_for(task, timeout=5) == {path}
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
