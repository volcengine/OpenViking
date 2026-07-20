from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest
from openviking_sdk import _utils
from openviking_sdk.client import SyncHTTPClient


@pytest.fixture(autouse=True)
def reset_background_loop():
    _utils._shutdown_loop()
    yield
    _utils._shutdown_loop()


def test_run_async_uses_shared_background_loop_across_sync_calls():
    seen_threads: list[int] = []

    async def capture_thread() -> str:
        seen_threads.append(threading.get_ident())
        return "ok"

    assert _utils.run_async(capture_thread()) == "ok"
    assert _utils.run_async(capture_thread()) == "ok"

    assert _utils._loop_thread is not None
    assert seen_threads == [_utils._loop_thread.ident, _utils._loop_thread.ident]


def test_run_async_works_inside_an_existing_event_loop():
    async def capture_thread() -> int:
        return threading.get_ident()

    async def outer() -> tuple[int, int]:
        caller_thread = threading.get_ident()
        worker_thread = _utils.run_async(capture_thread())
        return caller_thread, worker_thread

    caller_thread, worker_thread = asyncio.run(outer())

    assert worker_thread != caller_thread
    assert _utils._loop_thread is not None
    assert worker_thread == _utils._loop_thread.ident


def test_sync_client_works_inside_an_existing_event_loop():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client._get_system_status = AsyncMock(return_value={"is_healthy": True})

    async def outer() -> bool:
        return client.is_healthy()

    assert asyncio.run(outer()) is True


def test_run_async_propagates_coroutine_exceptions():
    async def fail() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _utils.run_async(fail())
