from __future__ import annotations

import asyncio
import atexit
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    try:
        loop.run_forever()
    finally:
        loop.close()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return the shared event loop running in a background thread."""
    global _loop, _loop_thread
    if _loop is not None and not _loop.is_closed():
        return _loop

    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_run_loop, args=(_loop,), daemon=True)
        _loop_thread.start()
        atexit.register(_shutdown_loop)
    return _loop


def _shutdown_loop() -> None:
    """Stop and close the shared background event loop."""
    global _loop, _loop_thread
    loop = _loop
    loop_thread = _loop_thread
    if loop is not None and not loop.is_closed() and loop_thread is not None:
        loop.call_soon_threadsafe(loop.stop)
        if threading.current_thread() is not loop_thread:
            loop_thread.join(timeout=5)
    _loop = None
    _loop_thread = None


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from synchronous SDK code.

    A dedicated background loop keeps stateful async clients on one loop and
    avoids nesting ``run_until_complete`` inside an already-running caller loop.
    """
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is not None and running_loop is _loop:
        result_box: list[T] = []
        error_box: list[BaseException] = []

        def _run_in_thread() -> None:
            loop = asyncio.new_event_loop()
            try:
                result_box.append(loop.run_until_complete(coro))
            except BaseException as exc:
                error_box.append(exc)
            finally:
                loop.close()

        thread = threading.Thread(target=_run_in_thread, daemon=True)
        thread.start()
        thread.join()
        if error_box:
            raise error_box[0]
        return result_box[0]

    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
