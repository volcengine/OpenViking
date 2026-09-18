import asyncio
from pathlib import Path

import pytest
from openviking_sdk import AsyncHTTPClient
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
