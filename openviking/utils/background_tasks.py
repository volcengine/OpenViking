# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Keep a strong reference to fire-and-forget asyncio tasks.

The event loop only holds a *weak* reference to a running task, so a task whose
only other reference was the discarded return value of ``asyncio.create_task``
can be garbage-collected before it finishes. CPython documents this directly:

    Save a reference to the result of this function, to avoid a task
    disappearing mid-execution.

Every caller here follows the same shape — record a tracker entry, launch the
work, return the task id to the client immediately — so a collected task leaves
a tracker row that never reaches a terminal state and a caller polling it
forever.
"""

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)

# Strong references to tasks that nothing else holds. A task removes itself once
# it is done, so this set is bounded by the number of in-flight background jobs.
_BACKGROUND_TASKS: Set["asyncio.Task[Any]"] = set()


def spawn_background_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: Optional[str] = None,
) -> "asyncio.Task[Any]":
    """Start ``coro`` and hold a reference to it until it finishes.

    Returns the task so a caller that does want to await or cancel it still can.
    An exception raised by the task is logged rather than left to surface as an
    unretrieved-exception warning at interpreter shutdown.
    """
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: "asyncio.Task[Any]") -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "Background task %s failed: %s",
            task.get_name(),
            error,
            exc_info=error,
        )


def pending_background_tasks() -> Set["asyncio.Task[Any]"]:
    """Tasks currently held. Exposed for tests and for shutdown draining."""
    return set(_BACKGROUND_TASKS)
