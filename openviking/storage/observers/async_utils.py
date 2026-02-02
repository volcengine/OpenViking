# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async helper utilities for observers.
"""

import asyncio
import threading
from typing import Any, Awaitable, Callable


def run_coroutine_sync(coro_factory: Callable[[], Awaitable[Any]]) -> Any:
    """
    Run a coroutine from sync code.

    If an event loop is already running in this thread, execute the coroutine
    in a dedicated thread with its own event loop.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: dict = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in result:
        raise result["error"]

    return result.get("value")
