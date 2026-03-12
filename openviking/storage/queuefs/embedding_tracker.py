# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Embedding Task Tracker for tracking embedding task completion status."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class EmbeddingTaskTracker:
    """Track embedding task completion status for each SemanticMsg.

    This tracker maintains a global registry of embedding tasks associated
    with each SemanticMsg. When all embedding tasks for a SemanticMsg are
    completed, it triggers the registered callback and removes the entry.
    """

    _instance: Optional["EmbeddingTaskTracker"] = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _tasks: Dict[str, Dict[str, Any]] = field(default_factory=dict)

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
        async with self._lock:
            self._tasks[semantic_msg_id] = {
                "remaining": total_count,
                "total": total_count,
                "on_complete": on_complete,
                "metadata": metadata or {},
            }
            logger.info(
                f"Registered embedding tracker for SemanticMsg {semantic_msg_id}: "
                f"{total_count} tasks"
            )

            if total_count <= 0 and on_complete:
                del self._tasks[semantic_msg_id]
                logger.info(
                    f"No embedding tasks for SemanticMsg {semantic_msg_id}, "
                    f"triggering on_complete immediately"
                )

        if total_count <= 0 and on_complete:
            try:
                result = on_complete()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(
                    f"Error in completion callback for {semantic_msg_id}: {e}",
                    exc_info=True,
                )

    async def increment(self, semantic_msg_id: str) -> Optional[int]:
        """Increment the remaining task count for a SemanticMsg.

        This method should be called when a new embedding task is added
        for an already registered SemanticMsg.

        Args:
            semantic_msg_id: The ID of the SemanticMsg

        Returns:
            The remaining count after increment, or None if not found
        """
        async with self._lock:
            if semantic_msg_id not in self._tasks:
                return None

            task_info = self._tasks[semantic_msg_id]
            task_info["remaining"] += 1
            task_info["total"] += 1
            remaining = task_info["remaining"]

        return remaining

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
        on_complete = None

        async with self._lock:
            if semantic_msg_id not in self._tasks:
                return None

            task_info = self._tasks[semantic_msg_id]
            task_info["remaining"] -= 1
            remaining = task_info["remaining"]

            if remaining <= 0:
                on_complete = task_info.get("on_complete")

                del self._tasks[semantic_msg_id]
                logger.info(
                    f"All embedding tasks({task_info['total']}) completed for SemanticMsg {semantic_msg_id}"
                )

        if on_complete:
            try:
                result = on_complete()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(
                    f"Error in completion callback for {semantic_msg_id}: {e}",
                    exc_info=True,
                )
        return remaining

    async def get_status(self, semantic_msg_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a SemanticMsg's embedding tasks.

        Args:
            semantic_msg_id: The ID of the SemanticMsg

        Returns:
            Dict with 'remaining', 'total', 'metadata' or None if not found
        """
        async with self._lock:
            if semantic_msg_id not in self._tasks:
                return None
            task_info = self._tasks[semantic_msg_id]
            return {
                "remaining": task_info["remaining"],
                "total": task_info["total"],
                "metadata": task_info.get("metadata", {}),
            }

    async def remove(self, semantic_msg_id: str) -> bool:
        """Remove a SemanticMsg from the tracker.

        Args:
            semantic_msg_id: The ID of the SemanticMsg

        Returns:
            True if removed, False if not found
        """
        async with self._lock:
            if semantic_msg_id in self._tasks:
                del self._tasks[semantic_msg_id]
                return True
            return False

    async def get_all_tracked(self) -> Dict[str, Dict[str, Any]]:
        """Get all currently tracked SemanticMsgs.

        Returns:
            Dict of semantic_msg_id -> task info
        """
        async with self._lock:
            return {
                msg_id: {
                    "remaining": info["remaining"],
                    "total": info["total"],
                    "metadata": info.get("metadata", {}),
                }
                for msg_id, info in self._tasks.items()
            }
