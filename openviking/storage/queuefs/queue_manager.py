# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
QueueManager: Encapsulates AGFS QueueFS plugin operations.
All queues are managed through NamedQueue.
"""

import asyncio
import atexit
import threading
import time
import traceback
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence, Set, Union

from openviking.service.task_work_index import TaskWorkIndex
from openviking_cli.utils.logger import get_logger

from .embedding_queue import EmbeddingQueue
from .named_queue import DequeueHandlerBase, NamedQueue, QueueStatus
from .queue_middleware import QueueMiddleware
from .semantic_queue import SemanticQueue

if TYPE_CHECKING:
    from openviking.config.vlm import VLMResolver

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
    max_concurrent_external_task: int = 10,
    *,
    middlewares: Sequence[QueueMiddleware] = (),
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
        middlewares: Additional middleware, fixed at construction for all queues.
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
        max_concurrent_external_task=max_concurrent_external_task,
        middlewares=middlewares,
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
    EXTERNAL_TASK = "ExternalTask"
    # Account and user cleanup share one consumer. Retain the persisted name
    # so user cleanup messages queued before this change resume in place.
    DATA_CLEANUP = "UserDeletion"
    # Deferred work re-enqueues itself; throttle the next scheduling round.
    _REQUEUE_POLL_INTERVAL = 1.0

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
        max_concurrent_external_task: int = 10,
        *,
        middlewares: Sequence[QueueMiddleware] = (),
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
        self._max_concurrent_external_task = max_concurrent_external_task
        self._queues: Dict[str, NamedQueue] = {}
        self._started = False
        self._queue_threads: Dict[str, threading.Thread] = {}
        self._queue_stop_events: Dict[str, threading.Event] = {}
        self._embedding_worker_stopped = threading.Event()
        self._poll_interval = 0.2
        self._task_work_index = TaskWorkIndex()
        self._vlm_resolver: Optional["VLMResolver"] = None
        # Import at composition time to avoid a service <-> queue package cycle.
        from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware

        self._middlewares: tuple[QueueMiddleware, ...] = (
            TaskWorkQueueMiddleware(self._task_work_index),
            *middlewares,
        )

        atexit.register(self.stop)
        logger.info(
            f"[QueueManager] Initialized with agfs={type(agfs).__name__}, mount_point={mount_point}"
        )

    def start(self) -> None:
        """Start QueueManager workers."""
        if self._started:
            return
        if self.SEMANTIC in self._queues and self._vlm_resolver is None:
            raise RuntimeError(
                "QueueManager requires a VLM resolver before semantic workers start"
            )

        self._started = True

        # Start queue workers for existing queues
        for queue in list(self._queues.values()):
            self._start_queue_worker(queue)

        logger.info(f"[QueueManager] mount_point={self.mount_point} Started")

    async def prepare_task_tracking(self, tracker: Any) -> list[Any]:
        """Rebuild task work from QueueFS before any consumer starts."""
        snapshots = {name: await queue.snapshot() for name, queue in self._queues.items()}
        owners = self._task_work_index.rebuild(snapshots)
        tracker.attach_work_index(self._task_work_index)
        return await tracker.restore_work_tasks(owners)

    def setup_standard_queues(
        self,
        vector_store: Any,
        start: bool = True,
        *,
        embedding_provider: Any = None,
    ) -> None:
        """
        Setup standard queues (Embedding and Semantic) with their handlers.

        Args:
            vector_store: Vector store instance for handlers to write results.
            start: Whether to start worker threads immediately (default True).
                   Pass False when the consumer depends on resources that are not
                   yet initialized.
            embedding_provider: Account-aware embedding provider for the handler.
        """
        # Import handlers here to avoid circular dependencies
        from openviking.storage.collection_schemas import TextEmbeddingHandler
        from openviking.storage.queuefs import SemanticProcessor

        # Embedding Queue
        embedding_handler = TextEmbeddingHandler(vector_store, embedding_provider)
        self.get_queue(
            self.EMBEDDING,
            dequeue_handler=embedding_handler,
            allow_create=True,
        )
        logger.info("Embedding queue initialized with TextEmbeddingHandler")

        # Semantic Queue
        semantic_processor = SemanticProcessor(
            max_concurrent_llm=self._max_concurrent_semantic,
            embedding_worker_stopped=self._embedding_worker_stopped.is_set,
            vlm_resolver=self._vlm_resolver,
        )
        self.get_queue(
            self.SEMANTIC,
            dequeue_handler=semantic_processor,
            allow_create=True,
        )
        logger.info("Semantic queue initialized with SemanticProcessor")

        if start:
            self.start()

    def set_vlm_resolver(self, resolver: "VLMResolver") -> None:
        """Bind the owning service's resolver before queue workers start."""
        from openviking.storage.queuefs import SemanticProcessor

        if self._started:
            raise RuntimeError("Cannot replace the VLM resolver after queue workers start")
        self._vlm_resolver = resolver
        queue = self._queues.get(self.SEMANTIC)
        if queue is not None:
            queue.set_dequeue_handler(
                SemanticProcessor(
                    max_concurrent_llm=self._max_concurrent_semantic,
                    embedding_worker_stopped=self._embedding_worker_stopped.is_set,
                    vlm_resolver=resolver,
                )
            )

    def _start_queue_worker(self, queue: NamedQueue) -> None:
        """Start a dedicated worker thread for a queue if not already running."""
        if queue.name in self._queue_threads:
            thread = self._queue_threads[queue.name]
            if thread.is_alive():
                return

        max_concurrent = self._max_concurrent_for_queue(queue.name)
        stop_event = threading.Event()
        self._queue_stop_events[queue.name] = stop_event
        if queue.name == self.EMBEDDING:
            self._embedding_worker_stopped.clear()
        thread = threading.Thread(
            target=self._queue_worker_loop,
            args=(queue, stop_event, max_concurrent),
            daemon=True,
        )
        self._queue_threads[queue.name] = thread
        thread.start()

    def _max_concurrent_for_queue(self, queue_name: str) -> int:
        """Return the worker concurrency limit for a named queue."""
        if queue_name == self.DATA_CLEANUP:
            return 1
        if queue_name == self.EMBEDDING:
            return self._max_concurrent_embedding
        if queue_name == self.EXTERNAL_PARSE:
            return self._max_concurrent_external_parse
        if queue_name == self.ADD_RESOURCE:
            return self._max_concurrent_add_resource
        if queue_name == self.SESSION_COMMIT:
            return self._max_concurrent_session_commit
        if queue_name == self.EXTERNAL_TASK:
            return self._max_concurrent_external_task
        return self._max_concurrent_semantic

    def _queue_worker_loop(
        self, queue: NamedQueue, stop_event: threading.Event, max_concurrent: int = 1
    ) -> None:
        """Worker loop for a single queue.

        When max_concurrent > 1, items are fetched and processed in parallel
        (up to max_concurrent at a time). Otherwise items are processed one by one.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        poll_interval = (
            self._REQUEUE_POLL_INTERVAL
            if queue.name in {self.SESSION_COMMIT, self.EXTERNAL_TASK}
            else self._poll_interval
        )
        try:
            if max_concurrent > 1:
                loop.run_until_complete(
                    self._worker_async_concurrent(queue, stop_event, max_concurrent)
                )
            else:
                while not stop_event.is_set():
                    try:
                        queue_size = loop.run_until_complete(queue.size())
                        if queue.has_dequeue_handler() and queue_size > 0:
                            data = loop.run_until_complete(queue.dequeue())
                            if data is not None:
                                logger.debug("[QueueManager] Dequeued message from %s", queue.name)
                            if queue.name in {self.SESSION_COMMIT, self.EXTERNAL_TASK}:
                                stop_event.wait(poll_interval)
                        else:
                            stop_event.wait(poll_interval)
                    except asyncio.CancelledError:
                        if not stop_event.is_set():
                            raise
                        break
                    except Exception as e:
                        logger.error(f"[QueueManager] Worker error for {queue.name}: {e}")
                        traceback.print_exc()
                        stop_event.wait(poll_interval)
        finally:
            # Consumers may own timers and async generators in addition to
            # their active queue deliveries. Finish them on the worker loop.
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            if queue.name == self.EMBEDDING:
                # No more deliveries can start and active handlers have exited,
                # including protected writes. Pending messages remain durable.
                self._embedding_worker_stopped.set()

    async def _worker_async_concurrent(
        self, queue: NamedQueue, stop_event: threading.Event, max_concurrent: int
    ) -> None:
        """Concurrent worker: drains the queue and processes items in parallel.

        A Semaphore caps inflight tasks at max_concurrent.
        """
        poll_interval = (
            self._REQUEUE_POLL_INTERVAL
            if queue.name in {self.SESSION_COMMIT, self.EXTERNAL_TASK}
            else self._poll_interval
        )
        sem = asyncio.Semaphore(max_concurrent)
        active_tasks: Set[asyncio.Task] = set()

        async def process_one(data: Dict[str, Any]) -> None:
            async with sem:
                msg_id = data.get("id", "") if isinstance(data, dict) else ""
                try:
                    await queue.process_dequeued(data)
                    # Ack after successful processing (delete from persistent storage).
                    await queue.ack(msg_id, data)
                except Exception as e:
                    # The message remains in processing and will be recovered.
                    logger.error(f"[QueueManager] Concurrent worker error for {queue.name}: {e}")

        while not stop_event.is_set():
            # Prune completed tasks
            active_tasks = {t for t in active_tasks if not t.done()}

            # While capacity remains, keep draining the queue
            while len(active_tasks) < max_concurrent:
                if not queue.has_dequeue_handler():
                    break
                data = await queue.dequeue_raw()
                if data is None:
                    break
                task = asyncio.create_task(process_one(data))
                active_tasks.add(task)
                logger.debug(
                    f"[QueueManager] Dispatched concurrent task for {queue.name} "
                    f"(active={len(active_tasks)})"
                )

            await asyncio.sleep(poll_interval)

        # Drain remaining in-flight tasks on shutdown (with timeout)
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
                for t in active_tasks:
                    t.cancel()
                await asyncio.gather(*active_tasks, return_exceptions=True)

    def stop(self) -> None:
        """Stop QueueManager and release resources."""
        global _instance
        if not self._started:
            return

        # Stop queue workers
        for stop_event in self._queue_stop_events.values():
            stop_event.set()
        if self.EMBEDDING not in self._queue_threads:
            self._embedding_worker_stopped.set()
        for name, thread in self._queue_threads.items():
            thread.join(timeout=10.0)
            if thread.is_alive():
                logger.warning(f"[QueueManager] Worker thread {name} did not exit in time")
        self._queue_threads.clear()
        self._queue_stop_events.clear()

        self._agfs = None
        self._queues.clear()
        self._started = False

        if _instance is self:
            _instance = None

        logger.info("[QueueManager] Stopped")

    def is_running(self) -> bool:
        """Check if QueueManager is running."""
        return self._started

    def get_queue(
        self,
        name: str,
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
                    dequeue_handler=dequeue_handler,
                    middlewares=self._middlewares,
                )
            elif name == self.SEMANTIC:
                self._queues[name] = SemanticQueue(
                    self._agfs,
                    self.mount_point,
                    name,
                    dequeue_handler=dequeue_handler,
                    middlewares=self._middlewares,
                )
            else:
                self._queues[name] = NamedQueue(
                    self._agfs,
                    self.mount_point,
                    name,
                    dequeue_handler=dequeue_handler,
                    middlewares=self._middlewares,
                )
            if self._started:
                self._start_queue_worker(self._queues[name])
        else:
            if dequeue_handler is not None:
                self._queues[name].set_dequeue_handler(dequeue_handler)
            if self._started:
                # Ensure existing queue has a worker running
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
            return self._queues[queue_name]._error_count > 0
        return any(q._error_count > 0 for q in self._queues.values())

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
