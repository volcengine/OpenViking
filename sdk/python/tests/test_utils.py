# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading
from pathlib import Path

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient, _utils
from openviking_sdk._utils import _path_is_relative_to, run_async
from openviking_sdk.options import FindOptions


def test_path_is_relative_to_handles_paths_outside_root():
    root = Path("/tmp/openviking-sdk-root")

    assert _path_is_relative_to(root / "nested" / "file.txt", root)
    assert not _path_is_relative_to(Path("/tmp/openviking-sdk-other"), root)


def test_options_payload_uses_typed_dict_fields_on_supported_python_versions():
    payload = AsyncHTTPClient._build_options_payload(
        {"node_limit": 3},
        FindOptions,
    )

    assert payload == {"node_limit": 3}


def test_run_async_reuses_worker_loop_across_sync_and_async_contexts():
    async def identify_execution():
        return asyncio.get_running_loop()

    first_loop = run_async(identify_execution())

    async def call_from_running_loop():
        return run_async(identify_execution())

    second_loop = asyncio.run(call_from_running_loop())

    assert first_loop is second_loop


@pytest.mark.asyncio
async def test_run_async_preserves_cancelled_error():
    async def cancel():
        raise asyncio.CancelledError("cancelled by caller")

    with pytest.raises(asyncio.CancelledError, match="cancelled by caller"):
        run_async(cancel())


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_sync_read_cancels_background_request(monkeypatch, interruption):
    started = threading.Event()
    cancelled = threading.Event()
    submitted = []
    client = SyncHTTPClient(url="http://localhost:1933")

    async def handle_request(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def setup():
        client._async_client._http = httpx.AsyncClient(
            base_url="http://localhost:1933", transport=httpx.MockTransport(handle_request)
        )

    run_async(setup())
    original_submit = asyncio.run_coroutine_threadsafe

    class InterruptedWait:
        def __init__(self, future):
            self.future = future

        def result(self):
            assert started.wait(5), "read did not reach the HTTP transport"
            raise interruption("caller interrupted")

        def cancel(self):
            return self.future.cancel()

    def submit(coro, loop):
        # Keep the real worker task; interrupt only its synchronous waiter.
        future = original_submit(coro, loop)
        submitted.append(future)
        return InterruptedWait(future)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(_utils.asyncio, "run_coroutine_threadsafe", submit)
            with pytest.raises(interruption, match="caller interrupted"):
                client.read("viking://resources/test.txt")
            assert cancelled.wait(2), "request kept running after the sync caller returned"
        assert run_async(asyncio.sleep(0, result="worker reusable")) == "worker reusable"
    finally:
        for future in submitted:
            future.cancel()
        client.close()
