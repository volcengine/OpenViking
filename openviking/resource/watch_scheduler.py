# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource watch scheduler.

Provides scheduled task execution for watch tasks.
"""

import asyncio
from datetime import datetime
from typing import Any, Optional, Set

from openviking.resource.watch_manager import WatchManager
from openviking.server.identity import RequestContext, Role
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
        max_concurrency: int = 4,
    ):
        """Initialize WatchScheduler.

        Args:
            resource_service: ResourceService instance for executing tasks
            viking_fs: VikingFS instance for WatchManager persistence (optional)
            check_interval: Interval in seconds between scheduler checks (default: 60)
        """
        self._resource_service = resource_service
        self._viking_fs = viking_fs
        if check_interval <= 0:
            raise ValueError("check_interval must be > 0")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be > 0")
        self._check_interval = check_interval
        self._max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

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
        if not self._watch_manager:
            logger.warning("[WatchScheduler] WatchManager is not initialized")
            return False

        task = await self._watch_manager.get_task(task_id)
        if not task:
            logger.warning(f"[WatchScheduler] Task {task_id} not found")
            return False

        if not await self._try_mark_executing(task_id):
            logger.info(f"[WatchScheduler] Task {task_id} is already executing, skipping")
            return False

        try:
            async with self._semaphore:
                await self._execute_task(task)
            return True
        finally:
            await asyncio.shield(self._discard_executing(task_id))

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
                sleep_seconds = self._check_interval
                if self._watch_manager:
                    next_time = await self._watch_manager.get_next_execution_time()
                    if next_time is not None:
                        now = datetime.now()
                        sleep_seconds = min(
                            self._check_interval,
                            max(0.0, (next_time - now).total_seconds()),
                        )
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                break

        logger.info("[WatchScheduler] Scheduler loop ended")

    async def _check_and_execute_due_tasks(self) -> None:
        """Check for due tasks and execute them.

        This method is called periodically by the scheduler loop.
        """
        if not self._watch_manager:
            return

        due_tasks = await self._watch_manager.get_due_tasks()

        if not due_tasks:
            return

        logger.info(f"[WatchScheduler] Found {len(due_tasks)} due tasks")

        tasks_to_run = []
        for task in due_tasks:
            if not await self._try_mark_executing(task.task_id):
                logger.info(f"[WatchScheduler] Task {task.task_id} is already executing, skipping")
                continue
            tasks_to_run.append(task)

        async def run_one(t) -> None:
            try:
                async with self._semaphore:
                    await self._execute_task(t)
            finally:
                await asyncio.shield(self._discard_executing(t.task_id))

        if tasks_to_run:
            await asyncio.gather(*(asyncio.create_task(run_one(t)) for t in tasks_to_run))

    async def _execute_task(self, task) -> None:
        """Execute a single watch task.

        Calls ResourceService.add_resource to process the resource.
        Handles errors gracefully and updates execution time regardless of success/failure.
        Deactivates tasks when resources no longer exist.

        Args:
            task: WatchTask to execute
        """
        logger.info(f"[WatchScheduler] Executing task {task.task_id} for path {task.path}")

        cancelled = False
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
                role_value = getattr(task, "original_role", None) or Role.USER.value
                try:
                    role = Role(role_value)
                except Exception:
                    role = Role.USER
                ctx = RequestContext(
                    user=user,
                    role=role,
                )

                processor_kwargs = dict(getattr(task, "processor_kwargs", {}) or {})
                processor_kwargs.pop("build_index", None)
                processor_kwargs.pop("summarize", None)
                result = await self._resource_service.add_resource(
                    path=task.path,
                    ctx=ctx,
                    to=task.to_uri,
                    parent=task.parent_uri,
                    reason=task.reason,
                    instruction=task.instruction,
                    build_index=getattr(task, "build_index", True),
                    summarize=getattr(task, "summarize", False),
                    watch_interval=task.watch_interval,
                    skip_watch_management=True,
                    **processor_kwargs,
                )

                logger.info(
                    f"[WatchScheduler] Task {task.task_id} executed successfully, "
                    f"result: {result.get('root_uri', 'N/A')}"
                )

        except asyncio.CancelledError:
            cancelled = True
            raise
        except FileNotFoundError as e:
            should_deactivate = True
            deactivation_reason = f"Resource not found: {e}"
            logger.error(
                f"[WatchScheduler] Task {task.task_id} resource not found: {e}. Deactivating task."
            )
        except Exception as e:
            logger.error(
                f"[WatchScheduler] Task {task.task_id} execution failed: {e}",
                exc_info=True,
            )

        finally:
            try:
                if not cancelled:
                    if should_deactivate:
                        await asyncio.shield(
                            self._watch_manager.update_task(
                                task_id=task.task_id,
                                account_id=task.account_id,
                                user_id=task.user_id,
                                role=getattr(task, "original_role", None) or Role.USER.value,
                                agent_id=task.agent_id,
                                is_active=False,
                            )
                        )
                        logger.info(
                            f"[WatchScheduler] Deactivated task {task.task_id}: {deactivation_reason}"
                        )
                    else:
                        await asyncio.shield(
                            self._watch_manager.update_execution_time(task.task_id)
                        )
                        logger.info(
                            f"[WatchScheduler] Updated execution time for task {task.task_id}"
                        )
            except Exception as e:
                logger.error(
                    f"[WatchScheduler] Failed to update task {task.task_id}: {e}",
                    exc_info=True,
                )

    async def _try_mark_executing(self, task_id: str) -> bool:
        async with self._lock:
            if task_id in self._executing_tasks:
                return False
            self._executing_tasks.add(task_id)
            return True

    async def _discard_executing(self, task_id: str) -> None:
        async with self._lock:
            self._executing_tasks.discard(task_id)

    def _check_resource_exists(self, path: str) -> bool:
        """Check if a resource path exists.

        Args:
            path: Resource path to check

        Returns:
            True if resource exists or is a URL, False otherwise
        """
        if path.startswith(("http://", "https://", "git@", "ssh://", "git://")):
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
