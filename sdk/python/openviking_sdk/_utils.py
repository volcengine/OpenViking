from __future__ import annotations

import asyncio
import atexit
import threading
from contextvars import copy_context
from typing import Any, Coroutine

_worker_lock = threading.Lock()
_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_thread: threading.Thread | None = None


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop, _worker_thread
    if _worker_loop is not None and _worker_thread is not None and _worker_thread.is_alive():
        return _worker_loop
    with _worker_lock:
        if _worker_loop is not None and _worker_thread is not None and _worker_thread.is_alive():
            return _worker_loop
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


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    worker_loop = _get_worker_loop()
    if running_loop is worker_loop:
        coro.close()
        raise RuntimeError("run_async cannot be called from its worker event loop")
    future = copy_context().run(asyncio.run_coroutine_threadsafe, coro, worker_loop)
    return future.result()
