# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async helper utilities for running coroutines from sync code.
"""

import asyncio
from typing import Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[None, None, T]) -> T:
    """
    Run async coroutine from sync code, handling nested event loops.

    This function safely runs a coroutine whether or not there's already
    a running event loop (e.g., when called from within an MCP server).

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
        # No running event loop, use asyncio.run()
        return asyncio.run(coro)
