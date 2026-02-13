# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async helper utilities for running coroutines from sync code.
"""

import asyncio
import atexit
import threading
from typing import Coroutine, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


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
    if _loop is not None and not _loop.is_closed() and _loop_thread is not None:
        _loop.call_soon_threadsafe(_loop.stop)
        _loop_thread.join(timeout=5)
        _loop.close()
    _loop = None
    _loop_thread = None


def run_async(coro: Coroutine[None, None, T]) -> T:
    """
    Run async coroutine from sync code.

    This function uses a shared background-thread event loop to run coroutines
    from synchronous code. This approach avoids compatibility issues with uvloop
    and other event loop implementations that don't support nested loops.

    The shared loop ensures stateful async objects (e.g. httpx.AsyncClient) stay
    on the same loop across multiple calls.

    Args:
        coro: The coroutine to run

    Returns:
        The result of coroutine
    """
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
