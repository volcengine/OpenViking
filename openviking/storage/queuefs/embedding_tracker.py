# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Embedding Task Tracker for tracking embedding task completion status."""

import asyncio
import inspect
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from openviking.service.coordinator import get_coordinator
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# How often the owner-side completion poller re-reads the shared remaining
# counter under the distributed backend. The memory backend never polls (its
# decrement is in-process and drives completion synchronously).
_POLL_INTERVAL_SEC = 0.5
_ERROR_BACKOFF_MULTIPLIER = 4.0


def _get_distributed_completion_timeout_sec() -> float:
    """Return the distributed-only embedding completion timeout."""
    try:
        value = get_openviking_config().storage.coordination.embedding_completion_timeout_sec
    except Exception:
        value = 1800
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 1800.0


def _remaining_key(semantic_msg_id: str) -> str:
    return f"emb:{semantic_msg_id}:remaining"


def _reg_key(semantic_msg_id: str) -> str:
    return f"emb:{semantic_msg_id}:reg"


@dataclass
class _EmbeddingTaskRecord:
    """Owner-instance-local state for one semantic message.

    The remaining-task *count* lives in the Coordinator (so embedding messages
    dequeued by any instance decrement the same total). Only this record's
    owner instance holds the completion and timeout callbacks and the loop they
    must run on, because the completion callback may close an in-memory lock
    lease that cannot cross a process boundary.
    """

    total: int
    on_complete: Optional[Callable[[], Any]]
    on_timeout: Optional[Callable[[str], Any]]
    metadata: Dict[str, Any]
    owner_loop: Optional[asyncio.AbstractEventLoop]
    deadline_at: float = 0.0


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

    async def _execute_callback(self, callback: Callable[..., Any], *args: Any) -> None:
        """Invoke a callback and await async results."""
        await self._await_callback_result(callback(*args))

    async def _run_callback(
        self,
        semantic_msg_id: str,
        callback: Optional[Callable[..., Any]],
        owner_loop: Optional[asyncio.AbstractEventLoop],
        callback_kind: str,
        *args: Any,
    ) -> None:
        """Execute one owner-bound callback on the loop that registered it."""
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
                        "Owner loop unavailable before %s callback for %s; "
                        "running callback in current loop",
                        callback_kind,
                        semantic_msg_id,
                    )
                else:
                    try:
                        fut = asyncio.run_coroutine_threadsafe(
                            self._execute_callback(callback, *args),
                            owner_loop,
                        )
                    except RuntimeError:
                        logger.warning(
                            "Owner loop stopped before %s callback for %s; "
                            "running callback in current loop",
                            callback_kind,
                            semantic_msg_id,
                        )
                    else:
                        await asyncio.wrap_future(fut)
                        return

            await self._execute_callback(callback, *args)
        except Exception as e:
            logger.error(
                f"Error in {callback_kind} callback for {semantic_msg_id}: {e}",
                exc_info=True,
            )

    async def _run_on_complete(
        self,
        semantic_msg_id: str,
        record: _EmbeddingTaskRecord,
    ) -> None:
        """Execute the completion callback on the loop that registered it."""
        await self._run_callback(
            semantic_msg_id,
            record.on_complete,
            record.owner_loop,
            "completion",
        )

    async def _run_on_timeout(
        self,
        semantic_msg_id: str,
        record: _EmbeddingTaskRecord,
        reason: str,
    ) -> None:
        """Execute the timeout callback on the loop that registered it."""
        await self._run_callback(
            semantic_msg_id,
            record.on_timeout,
            record.owner_loop,
            "timeout",
            reason,
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
        on_timeout: Optional[Callable[[str], Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout_sec: Optional[float] = None,
    ) -> None:
        """Register a SemanticMsg with its total embedding task count.

        Args:
            semantic_msg_id: The ID of the SemanticMsg
            total_count: Total number of embedding tasks for this SemanticMsg
            on_complete: Optional callback when all tasks complete
            on_timeout: Optional callback when distributed completion times out
            metadata: Optional metadata to store with the task
            timeout_sec: Optional distributed-only watchdog timeout in seconds
        """
        owner_loop = asyncio.get_running_loop()
        configured_timeout_sec = (
            _get_distributed_completion_timeout_sec() if timeout_sec is None else timeout_sec
        )
        try:
            configured_timeout_sec = max(float(configured_timeout_sec or 0), 0.0)
        except (TypeError, ValueError):
            configured_timeout_sec = _get_distributed_completion_timeout_sec()

        record = _EmbeddingTaskRecord(
            total=total_count,
            on_complete=on_complete,
            on_timeout=on_timeout,
            metadata=metadata or {},
            owner_loop=owner_loop,
            deadline_at=(
                time.monotonic() + configured_timeout_sec
                if configured_timeout_sec > 0
                else 0.0
            ),
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
        # callback locally when it reaches zero or when the distributed-only
        # completion deadline expires.
        if coord.is_distributed:
            asyncio.create_task(self._watch_until_resolved(semantic_msg_id))

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
        if remaining < 0:
            logger.warning(
                "Embedding tracker remaining went negative for SemanticMsg %s: %s; "
                "duplicate decrement or duplicate embedding completion is likely",
                semantic_msg_id,
                remaining,
            )

        # Distributed backend: completion is driven exclusively by the owner's
        # poller (this decrement may be running on a non-owner instance that
        # has no callback). Avoid a double fire by not completing here.
        if coord.is_distributed:
            return remaining

        if remaining <= 0:
            with self._lock:
                record = self._tasks.pop(semantic_msg_id, None)
            coord.delete(_remaining_key(semantic_msg_id), _reg_key(semantic_msg_id))
            logger.info(
                f"All embedding tasks completed for SemanticMsg {semantic_msg_id}"
            )
            if record is not None:
                await self._run_on_complete(semantic_msg_id, record)
        return remaining

    async def _watch_until_resolved(self, semantic_msg_id: str) -> None:
        """Owner-side waiter: resolve distributed completion or timeout.

        Runs only under the distributed backend, on the owner's event loop.
        Fires success when remaining <= 0 while the registration is still live;
        fires timeout cleanup once the distributed-only deadline expires; and
        bails without firing if the registration vanished (TTL expiry / external
        cleanup) so abandoned requests do not trigger a false completion.
        """
        coord = get_coordinator()
        while True:
            await asyncio.sleep(_POLL_INTERVAL_SEC)
            with self._lock:
                record = self._tasks.get(semantic_msg_id)
            if record is None:
                return

            try:
                if coord.scard(_reg_key(semantic_msg_id)) == 0:
                    with self._lock:
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

                remaining = coord.get_int(_remaining_key(semantic_msg_id))
                timed_out = record.deadline_at > 0 and time.monotonic() >= record.deadline_at
                if remaining > 0 and not timed_out:
                    continue

                coord.delete(_remaining_key(semantic_msg_id), _reg_key(semantic_msg_id))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "Coordinator error in embedding poller for %s: %s; retrying",
                    semantic_msg_id,
                    e,
                )
                await asyncio.sleep(max(_POLL_INTERVAL_SEC * _ERROR_BACKOFF_MULTIPLIER, 0.1))
                continue

            with self._lock:
                record = self._tasks.pop(semantic_msg_id, None)
            if record is None:
                return

            if remaining <= 0:
                logger.info(
                    f"All embedding tasks completed for SemanticMsg {semantic_msg_id}"
                )
                await self._run_on_complete(semantic_msg_id, record)
                return

            reason = (
                f"embedding completion timeout for semantic_msg_id={semantic_msg_id}, "
                f"remaining={remaining}, total={record.total}"
            )
            logger.error(reason)
            await self._run_on_timeout(semantic_msg_id, record, reason)
            return
