"""Remote export keeps ownership of local I/O until cleanup completes."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.sandbox.backends.opensandbox import OpenSandboxBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_io", ["open", "write"])
async def test_cancelled_export_joins_file_io_before_discard(tmp_path, monkeypatch, blocked_io):
    destination = tmp_path / "copy.md"
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()
    open_file = Path.open
    handles = []

    class Download:
        consumed = False
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.consumed:
                raise StopAsyncIteration
            self.consumed = True
            return b"synthetic body"

        async def aclose(self):
            self.closed = True

    download = Download()
    backend = object.__new__(OpenSandboxBackend)
    backend._sandbox = SimpleNamespace()
    backend._read_bytes_stream = AsyncMock(return_value=download)

    def wait_in_thread():
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise TimeoutError("test did not release local I/O")

    class Output:
        def __init__(self, handle):
            self.handle = handle

        def write(self, chunk):
            try:
                wait_in_thread()
                assert not self.handle.closed
                return self.handle.write(chunk)
            finally:
                finished.set()

        def close(self):
            self.handle.close()

    def delayed_open(path, *args, **kwargs):
        if path != destination:
            return open_file(path, *args, **kwargs)
        try:
            if blocked_io == "open":
                wait_in_thread()
            handle = open_file(path, *args, **kwargs)
            handles.append(handle)
            return Output(handle) if blocked_io == "write" else handle
        finally:
            if blocked_io == "open":
                finished.set()

    monkeypatch.setattr(Path, "open", delayed_open)
    task = asyncio.create_task(backend.export_file("page.md", destination, max_bytes=64))
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=0.05)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Ensure even the failing baseline has settled its test thread before teardown.
        assert await asyncio.to_thread(finished.wait, 3)
        all_closed = all(handle.closed for handle in handles)
        for handle in handles:
            if not handle.closed:
                handle.close()
    assert not done
    assert all_closed
    assert download.closed
    assert not destination.exists()
