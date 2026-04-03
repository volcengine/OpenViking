# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Embedding Task Tracker for tracking embedding task completion status."""

import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class _EmbeddingTaskRecord:
    """Coordinator state for a single semantic message."""

    remaining: int
    total: int
    on_complete: Optional[Callable[[], Any]]
    metadata: Dict[str, Any]
    owner_loop: Optional[asyncio.AbstractEventLoop]


class EmbeddingTaskTracker:
    """Track embedding task completion status for each SemanticMsg.

    This tracker maintains a process-global registry of embedding tasks associated
    with each SemanticMsg. Because semantic and embedding queues run on separate
    worker threads with distinct event loops, its internal state must be guarded
    by thread-safe primitives rather than loop-bound asyncio locks.

    When all embedding tasks for a SemanticMsg are completed, it triggers the
    registered callback and removes the entry.
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
        record_to_finalize: Optional[_EmbeddingTaskRecord] = None

        with self._lock:
            existing = self._tasks.get(semantic_msg_id)
            if existing is not None:
                logger.warning(
                    "Overwriting existing embedding tracker record for SemanticMsg %s",
                    semantic_msg_id,
                )

            self._tasks[semantic_msg_id] = _EmbeddingTaskRecord(
                remaining=total_count,
                total=total_count,
                on_complete=on_complete,
                metadata=metadata or {},
                owner_loop=owner_loop,
            )
            logger.info(
                f"Registered embedding tracker for SemanticMsg {semantic_msg_id}: "
                f"{total_count} tasks"
            )

            if total_count <= 0:
                record_to_finalize = self._tasks.pop(semantic_msg_id)
                logger.info(
                    f"No embedding tasks for SemanticMsg {semantic_msg_id}, "
                    f"clearing tracker entry immediately"
                )

        if record_to_finalize is not None:
            await self._run_on_complete(semantic_msg_id, record_to_finalize)

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
        record_to_finalize: Optional[_EmbeddingTaskRecord] = None

        with self._lock:
            record = self._tasks.get(semantic_msg_id)
            if record is None:
                return None

            record.remaining -= 1
            remaining = record.remaining

            if remaining <= 0:
                record_to_finalize = self._tasks.pop(semantic_msg_id)
                logger.info(
                    f"All embedding tasks({record.total}) completed for SemanticMsg {semantic_msg_id}"
                )

        if record_to_finalize is not None:
            await self._run_on_complete(semantic_msg_id, record_to_finalize)
        return remaining
