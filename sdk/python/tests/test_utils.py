import asyncio
import contextvars
import threading
from unittest.mock import AsyncMock

import pytest
from openviking_sdk import SyncHTTPClient
from openviking_sdk._utils import run_async


def test_run_async_reuses_worker_loop_across_contexts_and_copies_caller_context():
    caller_thread = threading.get_ident()
    marker = contextvars.ContextVar("marker", default="missing")
    marker.set("caller")

    async def identify_execution():
        return threading.get_ident(), asyncio.get_running_loop(), marker.get()

    first_thread, first_loop, first_context = run_async(identify_execution())
    marker.set("updated")

    async def call_from_running_loop():
        return run_async(identify_execution())

    second_thread, second_loop, second_context = asyncio.run(call_from_running_loop())

    assert first_thread == second_thread != caller_thread
    assert first_loop is second_loop
    assert (first_context, second_context) == ("caller", "updated")


@pytest.mark.asyncio
async def test_sync_client_method_inside_running_loop():
    client = SyncHTTPClient(url="http://localhost:1933")
    client._async_client.list_sessions = AsyncMock(return_value=[{"session_id": "demo"}])

    assert client.list_sessions() == [{"session_id": "demo"}]


@pytest.mark.asyncio
async def test_run_async_inside_running_loop_propagates_exceptions():
    async def fail():
        await asyncio.sleep(0)
        raise ValueError("worker failed")

    with pytest.raises(ValueError, match="worker failed"):
        run_async(fail())
