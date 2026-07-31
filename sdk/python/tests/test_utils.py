import asyncio

import pytest
from openviking_sdk._utils import run_async


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
