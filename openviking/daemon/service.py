"""
OpenViking Active Daemon main service.
Orchestrates file watching, ETL processing, and knowledge storage.
"""
import asyncio
import os
from pathlib import Path
from typing import Optional

from openviking.daemon.cursor_manager import CursorManager
from openviking.daemon.watchers.claude_code_watcher import ClaudeCodeWatcher
from openviking.daemon.etl_pipeline import BatchETLPipeline
from openviking.daemon.storage_adapter import VikingStorageAdapter
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class DaemonService:
    """
    OpenViking Active Daemon main service.
    Monitors Claude Code JSONL files and extracts knowledge into viking:// storage.
    """

    def __init__(
        self,
        resource_service,
        watch_dir: Optional[str] = None,
        db_path: Optional[str] = None,
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
    ):
        self.resource_service = resource_service

        # Default paths
        home = Path.home()
        self.watch_dir = watch_dir or str(home / ".claude" / "projects")
        self.db_path = db_path or str(
            home / ".qoderworkcn" / "openviking" / "daemon_cursors.db"
        )

        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds

        # Components (initialized in start())
        self.cursor_manager: Optional[CursorManager] = None
        self.watcher: Optional[ClaudeCodeWatcher] = None
        self.etl_pipeline: Optional[BatchETLPipeline] = None
        self.storage_adapter: Optional[VikingStorageAdapter] = None

        self._running = False
        self._etl_task: Optional[asyncio.Task] = None
        self._batch_queue: asyncio.Queue = asyncio.Queue()

    async def start(self):
        """Start the Daemon service."""
        logger.info("Starting OpenViking Active Daemon...")

        # Initialize components
        self.cursor_manager = CursorManager(self.db_path)
        self.etl_pipeline = BatchETLPipeline()
        self.storage_adapter = VikingStorageAdapter(self.resource_service)

        # Ensure watch directory exists
        Path(self.watch_dir).mkdir(parents=True, exist_ok=True)

        # Start the ETL processing loop as a background task
        self._etl_task = asyncio.create_task(self._etl_loop())

        # Start the file watcher
        # The watcher runs in a separate thread (watchdog), so we pass
        # a sync callback that puts events onto the async queue
        self.watcher = ClaudeCodeWatcher(
            watch_dir=self.watch_dir,
            cursor_manager=self.cursor_manager,
            batch_callback=self._enqueue_batch,
            batch_trigger_lines=self.batch_trigger_lines,
            batch_trigger_seconds=self.batch_trigger_seconds,
        )
        self.watcher.start()

        self._running = True
        logger.info("Daemon started, watching: %s", self.watch_dir)

    async def stop(self):
        """Stop the Daemon service."""
        logger.info("Stopping OpenViking Active Daemon...")

        self._running = False

        if self.watcher:
            self.watcher.stop()

        # Signal the ETL loop to stop
        if self._etl_task:
            await self._batch_queue.put(None)  # sentinel
            try:
                await asyncio.wait_for(self._etl_task, timeout=10)
            except asyncio.TimeoutError:
                self._etl_task.cancel()

        logger.info("Daemon stopped")

    def _enqueue_batch(self, events):
        """Sync callback from watcher thread - puts events onto async queue."""
        try:
            self._batch_queue.put_nowait(events)
        except Exception as e:
            logger.error("Failed to enqueue batch: %s", e)

    async def _etl_loop(self):
        """Background loop that processes batches from the queue."""
        logger.info("ETL processing loop started")

        while self._running:
            try:
                events = await asyncio.wait_for(
                    self._batch_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue

            if events is None:
                break  # shutdown sentinel

            try:
                extracted = await self.etl_pipeline.process_batch(events)
                if not extracted:
                    logger.info("No knowledge extracted from batch")
                    continue

                # Write to OpenViking
                for knowledge in extracted:
                    try:
                        from openviking.server.identity import RequestContext
                        ctx = RequestContext(user_id="daemon", session_id="daemon-session")
                        success = await self.storage_adapter.write_knowledge(
                            knowledge, ctx
                        )
                        if success:
                            logger.info("Successfully wrote: %s", knowledge.title)
                        else:
                            logger.warning("Failed to write: %s", knowledge.title)
                    except Exception as e:
                        logger.error("Error writing knowledge: %s", e)

            except Exception as e:
                logger.error("Error in ETL processing: %s", e, exc_info=True)

        logger.info("ETL processing loop stopped")

    async def flush(self):
        """Force flush any buffered events."""
        if self.watcher:
            self.watcher.flush()
            logger.info("Manual flush triggered")

    @property
    def is_running(self) -> bool:
        return self._running
