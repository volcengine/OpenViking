# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async helper utilities for running coroutines from sync code.
"""

import asyncio
import atexit
import threading
from enum import Enum
from typing import Coroutine, Dict, TypeVar

T = TypeVar("T")


class LoopType(Enum):
    """事件循环类型，用于隔离不同类型的任务"""

    CLIENT = "client"  # 前台任务：客户端请求、API调用
    BACKGROUND = "background"  # 后台任务：队列处理、批量操作
    OBSERVER = "observer"  # 监控任务：状态查询、健康检查


_lock = threading.Lock()
_loop_pool: Dict[LoopType, asyncio.AbstractEventLoop | None] = {}
_loop_thread_pool: Dict[LoopType, threading.Thread | None] = {}
_default_loop_type = LoopType.CLIENT


def _get_loop(loop_type: LoopType = _default_loop_type) -> asyncio.AbstractEventLoop:
    """Get or create an event loop for the specified type running in a background thread."""
    current_loop = _loop_pool.get(loop_type)
    if current_loop is not None and not current_loop.is_closed():
        return current_loop
    with _lock:
        current_loop = _loop_pool.get(loop_type)
        if current_loop is not None and not current_loop.is_closed():
            return current_loop
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        _loop_pool[loop_type] = loop
        _loop_thread_pool[loop_type] = loop_thread
        atexit.register(lambda: _shutdown_loop(loop_type))
        return loop


def _shutdown_loop(loop_type: LoopType = _default_loop_type):
    """Shutdown the specified loop on process exit."""
    loop = _loop_pool.get(loop_type)
    loop_thread = _loop_thread_pool.get(loop_type)
    if loop is not None and not loop.is_closed() and loop_thread is not None:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        loop.close()
    if loop_type in _loop_pool:
        _loop_pool[loop_type] = None
    if loop_type in _loop_thread_pool:
        _loop_thread_pool[loop_type] = None


# ========== 向后兼容性接口 ==========
# 保持原有签名，默认使用 CLIENT 循环
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def run_async(coro: Coroutine[None, None, T], loop_type: LoopType = _default_loop_type) -> T:
    """
    Run async coroutine from sync code.

    This function uses a shared background-thread event loop to run coroutines
    from synchronous code. This approach avoids compatibility issues with uvloop
    and other event loop implementations that don't support nested loops.

    The shared loop ensures stateful async objects (e.g. httpx.AsyncClient) stay
    on the same loop across multiple calls.

    Re-entrant safe: if called from a context where an event loop is already
    running on the current thread (e.g. Session methods invoked by async code
    on the shared loop), the coroutine is executed on a fresh event loop in a
    new thread to avoid deadlock.

    Args:
        coro: The coroutine to run
        loop_type: The type of event loop to use (CLIENT, BACKGROUND, or OBSERVER).
                   Defaults to CLIENT for backward compatibility.

    Returns:
        The result of coroutine
    """
    # Detect re-entrancy: if the current thread already has a running event
    # loop, we cannot use run_until_complete or block on the shared loop.
    # Spawn a helper thread with its own loop instead.
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is not None:
        result_box: list = []
        error_box: list = []

        def _run_in_thread() -> None:
            tmp_loop = asyncio.new_event_loop()
            try:
                result_box.append(tmp_loop.run_until_complete(coro))
            except BaseException as exc:
                error_box.append(exc)
            finally:
                tmp_loop.close()

        t = threading.Thread(target=_run_in_thread, daemon=True)
        t.start()
        t.join()
        if error_box:
            raise error_box[0]
        return result_box[0]

    loop = _get_loop(loop_type)
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
