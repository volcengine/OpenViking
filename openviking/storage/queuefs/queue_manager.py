# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
QueueManager: Encapsulates AGFS QueueFS plugin operations.
All queues are managed through NamedQueue.
"""

import asyncio
import atexit
import threading
import time
from typing import Any, Dict, Optional, Union

from pyagfs import AGFSClient

from openviking_cli.utils.logger import get_logger

from .embedding_queue import EmbeddingQueue
from .named_queue import DequeueHandlerBase, EnqueueHookBase, NamedQueue, QueueStatus
from .semantic_queue import SemanticQueue

logger = get_logger(__name__)

# ========== Singleton Pattern ==========
_instance: Optional["QueueManager"] = None


def init_queue_manager(
    agfs_url: str = "http://localhost:8080",
    timeout: int = 10,
    mount_point: str = "/queue",
) -> "QueueManager":
    """Initialize QueueManager singleton."""
    global _instance
    _instance = QueueManager(
        agfs_url=agfs_url,
        timeout=timeout,
        mount_point=mount_point,
    )
    return _instance


def get_queue_manager() -> "QueueManager":
    """Get QueueManager singleton."""
    if _instance is None:
        # If not initialized, try to initialize with default configuration
        return init_queue_manager()
    return _instance


class QueueManager:
    """
    QueueManager: Encapsulates AGFS QueueFS plugin operations.
    Integrates NamedQueue to manage multiple named queues.
    """

    # Standard queue names
    EMBEDDING = "Embedding"
    SEMANTIC = "Semantic"

    def __init__(
        self,
        agfs_url: str = "http://localhost:8080",
        timeout: int = 10,
        mount_point: str = "/queue",
    ):
        """Initialize QueueManager."""
        self._agfs_url = agfs_url
        self.timeout = timeout
        self.mount_point = mount_point
        self._agfs: Optional[Any] = None
        self._queues: Dict[str, NamedQueue] = {}
        self._started = False
        self._queue_threads: Dict[str, threading.Thread] = {}
        self._queue_stop_events: Dict[str, threading.Event] = {}
        self._poll_interval = 0.2

        atexit.register(self.stop)
        logger.info(
            f"[QueueManager] Initialized with agfs_url={agfs_url}, mount_point={mount_point}"
        )

    def start(self) -> None:
        """Start QueueManager, establish connection and ensure queuefs is mounted."""
        if self._started:
            return

        self._agfs = AGFSClient(api_base_url=self._agfs_url, timeout=self.timeout)
        self._started = True

        # Start queue workers for existing queues
        for queue in list(self._queues.values()):
            self._start_queue_worker(queue)

        logger.info("[QueueManager] Started")

    def setup_standard_queues(self, vikingdb_interface: Any) -> None:
        """
        Setup standard queues (Embedding and Semantic) with their handlers.

        This method initializes the EmbeddingQueue with TextEmbeddingHandler
        and the SemanticQueue with SemanticProcessor, then ensures the
        queue manager is started.

        Args:
            vikingdb_interface: VikingDBInterface instance for handlers to write results.
        """
        # Import handlers here to avoid circular dependencies
        from openviking.storage.collection_schemas import TextEmbeddingHandler
        from openviking.storage.queuefs import SemanticProcessor

        # Embedding Queue
        embedding_handler = TextEmbeddingHandler(vikingdb_interface)
        self.get_queue(
            self.EMBEDDING,
            dequeue_handler=embedding_handler,
            allow_create=True,
        )
        logger.info("Embedding queue initialized with TextEmbeddingHandler")

        # Semantic Queue
        semantic_processor = SemanticProcessor()
        self.get_queue(
            self.SEMANTIC,
            dequeue_handler=semantic_processor,
            allow_create=True,
        )
        logger.info("Semantic queue initialized with SemanticProcessor")

        # Start QueueManager processing
        self.start()

    def _start_queue_worker(self, queue: NamedQueue) -> None:
        """Start a dedicated worker thread for a queue if not already running."""
        if queue.name in self._queue_threads:
            thread = self._queue_threads[queue.name]
            if thread.is_alive():
                return

        stop_event = threading.Event()
        self._queue_stop_events[queue.name] = stop_event
        thread = threading.Thread(
            target=self._queue_worker_loop,
            args=(queue, stop_event),
            daemon=True,
        )
        self._queue_threads[queue.name] = thread
        thread.start()

    def _queue_worker_loop(self, queue: NamedQueue, stop_event: threading.Event) -> None:
        """Worker loop for a single queue, processes items sequentially."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not stop_event.is_set():
                try:
                    queue_size = loop.run_until_complete(queue.size())
                    if queue.has_dequeue_handler() and queue_size > 0:
                        data = loop.run_until_complete(queue.dequeue())
                        if data is not None:
                            logger.debug(
                                f"[QueueManager] Dequeued message from {queue.name}: {data}"
                            )
                    else:
                        stop_event.wait(self._poll_interval)
                except Exception as e:
                    logger.error(f"[QueueManager] Worker error for {queue.name}: {e}")
                    import traceback

                    traceback.print_exc()
                    stop_event.wait(self._poll_interval)
        finally:
            loop.close()

    def stop(self) -> None:
        """Stop QueueManager and release resources."""
        global _instance
        if not self._started:
            return

        # Stop queue workers
        for stop_event in self._queue_stop_events.values():
            stop_event.set()
        for thread in self._queue_threads.values():
            thread.join()
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
        enqueue_hook: Optional[EnqueueHookBase] = None,
        dequeue_handler: Optional[DequeueHandlerBase] = None,
        allow_create: bool = False,
    ) -> NamedQueue:
        """Get or create a named queue object."""
        if not self._started:
            self.start()

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
                )
            elif name == self.SEMANTIC:
                self._queues[name] = SemanticQueue(
                    self._agfs,
                    self.mount_point,
                    name,
                    enqueue_hook=enqueue_hook,
                    dequeue_handler=dequeue_handler,
                )
            else:
                self._queues[name] = NamedQueue(
                    self._agfs,
                    self.mount_point,
                    name,
                    enqueue_hook=enqueue_hook,
                    dequeue_handler=dequeue_handler,
                )
            if self._started:
                self._start_queue_worker(self._queues[name])
        elif self._started:
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
