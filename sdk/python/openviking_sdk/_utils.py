from __future__ import annotations

import asyncio
import atexit
import os
import threading
from typing import Any, Coroutine

_worker_lock = threading.Lock()
_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_thread: threading.Thread | None = None


async def _capture_result(coro: Coroutine[Any, Any, Any]) -> tuple[bool, Any]:
    try:
        return True, await coro
    except BaseException as exc:
        return False, exc


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop, _worker_thread
    with _worker_lock:
        if _worker_loop is None or _worker_thread is None or not _worker_thread.is_alive():
            _worker_loop = asyncio.new_event_loop()
            _worker_thread = threading.Thread(target=_worker_loop.run_forever, daemon=True)
            _worker_thread.start()
            atexit.register(_shutdown_worker_loop)
        return _worker_loop


def _shutdown_worker_loop() -> None:
    global _worker_loop, _worker_thread
    if _worker_loop is not None and _worker_thread is not None and _worker_thread.is_alive():
        _worker_loop.call_soon_threadsafe(_worker_loop.stop)
        _worker_thread.join(timeout=5)
    if _worker_loop is not None and not _worker_loop.is_running():
        _worker_loop.close()
    _worker_loop = None
    _worker_thread = None


def _reset_worker_after_fork() -> None:
    global _worker_lock, _worker_loop, _worker_thread
    _worker_lock = threading.Lock()
    _worker_loop = _worker_thread = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_worker_after_fork)


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    worker_loop = _get_worker_loop()
    if running_loop is worker_loop:
        coro.close()
        raise RuntimeError("run_async cannot be called from its worker event loop")
    future = asyncio.run_coroutine_threadsafe(_capture_result(coro), worker_loop)
    succeeded, value = future.result()
    if succeeded:
        return value
    raise value
