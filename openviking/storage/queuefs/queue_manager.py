# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
QueueManager: Encapsulates AGFS QueueFS plugin operations.
All queues are managed through NamedQueue.
"""

import asyncio
import time
from typing import Any, Dict, Optional, Set, Union

from openviking.service.task_work_index import TaskWorkIndex
from openviking_cli.utils.logger import get_logger

from .embedding_queue import EmbeddingQueue
from .named_queue import DequeueHandlerBase, EnqueueHookBase, NamedQueue, QueueStatus
from .semantic_queue import SemanticQueue

logger = get_logger(__name__)

DEFAULT_MAX_CONCURRENT_SESSION_COMMIT = 8

# ========== Singleton Pattern ==========
_instance: Optional["QueueManager"] = None


def init_queue_manager(
    agfs: Any,
    timeout: int = 10,
    mount_point: str = "/queue",
    max_concurrent_embedding: int = 10,
    max_concurrent_semantic: int = 32,
    max_concurrent_external_parse: int = 4,
    max_concurrent_add_resource: int = 4,
    max_concurrent_session_commit: int = DEFAULT_MAX_CONCURRENT_SESSION_COMMIT,
) -> "QueueManager":
    """Initialize QueueManager singleton.

    Args:
        agfs: Pre-initialized AGFS client (HTTP or Binding).
        timeout: Request timeout in seconds.
        mount_point: Path where QueueFS is mounted.
        max_concurrent_embedding: Max concurrent embedding tasks.
        max_concurrent_semantic: Max concurrent semantic node work.
        max_concurrent_external_parse: Max concurrent ExternalParse tasks.
        max_concurrent_add_resource: Max concurrent AddResource tasks.
        max_concurrent_session_commit: Max concurrent SessionCommit tasks.
    """
    global _instance
    _instance = QueueManager(
        agfs=agfs,
        timeout=timeout,
        mount_point=mount_point,
        max_concurrent_embedding=max_concurrent_embedding,
        max_concurrent_semantic=max_concurrent_semantic,
        max_concurrent_external_parse=max_concurrent_external_parse,
        max_concurrent_add_resource=max_concurrent_add_resource,
        max_concurrent_session_commit=max_concurrent_session_commit,
    )
    return _instance


def get_queue_manager() -> "QueueManager":
    """Get QueueManager singleton."""
    if _instance is None:
        raise RuntimeError("QueueManager is not initialized. Call init_queue_manager() first.")
    return _instance


class QueueManager:
    """
    QueueManager: Encapsulates AGFS QueueFS plugin operations.
    Integrates NamedQueue to manage multiple named queues.
    """

    # Standard queue names
    EMBEDDING = "Embedding"
    SEMANTIC = "Semantic"
    # Keep the on-disk name stable so pre-upgrade jobs remain recoverable.
    EXTERNAL_PARSE = "ExternalParse"
    ADD_RESOURCE = "AddResource"
    SESSION_COMMIT = "SessionCommit"
    USER_DELETION = "UserDeletion"
    # A deferred archive re-enqueues itself; throttle the next scheduling round.
    _SESSION_COMMIT_POLL_INTERVAL = 1.0

    def __init__(
        self,
        agfs: Any,
        timeout: int = 10,
        mount_point: str = "/queue",
        max_concurrent_embedding: int = 10,
        max_concurrent_semantic: int = 32,
        max_concurrent_external_parse: int = 4,
        max_concurrent_add_resource: int = 4,
        max_concurrent_session_commit: int = DEFAULT_MAX_CONCURRENT_SESSION_COMMIT,
    ):
        """Initialize QueueManager."""
        self._agfs = agfs
        self.timeout = timeout
        self.mount_point = mount_point
        self._max_concurrent_embedding = max_concurrent_embedding
        self._max_concurrent_semantic = max_concurrent_semantic
        self._max_concurrent_external_parse = max_concurrent_external_parse
        self._max_concurrent_add_resource = max_concurrent_add_resource
        self._max_concurrent_session_commit = max_concurrent_session_commit
        self._queues: Dict[str, NamedQueue] = {}
        self._workers: Dict[str, asyncio.Task[None]] = {}
        self._stop_event: Optional[asyncio.Event] = None
        self._poll_interval = 0.2
        self._task_work_index = TaskWorkIndex()

        logger.info(
            f"[QueueManager] Initialized with agfs={type(agfs).__name__}, mount_point={mount_point}"
        )

    async def start(self) -> None:
        """Start QueueManager workers."""
        if self._stop_event is not None:
            return

        self._stop_event = asyncio.Event()
        for queue in list(self._queues.values()):
            self._start_queue_worker(queue)

        logger.info(f"[QueueManager] mount_point={self.mount_point} Started")

    async def prepare_task_tracking(self, tracker: Any) -> None:
        """Rebuild task work from QueueFS before any consumer starts."""
        snapshots = {name: await queue.snapshot() for name, queue in self._queues.items()}
        owners = self._task_work_index.rebuild(snapshots)
        tracker.attach_work_index(self._task_work_index)
        await tracker.restore_work_tasks(owners)

    def setup_standard_queues(self, vector_store: Any) -> None:
        """Set up standard queues without starting their consumers."""
        # Import handlers here to avoid circular dependencies
        from openviking.storage.collection_schemas import TextEmbeddingHandler
        from openviking.storage.queuefs import SemanticProcessor

        # Embedding Queue
        embedding_handler = TextEmbeddingHandler(vector_store)
        self.get_queue(
            self.EMBEDDING,
            dequeue_handler=embedding_handler,
            allow_create=True,
        )
        logger.info("Embedding queue initialized with TextEmbeddingHandler")

        # Semantic Queue
        semantic_processor = SemanticProcessor(max_concurrent_llm=self._max_concurrent_semantic)
        self.get_queue(
            self.SEMANTIC,
            dequeue_handler=semantic_processor,
            allow_create=True,
        )
        logger.info("Semantic queue initialized with SemanticProcessor")

    def _start_queue_worker(self, queue: NamedQueue) -> None:
        """Start one consumer task on the service event loop."""
        if self._stop_event is None or queue.name in self._workers:
            return
        self._workers[queue.name] = asyncio.create_task(
            self._queue_worker_loop(
                queue,
                self._max_concurrent_for_queue(queue.name),
                self._stop_event,
            ),
            name=f"queuefs:{queue.name}",
        )

    def _max_concurrent_for_queue(self, queue_name: str) -> int:
        """Return the worker concurrency limit for a named queue."""
        if queue_name == self.USER_DELETION:
            return 1
        if queue_name == self.EMBEDDING:
            return self._max_concurrent_embedding
        if queue_name == self.EXTERNAL_PARSE:
            return self._max_concurrent_external_parse
        if queue_name == self.ADD_RESOURCE:
            return self._max_concurrent_add_resource
        if queue_name == self.SESSION_COMMIT:
            return self._max_concurrent_session_commit
        return self._max_concurrent_semantic

    async def _queue_worker_loop(
        self,
        queue: NamedQueue,
        max_concurrent: int,
        stop_event: asyncio.Event,
    ) -> None:
        """Consume one durable queue with bounded in-flight work."""
        poll_interval = (
            self._SESSION_COMMIT_POLL_INTERVAL
            if queue.name == self.SESSION_COMMIT
            else self._poll_interval
        )
        active_tasks: Set[asyncio.Task] = set()

        async def process_one(data: Dict[str, Any]) -> None:
            msg_id = data.get("id", "")
            try:
                await queue.process_dequeued(data)
                await queue.ack(msg_id, data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                queue._on_process_error(str(exc), data)
                logger.error(
                    "[QueueManager] Worker error for %s: %s",
                    queue.name,
                    exc,
                )

        while not stop_event.is_set():
            active_tasks = {task for task in active_tasks if not task.done()}
            while queue.has_dequeue_handler() and len(active_tasks) < max_concurrent:
                try:
                    data = await queue.dequeue_raw()
                except Exception as exc:
                    logger.error("[QueueManager] Dequeue failed for %s: %s", queue.name, exc)
                    break
                if data is None:
                    break
                queue._on_dequeue_start()
                task = asyncio.create_task(
                    process_one(data),
                    name=f"queuefs:{queue.name}:{data.get('id', '')}",
                )
                active_tasks.add(task)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass

        if active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*active_tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[QueueManager] Drain timeout for {queue.name}, "
                    f"cancelling {len(active_tasks)} in-flight task(s)"
                )
                for task in active_tasks:
                    task.cancel()
                await asyncio.gather(*active_tasks, return_exceptions=True)

    async def stop(self) -> None:
        """Stop QueueManager and release resources."""
        global _instance
        if self._stop_event is None:
            return

        self._stop_event.set()
        await asyncio.gather(*self._workers.values())
        self._workers.clear()

        self._agfs = None
        self._queues.clear()
        self._stop_event = None

        if _instance is self:
            _instance = None

        logger.info("[QueueManager] Stopped")

    def is_running(self) -> bool:
        """Check if QueueManager is running."""
        return self._stop_event is not None

    def get_queue(
        self,
        name: str,
        enqueue_hook: Optional[EnqueueHookBase] = None,
        dequeue_handler: Optional[DequeueHandlerBase] = None,
        allow_create: bool = False,
    ) -> NamedQueue:
        """Get or create a named queue object."""
        if name not in self._queues:
            if not allow_create:
                raise RuntimeError(f"Queue {name} does not exist and allow_create is False")
            if name == self.EMBEDDING:
                self._queues[name] = EmbeddingQueue(
                    self._agfs,
                    self.mount_point,
                    name,
                    enqueue_hook=enqueue_hook,
                    dequeue_handler=dequeue_handler,
                    task_work_index=self._task_work_index,
                )
            elif name == self.SEMANTIC:
                self._queues[name] = SemanticQueue(
                    self._agfs,
                    self.mount_point,
                    name,
                    enqueue_hook=enqueue_hook,
                    dequeue_handler=dequeue_handler,
                    task_work_index=self._task_work_index,
                )
            else:
                self._queues[name] = NamedQueue(
                    self._agfs,
                    self.mount_point,
                    name,
                    enqueue_hook=enqueue_hook,
                    dequeue_handler=dequeue_handler,
                    task_work_index=self._task_work_index,
                )
            if self._stop_event is not None:
                self._start_queue_worker(self._queues[name])
        else:
            if dequeue_handler is not None:
                self._queues[name].set_dequeue_handler(dequeue_handler)
            if self._stop_event is not None:
                self._start_queue_worker(self._queues[name])
        return self._queues[name]

    # ========== Compatibility convenience methods ==========

    async def enqueue(self, queue_name: str, data: Union[str, Dict[str, Any]]) -> str:
        """Send message to queue (enqueue)."""
        return await self.get_queue(queue_name).enqueue(data)

    async def dequeue(self, queue_name: str) -> Optional[Dict[str, Any]]:
        """Get message from specified queue."""
        return await self.get_queue(queue_name).dequeue()

    async def peek(self, queue_name: str) -> Optional[Dict[str, Any]]:
        """Peek at the head message of specified queue."""
        return await self.get_queue(queue_name).peek()

    async def size(self, queue_name: str) -> int:
        """Get the size of specified queue."""
        return await self.get_queue(queue_name).size()

    async def clear(self, queue_name: str) -> bool:
        """Clear specified queue."""
        return await self.get_queue(queue_name).clear()

    # ========== Status check interface ==========

    async def check_status(self, queue_name: Optional[str] = None) -> Dict[str, QueueStatus]:
        """Check queue status."""
        if queue_name:
            if queue_name not in self._queues:
                return {}
            return {queue_name: await self._queues[queue_name].get_status()}
        return {name: await q.get_status() for name, q in self._queues.items()}

    def has_errors(self, queue_name: Optional[str] = None) -> bool:
        """Check if there are errors."""
        if queue_name:
            if queue_name not in self._queues:
                return False
            return self._queues[queue_name].has_errors()
        return any(queue.has_errors() for queue in self._queues.values())

    async def is_all_complete(self, queue_name: Optional[str] = None) -> bool:
        """Check if all processing is complete."""
        statuses = await self.check_status(queue_name)
        return all(s.is_complete for s in statuses.values())

    async def wait_complete(
        self,
        queue_name: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: float = 0.5,
    ) -> Dict[str, QueueStatus]:
        """Wait for completion and return final status."""
        start = time.time()
        while True:
            if await self.is_all_complete(queue_name):
                return await self.check_status(queue_name)
            if timeout and (time.time() - start) > timeout:
                raise TimeoutError(f"Queue processing not complete after {timeout}s")
            await asyncio.sleep(poll_interval)
