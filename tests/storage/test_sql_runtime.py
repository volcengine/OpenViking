# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Platform selection must apply to the SQL runtime's private thread."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openviking.storage import sql_runtime


@pytest.mark.parametrize("platform", ["win32", "linux"])
async def test_private_thread_selects_driver_compatible_loop(monkeypatch, platform):
    caller_loop = asyncio.get_running_loop()
    caller_thread = threading.get_ident()
    policy = asyncio.get_event_loop_policy()
    default_factory = Mock(side_effect=asyncio.new_event_loop)
    monkeypatch.setattr(sql_runtime, "sys", SimpleNamespace(platform=platform), raising=False)
    monkeypatch.setattr(asyncio, "new_event_loop", default_factory)
    runtime = sql_runtime.SQLRuntime()
    await asyncio.wait_for(runtime.acquire(), timeout=5)
    thread = runtime._thread
    try:

        async def inspect_private_loop():
            return asyncio.get_running_loop(), threading.get_ident()

        loop, thread_id = await asyncio.wait_for(runtime.run(inspect_private_loop), timeout=5)
        assert loop is not caller_loop
        assert thread_id != caller_thread
        if platform == "win32":
            assert isinstance(loop, asyncio.SelectorEventLoop)
            # Windows' policy factory normally creates a psycopg-incompatible
            # Proactor loop. It must be bypassed inside the private thread.
            default_factory.assert_not_called()
        else:
            default_factory.assert_called_once_with()
        assert asyncio.get_event_loop_policy() is policy
    finally:
        await asyncio.wait_for(runtime.release(), timeout=5)
        await asyncio.to_thread(thread.join, 5)
        assert not thread.is_alive()
    assert loop.is_closed()
