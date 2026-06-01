# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Embedding Task Tracker for tracking embedding task completion status."""

import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from openviking.service.coordinator import get_coordinator
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# How often the owner-side completion poller re-reads the shared remaining
# counter under the distributed backend. The memory backend never polls (its
# decrement is in-process and drives completion synchronously).
_POLL_INTERVAL_SEC = 0.5



def _remaining_key(semantic_msg_id: str) -> str:
    return f"emb:{semantic_msg_id}:remaining"


def _reg_key(semantic_msg_id: str) -> str:
    return f"emb:{semantic_msg_id}:reg"


@dataclass
class _EmbeddingTaskRecord:
    """Owner-instance-local state for a single semantic message.

    The remaining-task *count* lives in the Coordinator (so embedding messages
    dequeued by any instance decrement the same total). Only this record's
    owner instance holds the completion callback and the loop it must run on,
    because the callback closes an in-memory lock lease that cannot cross a
    process boundary.
    """

    total: int
    on_complete: Optional[Callable[[], Any]]
    metadata: Dict[str, Any]
    owner_loop: Optional[asyncio.AbstractEventLoop]


class EmbeddingTaskTracker:
    """Track embedding task completion status for each SemanticMsg.

    The shared remaining-task counter is held in the Coordinator, making the
    count consistent across load-balanced instances that dequeue embedding
    messages from the same queue. The completion callback (which releases an
    owner-local lock lease and marks the semantic root done) stays on the
    instance/loop that registered it.

    Because semantic and embedding queues run on separate worker threads with
    distinct event loops, the owner-local registry is guarded by a thread-safe
    primitive rather than a loop-bound asyncio lock.
    """

    _instance: Optional["EmbeddingTaskTracker"] = None
    _initialized: bool = False

    def __new__(cls) -> "EmbeddingTaskTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._lock = threading.Lock()
        self._tasks: Dict[str, _EmbeddingTaskRecord] = {}
        self._initialized = True

    @staticmethod
    async def _await_callback_result(result: Any) -> None:
        """Await callback results when they are async."""
        if inspect.isawaitable(result):
            await result

    async def _execute_callback(self, on_complete: Callable[[], Any]) -> None:
        """Invoke a completion callback and await async results."""
        await self._await_callback_result(on_complete())

    async def _run_on_complete(
        self,
        semantic_msg_id: str,
        record: _EmbeddingTaskRecord,
    ) -> None:
        """Execute the completion callback on the loop that registered it."""
        on_complete = record.on_complete
        owner_loop = record.owner_loop
        if on_complete is None:
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        owner_loop_running = bool(owner_loop and owner_loop.is_running())
        owner_loop_available = bool(
            owner_loop and not owner_loop.is_closed() and owner_loop_running
        )

        try:
            if owner_loop and owner_loop is not current_loop:
                if not owner_loop_available:
                    logger.warning(
                        "Owner loop unavailable before completion callback for %s; "
                        "running callback in current loop",
                        semantic_msg_id,
                    )
                else:
                    try:
                        fut = asyncio.run_coroutine_threadsafe(
                            self._execute_callback(on_complete),
                            owner_loop,
                        )
                    except RuntimeError:
                        logger.warning(
                            "Owner loop stopped before completion callback for %s; "
                            "running callback in current loop",
                            semantic_msg_id,
                        )
                    else:
                        await asyncio.wrap_future(fut)
                        return

            await self._execute_callback(on_complete)
        except Exception as e:
            logger.error(
                f"Error in completion callback for {semantic_msg_id}: {e}",
                exc_info=True,
            )

    @classmethod
    def get_instance(cls) -> "EmbeddingTaskTracker":
        """Get the singleton instance of EmbeddingTaskTracker."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def register(
        self,
        semantic_msg_id: str,
        total_count: int,
        on_complete: Optional[Callable[[], Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a SemanticMsg with its total embedding task count.

        Args:
            semantic_msg_id: The ID of the SemanticMsg
            total_count: Total number of embedding tasks for this SemanticMsg
            on_complete: Optional callback when all tasks complete
            metadata: Optional metadata to store with the task
        """
        owner_loop = asyncio.get_running_loop()
        record = _EmbeddingTaskRecord(
            total=total_count,
            on_complete=on_complete,
            metadata=metadata or {},
            owner_loop=owner_loop,
        )

        if total_count <= 0:
            logger.info(
                f"No embedding tasks for SemanticMsg {semantic_msg_id}, "
                f"running completion callback immediately"
            )
            await self._run_on_complete(semantic_msg_id, record)
            return

        coord = get_coordinator()
        # Authoritative set: drop any stale counter from a reused id, then seed
        # the total. The reg marker lets decrement distinguish a tracked id
        # (all-tasks-pending) from an unknown one.
        coord.delete(_remaining_key(semantic_msg_id))
        coord.incr(_remaining_key(semantic_msg_id), total_count)
        coord.sadd(_reg_key(semantic_msg_id), "1")

        with self._lock:
            if self._tasks.get(semantic_msg_id) is not None:
                logger.warning(
                    "Overwriting existing embedding tracker record for SemanticMsg %s",
                    semantic_msg_id,
                )
            self._tasks[semantic_msg_id] = record

        logger.info(
            f"Registered embedding tracker for SemanticMsg {semantic_msg_id}: "
            f"{total_count} tasks"
        )

        # Under the distributed backend the final decrement may land on a
        # different instance that holds neither the callback nor the loop it
        # must run on. The owner watches the shared counter and fires the
        # callback locally when it reaches zero.
        if coord.is_distributed:
            asyncio.create_task(self._poll_until_complete(semantic_msg_id))

    async def decrement(self, semantic_msg_id: str) -> Optional[int]:
        """Decrement the remaining task count for a SemanticMsg.

        This method should be called when an embedding task is completed.
        When the count reaches zero, the registered callback is executed
        and the entry is removed from the tracker.

        Args:
            semantic_msg_id: The ID of the SemanticMsg

        Returns:
            The remaining count after decrement, or None if not found
        """
        coord = get_coordinator()
        if coord.scard(_reg_key(semantic_msg_id)) == 0:
            return None

        remaining = coord.incr(_remaining_key(semantic_msg_id), -1)

        # Distributed backend: completion is driven exclusively by the owner's
        # poller (this decrement may be running on a non-owner instance that
        # has no callback). Avoid a double fire by not completing here.
        if coord.is_distributed:
            return remaining

        if remaining <= 0:
            record = self._tasks.pop(semantic_msg_id, None)
            coord.delete(_remaining_key(semantic_msg_id), _reg_key(semantic_msg_id))
            logger.info(
                f"All embedding tasks completed for SemanticMsg {semantic_msg_id}"
            )
            if record is not None:
                await self._run_on_complete(semantic_msg_id, record)
        return remaining

    async def _poll_until_complete(self, semantic_msg_id: str) -> None:
        """Owner-side waiter: fire the callback once the shared counter is drained.

        Runs only under the distributed backend, on the owner's event loop.
        Fires when remaining <= 0 while the registration is still live; bails
        without firing if the registration vanished (TTL expiry / external
        cleanup) so abandoned requests do not trigger a false completion.
        """
        coord = get_coordinator()
        while True:
            await asyncio.sleep(_POLL_INTERVAL_SEC)
            if coord.scard(_reg_key(semantic_msg_id)) == 0:
                self._tasks.pop(semantic_msg_id, None)
                logger.warning(
                    "Embedding tracker registration for %s expired before "
                    "completion; abandoning completion callback",
                    semantic_msg_id,
                )
                return
            if coord.default_ttl_sec > 0:
                coord.expire(_reg_key(semantic_msg_id), coord.default_ttl_sec)
                coord.expire(_remaining_key(semantic_msg_id), coord.default_ttl_sec)
            if coord.get_int(_remaining_key(semantic_msg_id)) <= 0:
                record = self._tasks.pop(semantic_msg_id, None)
                coord.delete(
                    _remaining_key(semantic_msg_id), _reg_key(semantic_msg_id)
                )
                logger.info(
                    f"All embedding tasks completed for SemanticMsg {semantic_msg_id}"
                )
                if record is not None:
                    await self._run_on_complete(semantic_msg_id, record)
                return
