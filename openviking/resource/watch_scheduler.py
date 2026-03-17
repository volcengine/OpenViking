# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource watch scheduler.

Provides scheduled task execution for watch tasks.
"""

import asyncio
from typing import Any, Optional, Set

from openviking.resource.watch_manager import WatchManager
from openviking.server.identity import RequestContext, Role, UserIdentifier
from openviking.service.resource_service import ResourceService
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class WatchScheduler:
    """Scheduled task scheduler for resource watch tasks.

    Periodically checks for due tasks and executes them by calling ResourceService.
    Implements concurrency control to skip tasks that are already executing.
    Handles execution failures gracefully without affecting next scheduling.
    Manages the lifecycle of WatchManager internally.
    """

    DEFAULT_CHECK_INTERVAL = 60.0

    def __init__(
        self,
        resource_service: ResourceService,
        viking_fs: Optional[Any] = None,
        check_interval: float = DEFAULT_CHECK_INTERVAL,
    ):
        """Initialize WatchScheduler.

        Args:
            resource_service: ResourceService instance for executing tasks
            viking_fs: VikingFS instance for WatchManager persistence (optional)
            check_interval: Interval in seconds between scheduler checks (default: 60)
        """
        self._resource_service = resource_service
        self._viking_fs = viking_fs
        self._check_interval = check_interval

        self._watch_manager: Optional[WatchManager] = None
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._executing_tasks: Set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def watch_manager(self) -> Optional[WatchManager]:
        """Get the WatchManager instance."""
        return self._watch_manager

    async def start(self) -> None:
        """Start the scheduler.

        Creates a background task that periodically checks for due tasks.
        Initializes the WatchManager and loads persisted tasks.
        """
        if self._running:
            logger.warning("[WatchScheduler] Scheduler is already running")
            return

        # Initialize WatchManager
        self._watch_manager = WatchManager(viking_fs=self._viking_fs)
        await self._watch_manager.initialize()
        logger.info("[WatchScheduler] WatchManager initialized")

        self._running = True
        self._scheduler_task = asyncio.create_task(self._run_scheduler())
        logger.info(f"[WatchScheduler] Started with check interval {self._check_interval}s")

    async def stop(self) -> None:
        """Stop the scheduler.

        Cancels the background task and waits for it to complete.
        Cleans up the WatchManager.
        """
        if not self._running:
            logger.warning("[WatchScheduler] Scheduler is not running")
            return

        self._running = False

        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        # Clean up WatchManager
        if self._watch_manager:
            self._watch_manager = None
            logger.info("[WatchScheduler] WatchManager cleaned up")

        logger.info("[WatchScheduler] Stopped")

    async def schedule_task(self, task_id: str) -> bool:
        """Schedule a single task for immediate execution.

        Args:
            task_id: ID of the task to schedule

        Returns:
            True if task was scheduled, False if task is already executing or not found
        """
        task = await self._watch_manager.get_task(task_id)
        if not task:
            logger.warning(f"[WatchScheduler] Task {task_id} not found")
            return False

        async with self._lock:
            if task_id in self._executing_tasks:
                logger.info(f"[WatchScheduler] Task {task_id} is already executing, skipping")
                return False

            self._executing_tasks.add(task_id)

        try:
            await self._execute_task(task)
            return True
        finally:
            async with self._lock:
                self._executing_tasks.discard(task_id)

    async def _run_scheduler(self) -> None:
        """Background task loop that periodically checks and executes due tasks.

        This method runs continuously until the scheduler is stopped.
        """
        logger.info("[WatchScheduler] Scheduler loop started")

        while self._running:
            try:
                await self._check_and_execute_due_tasks()
            except Exception as e:
                logger.error(f"[WatchScheduler] Error in scheduler loop: {e}", exc_info=True)

            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break

        logger.info("[WatchScheduler] Scheduler loop ended")

    async def _check_and_execute_due_tasks(self) -> None:
        """Check for due tasks and execute them.

        This method is called periodically by the scheduler loop.
        """
        due_tasks = await self._watch_manager.get_due_tasks()

        if not due_tasks:
            return

        logger.info(f"[WatchScheduler] Found {len(due_tasks)} due tasks")

        for task in due_tasks:
            async with self._lock:
                if task.task_id in self._executing_tasks:
                    logger.info(
                        f"[WatchScheduler] Task {task.task_id} is already executing, skipping"
                    )
                    continue

                self._executing_tasks.add(task.task_id)

            try:
                await self._execute_task(task)
            finally:
                async with self._lock:
                    self._executing_tasks.discard(task.task_id)

    async def _execute_task(self, task) -> None:
        """Execute a single watch task.

        Calls ResourceService.add_resource to process the resource.
        Handles errors gracefully and updates execution time regardless of success/failure.
        Deactivates tasks when resources no longer exist.

        Args:
            task: WatchTask to execute
        """
        logger.info(f"[WatchScheduler] Executing task {task.task_id} for path {task.path}")

        should_deactivate = False
        deactivation_reason = ""

        try:
            if not self._check_resource_exists(task.path):
                should_deactivate = True
                deactivation_reason = f"Resource path does not exist: {task.path}"
                logger.warning(
                    f"[WatchScheduler] Task {task.task_id}: {deactivation_reason}. "
                    "Deactivating task."
                )
            else:
                from openviking_cli.session.user_id import UserIdentifier
                
                user = UserIdentifier(
                    account_id=task.account_id,
                    user_id=task.user_id,
                    agent_id=task.agent_id,
                )
                ctx = RequestContext(
                    user=user,
                    role=Role.ROOT,
                )

                result = await self._resource_service.add_resource(
                    path=task.path,
                    ctx=ctx,
                    to=task.to_uri,
                    parent=task.parent_uri,
                    reason=task.reason,
                    instruction=task.instruction,
                    watch_interval=task.watch_interval,
                    skip_watch_management=True,
                )

                logger.info(
                    f"[WatchScheduler] Task {task.task_id} executed successfully, "
                    f"result: {result.get('root_uri', 'N/A')}"
                )

        except FileNotFoundError as e:
            should_deactivate = True
            deactivation_reason = f"Resource not found: {e}"
            logger.error(
                f"[WatchScheduler] Task {task.task_id} resource not found: {e}. "
                "Deactivating task."
            )
        except Exception as e:
            logger.error(
                f"[WatchScheduler] Task {task.task_id} execution failed: {e}",
                exc_info=True,
            )

        finally:
            try:
                if should_deactivate:
                    await self._watch_manager.update_task(
                        task_id=task.task_id,
                        account_id=task.account_id,
                        user_id=task.user_id,
                        role="ROOT",
                        is_active=False
                    )
                    logger.info(
                        f"[WatchScheduler] Deactivated task {task.task_id}: {deactivation_reason}"
                    )
                else:
                    await self._watch_manager.update_execution_time(task.task_id)
                    logger.info(
                        f"[WatchScheduler] Updated execution time for task {task.task_id}"
                    )
            except Exception as e:
                logger.error(
                    f"[WatchScheduler] Failed to update task {task.task_id}: {e}",
                    exc_info=True,
                )

    def _check_resource_exists(self, path: str) -> bool:
        """Check if a resource path exists.

        Args:
            path: Resource path to check

        Returns:
            True if resource exists or is a URL, False otherwise
        """
        if path.startswith("http://") or path.startswith("https://") or path.startswith("git@"):
            return True

        from pathlib import Path
        try:
            return Path(path).exists()
        except Exception as e:
            logger.warning(f"[WatchScheduler] Failed to check path existence {path}: {e}")
            return False

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._running

    @property
    def executing_tasks(self) -> Set[str]:
        """Get the set of currently executing task IDs."""
        return self._executing_tasks.copy()
