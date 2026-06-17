"""
OpenViking Active Daemon main service.
Orchestrates multi-tool file watching, ETL processing, and knowledge storage.
"""
import asyncio
import os
from pathlib import Path
from typing import List, Optional

from openviking.daemon.cursor_manager import CursorManager
from openviking.daemon.etl_pipeline import BatchETLPipeline
from openviking.daemon.storage_adapter import VikingStorageAdapter
from openviking.daemon.watchers.registry import create_watcher
from openviking.daemon.watchers import BaseWatcher
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class DaemonService:
    """
    OpenViking Active Daemon main service.
    Monitors multiple AI tool logs and extracts knowledge into viking:// storage.
    """

    def __init__(
        self,
        resource_service,
        watcher_configs: Optional[List] = None,
        db_path: Optional[str] = None,
        # Backward-compatible single watcher args
        watch_dir: Optional[str] = None,
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
    ):
        self.resource_service = resource_service

        home = Path.home()
        self.db_path = db_path or str(
            home / ".qoderworkcn" / "openviking" / "daemon_cursors.db"
        )

        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds

        # Build watcher config list
        if watcher_configs:
            self._watcher_configs = watcher_configs
        else:
            # Backward compatible: single claude_code watcher
            from openviking.server.config import WatcherConfig
            wd = watch_dir or str(home / ".claude" / "projects")
            self._watcher_configs = [WatcherConfig(
                tool_name="claude_code",
                watch_dir=wd,
                batch_trigger_lines=batch_trigger_lines,
                batch_trigger_seconds=batch_trigger_seconds,
            )]

        # Components
        self.cursor_manager: Optional[CursorManager] = None
        self.watchers: List[BaseWatcher] = []
        self.etl_pipeline: Optional[BatchETLPipeline] = None
        self.storage_adapter: Optional[VikingStorageAdapter] = None

        self._running = False
        self._etl_task: Optional[asyncio.Task] = None
        self._batch_queue: asyncio.Queue = asyncio.Queue()

    async def start(self):
        """Start the Daemon service with all configured watchers."""
        logger.info("Starting OpenViking Active Daemon...")

        self.cursor_manager = CursorManager(self.db_path)
        self.etl_pipeline = BatchETLPipeline()
        self.storage_adapter = VikingStorageAdapter(self.resource_service)

        # Ensure db directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # Start ETL loop
        self._etl_task = asyncio.create_task(self._etl_loop())

        # Create and start each watcher
        for wc in self._watcher_configs:
            watch_dir = os.path.expanduser(wc.watch_dir)
            Path(watch_dir).mkdir(parents=True, exist_ok=True)

            try:
                watcher = create_watcher(
                    tool_name=wc.tool_name,
                    watch_dir=watch_dir,
                    cursor_manager=self.cursor_manager,
                    batch_callback=self._enqueue_batch,
                    file_pattern=wc.file_pattern,
                    batch_trigger_lines=wc.batch_trigger_lines,
                    batch_trigger_seconds=wc.batch_trigger_seconds,
                    extra=wc.extra,
                )
                watcher.start()
                self.watchers.append(watcher)
                logger.info("Watcher started: %s -> %s", wc.tool_name, watch_dir)
            except Exception as e:
                logger.warning("Failed to start watcher %s: %s", wc.tool_name, e)

        self._running = True
        logger.info("Daemon started with %d watcher(s)", len(self.watchers))

    async def stop(self):
        """Stop all watchers and the ETL loop."""
        logger.info("Stopping OpenViking Active Daemon...")

        self._running = False

        for watcher in self.watchers:
            try:
                watcher.stop()
            except Exception as e:
                logger.warning("Error stopping watcher: %s", e)

        if self._etl_task:
            await self._batch_queue.put(None)
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
                break

            try:
                extracted = await self.etl_pipeline.process_batch(events)
                if not extracted:
                    logger.info("No knowledge extracted from batch")
                    continue

                for knowledge in extracted:
                    try:
                        from openviking.server.identity import RequestContext, Role
                        from openviking_cli.session.user_id import UserIdentifier

                        ctx = RequestContext(
                            user=UserIdentifier.the_default_user(),
                            role=Role.ROOT,
                        )
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
        """Force flush all watchers' buffers."""
        for watcher in self.watchers:
            watcher.flush()
        logger.info("Manual flush triggered for %d watchers", len(self.watchers))

    @property
    def is_running(self) -> bool:
        return self._running
