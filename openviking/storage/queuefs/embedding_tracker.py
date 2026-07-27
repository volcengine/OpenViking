# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Embedding Task Tracker for tracking embedding task completion status."""

import asyncio
import inspect
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    sealed: bool = True
    tracks_leaf: bool = False
    leaf_remaining: int = 0
    leaf_total: int = 0
    leaf_sealed: bool = True
    on_leaf_complete: Optional[Callable[[], Any]] = None
    leaf_callback_fired: bool = False


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

    async def _run_callback(
        self,
        semantic_msg_id: str,
        callback_name: str,
        callback: Optional[Callable[[], Any]],
        owner_loop: Optional[asyncio.AbstractEventLoop],
    ) -> None:
        """Execute a tracker callback on the loop that registered it."""
        if callback is None:
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
                            self._execute_callback(callback),
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

            await self._execute_callback(callback)
        except Exception as e:
            logger.error(
                f"Error in {callback_name} callback for {semantic_msg_id}: {e}",
                exc_info=True,
            )

    async def _run_callbacks(
        self,
        semantic_msg_id: str,
        callbacks: List[
            Tuple[str, Optional[Callable[[], Any]], Optional[asyncio.AbstractEventLoop]]
        ],
    ) -> None:
        for callback_name, callback, owner_loop in callbacks:
            await self._run_callback(
                semantic_msg_id,
                callback_name,
                callback,
                owner_loop,
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
            await self._run_callback(
                semantic_msg_id,
                "completion",
                record_to_finalize.on_complete,
                record_to_finalize.owner_loop,
            )

    async def register_open(
        self,
        semantic_msg_id: str,
        on_complete: Optional[Callable[[], Any]] = None,
        on_leaf_complete: Optional[Callable[[], Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a tracker whose task counts will be discovered incrementally."""
        owner_loop = asyncio.get_running_loop()
        with self._lock:
            existing = self._tasks.get(semantic_msg_id)
            if existing is not None:
                logger.warning(
                    "Overwriting existing embedding tracker record for SemanticMsg %s",
                    semantic_msg_id,
                )

            self._tasks[semantic_msg_id] = _EmbeddingTaskRecord(
                remaining=0,
                total=0,
                on_complete=on_complete,
                metadata=metadata or {},
                owner_loop=owner_loop,
                sealed=False,
                tracks_leaf=True,
                leaf_sealed=False,
                on_leaf_complete=on_leaf_complete,
            )

    async def add(
        self,
        semantic_msg_id: str,
        count: int,
        leaf_count: int = 0,
    ) -> None:
        """Add newly discovered embedding tasks to an open tracker."""
        if count < 0 or leaf_count < 0 or leaf_count > count:
            raise ValueError("count and leaf_count must satisfy 0 <= leaf_count <= count")

        with self._lock:
            record = self._tasks.get(semantic_msg_id)
            if record is None:
                raise KeyError(f"Embedding tracker not found: {semantic_msg_id}")
            if record.sealed and count:
                raise RuntimeError(f"Embedding tracker is sealed: {semantic_msg_id}")
            if record.leaf_sealed and leaf_count:
                raise RuntimeError(f"Leaf embedding tracker is sealed: {semantic_msg_id}")

            record.remaining += count
            record.total += count
            record.leaf_remaining += leaf_count
            record.leaf_total += leaf_count

    async def seal_leaf(self, semantic_msg_id: str) -> Optional[int]:
        """Seal leaf discovery and fire the leaf callback once all leaves finish."""
        callbacks: List[
            Tuple[str, Optional[Callable[[], Any]], Optional[asyncio.AbstractEventLoop]]
        ] = []
        with self._lock:
            record = self._tasks.get(semantic_msg_id)
            if record is None:
                return None

            record.leaf_sealed = True
            if record.leaf_remaining <= 0 and not record.leaf_callback_fired:
                record.leaf_callback_fired = True
                callbacks.append(("leaf completion", record.on_leaf_complete, record.owner_loop))
            remaining = record.leaf_remaining

        await self._run_callbacks(semantic_msg_id, callbacks)
        return remaining

    async def seal(self, semantic_msg_id: str) -> Optional[int]:
        """Seal all task discovery and complete once all registered tasks finish."""
        callbacks: List[
            Tuple[str, Optional[Callable[[], Any]], Optional[asyncio.AbstractEventLoop]]
        ] = []
        with self._lock:
            record = self._tasks.get(semantic_msg_id)
            if record is None:
                return None

            record.sealed = True
            record.leaf_sealed = True
            if record.leaf_remaining <= 0 and not record.leaf_callback_fired:
                record.leaf_callback_fired = True
                callbacks.append(("leaf completion", record.on_leaf_complete, record.owner_loop))

            remaining = record.remaining
            if remaining <= 0:
                self._tasks.pop(semantic_msg_id)
                callbacks.append(("completion", record.on_complete, record.owner_loop))

        await self._run_callbacks(semantic_msg_id, callbacks)
        return remaining

    async def discard(self, semantic_msg_id: str) -> bool:
        """Remove a tracker without running completion callbacks."""
        with self._lock:
            return self._tasks.pop(semantic_msg_id, None) is not None

    async def decrement(
        self,
        semantic_msg_id: str,
        is_leaf: bool = False,
    ) -> Optional[int]:
        """Decrement the remaining task count for a SemanticMsg.

        This method should be called when an embedding task is completed.
        When the count reaches zero, the registered callback is executed
        and the entry is removed from the tracker.

        Args:
            semantic_msg_id: The ID of the SemanticMsg

        Returns:
            The remaining count after decrement, or None if not found
        """
        callbacks: List[
            Tuple[str, Optional[Callable[[], Any]], Optional[asyncio.AbstractEventLoop]]
        ] = []

        with self._lock:
            record = self._tasks.get(semantic_msg_id)
            if record is None:
                return None

            record.remaining -= 1
            remaining = record.remaining
            if is_leaf and record.tracks_leaf:
                record.leaf_remaining -= 1

            if (
                record.tracks_leaf
                and record.leaf_sealed
                and record.leaf_remaining <= 0
                and not record.leaf_callback_fired
            ):
                record.leaf_callback_fired = True
                callbacks.append(("leaf completion", record.on_leaf_complete, record.owner_loop))

            if record.sealed and remaining <= 0:
                self._tasks.pop(semantic_msg_id)
                logger.info(
                    f"All embedding tasks({record.total}) completed for SemanticMsg {semantic_msg_id}"
                )
                callbacks.append(("completion", record.on_complete, record.owner_loop))

        await self._run_callbacks(semantic_msg_id, callbacks)
        return remaining
