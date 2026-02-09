# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async helper utilities for running coroutines from sync code.
"""

import atexit
import asyncio
import threading
from typing import Coroutine, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop = None
_loop_thread: threading.Thread = None


def _get_loop() -> asyncio.AbstractEventLoop:
    """Get or create a shared event loop running in a background thread."""
    global _loop, _loop_thread
    if _loop is not None and not _loop.is_closed():
        return _loop
    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
        _loop_thread.start()
        atexit.register(_shutdown_loop)
    return _loop


def _shutdown_loop():
    """Shutdown the shared loop on process exit."""
    global _loop, _loop_thread
    if _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(_loop.stop)
        _loop_thread.join(timeout=5)
        _loop.close()
    _loop = None
    _loop_thread = None


def run_async(coro: Coroutine[None, None, T]) -> T:
    """
    Run async coroutine from sync code, handling nested event loops.

    This function safely runs a coroutine whether or not there's already
    a running event loop (e.g., when called from within an MCP server).
    When no loop is running, uses a shared background-thread loop so that
    stateful async objects (e.g. httpx.AsyncClient) stay on the same loop.

    Args:
        coro: The coroutine to run

    Returns:
        The result of the coroutine
    """
    try:
        loop = asyncio.get_running_loop()
        # Already in event loop, use nest_asyncio to allow nested calls
        import nest_asyncio

        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    except RuntimeError:
        # No running event loop — dispatch to the shared background loop
        loop = _get_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()
