# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Resource watch scheduler.

Provides scheduled task execution for watch tasks.
"""

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional, Set

from openviking.resource.feishu_watch_auth import (
    FeishuOAuthClient,
    FeishuTokenRefreshError,
    apply_feishu_refreshed_token,
    feishu_auth_state_needs_refresh,
    is_feishu_auth_state,
    load_feishu_app_credentials,
)
from openviking.resource.watch_manager import WatchManager
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_FEISHU_SYNC_FINGERPRINT_KEY = "feishu_sync_fingerprint_v1"
_FEISHU_ACCESS_TOKEN_KEY = "feishu_access_token"


class _StaleWatchTaskError(RuntimeError):
    """Raised when an awaited execution loses its task revision."""


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
        self._feishu_oauth_client: Optional[Any] = None
        self._feishu_accessor: Optional[Any] = None

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
        # WatchManager.update_task() mutates its stored model in place. Freeze
        # every input used by this execution before the first await so a
        # concurrent update cannot make the post-sync fingerprint describe
        # values that add_resource() did not process.
        task = task.model_copy(deep=True)
        logger.info(f"[WatchScheduler] Executing task {task.task_id} for path {task.path}")

        cancelled = False
        should_deactivate = False
        deactivation_reason = ""
        feishu_source_version: Optional[int] = None
        feishu_sync_fingerprint: Optional[str] = None
        feishu_auth_context: Optional[Dict[str, Any]] = None
        require_queue_status = False
        skip_resource_sync = False
        resource_sync_succeeded = False

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
                )
                role_value = getattr(task, "original_role", None) or str(Role.USER)
                try:
                    role = Role(role_value)
                except Exception:
                    role = Role.USER
                ctx = RequestContext(
                    user=user,
                    role=role,
                )

                if task.to_uri:
                    target_exists = await self._check_target_uri_exists(task.to_uri, ctx)
                    if target_exists is False:
                        should_deactivate = True
                        deactivation_reason = f"Watched target URI does not exist: {task.to_uri}"
                        logger.warning(
                            f"[WatchScheduler] Task {task.task_id}: {deactivation_reason}. "
                            "Deactivating task."
                        )

                if not should_deactivate:
                    processor_kwargs = dict(getattr(task, "processor_kwargs", {}) or {})
                    processor_kwargs.pop("build_index", None)
                    processor_kwargs.pop("summarize", None)
                    auth_state = getattr(task, "auth_state", None)
                    if is_feishu_auth_state(auth_state):
                        try:
                            auth_state = await self._prepare_feishu_auth_state(task, auth_state)
                            task.auth_state = auth_state
                            processor_kwargs["feishu_access_token"] = auth_state["access_token"]
                        except FeishuTokenRefreshError as e:
                            if e.permanent:
                                should_deactivate = True
                                deactivation_reason = str(e)
                                logger.error(
                                    f"[WatchScheduler] Task {task.task_id} permanent Feishu "
                                    f"token refresh failure: {e}. Deactivating task."
                                )
                            else:
                                raise

                    if not should_deactivate:
                        feishu_source_version = await self._fetch_feishu_latest_modify_time(
                            task.path,
                            processor_kwargs.get(_FEISHU_ACCESS_TOKEN_KEY),
                        )
                        if feishu_source_version is not None:
                            feishu_auth_context = self._capture_feishu_auth_context(
                                task,
                                processor_kwargs=processor_kwargs,
                            )
                            destination_state = await self._fetch_destination_sync_state(
                                task.to_uri,
                                ctx,
                            )
                            feishu_sync_fingerprint = self._build_feishu_sync_fingerprint(
                                task,
                                processor_kwargs=processor_kwargs,
                                source_version=feishu_source_version,
                                destination_state=destination_state,
                                auth_context=feishu_auth_context,
                            )
                            require_queue_status = feishu_sync_fingerprint is not None
                        previous_fingerprint = (getattr(task, "sync_state", {}) or {}).get(
                            _FEISHU_SYNC_FINGERPRINT_KEY
                        )
                        skip_resource_sync = bool(
                            feishu_sync_fingerprint
                            and previous_fingerprint
                            and feishu_sync_fingerprint == previous_fingerprint
                        )
                        if skip_resource_sync:
                            logger.info(
                                "[WatchScheduler] Task %s source is unchanged; "
                                "skipping full Feishu synchronization",
                                task.task_id,
                            )
                            resource_sync_succeeded = True

                if not should_deactivate and not skip_resource_sync:
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
                        wait=require_queue_status,
                        skip_watch_management=True,
                        **processor_kwargs,
                    )
                    if not self._resource_sync_completed(
                        result,
                        require_queue_status=require_queue_status,
                    ):
                        raise RuntimeError("Resource synchronization did not complete successfully")
                    resource_sync_succeeded = True

                    logger.info(
                        f"[WatchScheduler] Task {task.task_id} executed successfully, "
                        f"result: {result.get('root_uri', 'N/A')}"
                    )

        except asyncio.CancelledError:
            cancelled = True
            raise
        except _StaleWatchTaskError as e:
            logger.info("[WatchScheduler] Task %s became stale: %s", task.task_id, e)
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
                        deactivated = await asyncio.shield(
                            self._watch_manager.deactivate_task_if_revision(
                                task_id=task.task_id,
                                expected_revision=task.revision,
                                account_id=task.account_id,
                                user_id=task.user_id,
                                role=getattr(task, "original_role", None) or str(Role.USER),
                            )
                        )
                        if deactivated:
                            logger.info(
                                f"[WatchScheduler] Deactivated task {task.task_id}: "
                                f"{deactivation_reason}"
                            )
                        else:
                            logger.info(
                                f"[WatchScheduler] Rejected stale deactivation for task "
                                f"{task.task_id}; task remains due"
                            )
                    else:

                        async def finalize_execution_state() -> bool:
                            sync_state_updates = None
                            if (
                                resource_sync_succeeded
                                and not skip_resource_sync
                                and require_queue_status
                            ):
                                destination_state = await self._fetch_destination_sync_state(
                                    task.to_uri,
                                    ctx,
                                )
                                final_fingerprint = self._build_feishu_sync_fingerprint(
                                    task,
                                    processor_kwargs=processor_kwargs,
                                    source_version=feishu_source_version,
                                    destination_state=destination_state,
                                    auth_context=feishu_auth_context,
                                )
                                if final_fingerprint is not None:
                                    sync_state_updates = {
                                        _FEISHU_SYNC_FINGERPRINT_KEY: final_fingerprint
                                    }
                            if sync_state_updates is None:
                                return await self._watch_manager.update_execution_time(
                                    task.task_id,
                                    expected_revision=task.revision,
                                )
                            return await self._watch_manager.update_execution_time(
                                task.task_id,
                                expected_revision=task.revision,
                                sync_state_updates=sync_state_updates,
                            )

                        finalization_task = asyncio.create_task(finalize_execution_state())
                        try:
                            execution_state_committed = await asyncio.shield(finalization_task)
                        except asyncio.CancelledError:
                            # Once the resource sync has completed, cancellation must not
                            # strand its post-sync stat/fingerprint without the matching
                            # revision commit. Preserve cancellation, but only re-raise
                            # after the finalization unit reaches its terminal state.
                            try:
                                await finalization_task
                            except Exception as exc:
                                logger.error(
                                    "[WatchScheduler] Finalization failed for cancelled task "
                                    "%s: %s",
                                    task.task_id,
                                    exc,
                                    exc_info=True,
                                )
                            raise
                        if execution_state_committed:
                            logger.info(
                                f"[WatchScheduler] Updated execution time for task {task.task_id}"
                            )
                        else:
                            logger.info(
                                f"[WatchScheduler] Rejected stale execution state for task "
                                f"{task.task_id}; task remains due"
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

    async def _prepare_feishu_auth_state(
        self,
        task,
        auth_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not feishu_auth_state_needs_refresh(auth_state):
            return auth_state

        refresh_token = auth_state.get("refresh_token")
        refreshed = await self._get_feishu_oauth_client().refresh_user_access_token(refresh_token)
        updated = apply_feishu_refreshed_token(auth_state, refreshed)
        if self._watch_manager is not None:
            new_revision = await self._watch_manager.update_auth_state(
                task.task_id,
                updated,
                expected_revision=task.revision,
            )
            if new_revision is None:
                raise _StaleWatchTaskError("OAuth refresh lost a concurrent task update")
            task.revision = new_revision
        return updated

    def _get_feishu_oauth_client(self):
        if self._feishu_oauth_client is None:
            self._feishu_oauth_client = FeishuOAuthClient.from_config()
        return self._feishu_oauth_client

    def _get_feishu_accessor(self):
        if self._feishu_accessor is None:
            from openviking.parse.accessors.feishu_accessor import FeishuAccessor

            self._feishu_accessor = FeishuAccessor()
        return self._feishu_accessor

    async def _fetch_feishu_latest_modify_time(
        self,
        path: str,
        feishu_access_token: Optional[str] = None,
    ) -> Optional[int]:
        try:
            accessor = self._get_feishu_accessor()
            if not accessor.can_handle(path):
                return None
            return await accessor.fetch_latest_modify_time(
                path,
                feishu_access_token=feishu_access_token,
            )
        except Exception as exc:
            logger.warning(
                "[WatchScheduler] Feishu source precheck failed for %s: %s; "
                "falling back to full synchronization",
                path,
                exc,
            )
            return None

    async def _fetch_destination_sync_state(
        self,
        uri: Optional[str],
        ctx: RequestContext,
    ) -> Optional[Dict[str, Any]]:
        if not uri:
            # add_resource may choose a dynamic root URI when no target is supplied.
            # Without a concrete destination to stat on later runs, a fingerprint
            # cannot prove that the previously synchronized resource still exists.
            return None
        if self._viking_fs is None:
            return None
        try:
            stat = await self._viking_fs.stat(uri, ctx=ctx, skip_count=True)
            if not isinstance(stat, dict):
                return None
            return {
                "uri": uri,
                "is_dir": bool(stat.get("isDir", False)),
                "name": stat.get("name"),
                "size": stat.get("size"),
                "mod_time": stat.get("modTime"),
            }
        except Exception as exc:
            logger.warning(
                "[WatchScheduler] Destination state precheck failed for %s: %s; "
                "falling back to full synchronization",
                uri,
                exc,
            )
            return None

    @staticmethod
    def _resource_sync_completed(
        result: Any,
        *,
        require_queue_status: bool,
    ) -> bool:
        if not isinstance(result, dict) or result.get("status") == "error":
            return False
        if not require_queue_status:
            return True

        queue_status = result.get("queue_status")
        if not isinstance(queue_status, dict) or not queue_status:
            return False
        for status in queue_status.values():
            if not isinstance(status, dict):
                return False
            try:
                error_count = int(status["error_count"])
            except (KeyError, TypeError, ValueError):
                return False
            if error_count != 0 or status.get("errors"):
                return False
        return True

    def _build_feishu_sync_fingerprint(
        self,
        task,
        *,
        processor_kwargs: Dict[str, Any],
        source_version: Optional[int],
        destination_state: Optional[Dict[str, Any]],
        auth_context: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if source_version is None or destination_state is None or auth_context is None:
            return None

        try:
            stable_kwargs = dict(processor_kwargs)
            stable_kwargs.pop(_FEISHU_ACCESS_TOKEN_KEY, None)

            payload = {
                "schema": "feishu_watch_sync_fingerprint_v1",
                "source": {
                    "path": task.path,
                    "latest_modify_time": source_version,
                },
                "destination": destination_state,
                "inputs": {
                    "to_uri": task.to_uri,
                    "parent_uri": task.parent_uri,
                    "reason": task.reason,
                    "instruction": task.instruction,
                    "build_index": getattr(task, "build_index", True),
                    "summarize": getattr(task, "summarize", False),
                    "processor_kwargs": stable_kwargs,
                    "account_id": task.account_id,
                    "user_id": task.user_id,
                    "original_role": getattr(task, "original_role", None),
                    "auth": auth_context,
                },
            }
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        except Exception as exc:
            logger.warning(
                "[WatchScheduler] Failed to build Feishu sync fingerprint for %s: %s; "
                "falling back to full synchronization",
                task.path,
                exc,
            )
            return None
        return hashlib.sha256(encoded).hexdigest()

    def _capture_feishu_auth_context(
        self,
        task,
        *,
        processor_kwargs: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Capture one immutable auth identity for this synchronization."""
        try:
            explicit_access_token = processor_kwargs.get(_FEISHU_ACCESS_TOKEN_KEY)
            auth_state = getattr(task, "auth_state", None)
            if is_feishu_auth_state(auth_state):
                return {
                    "mode": "user",
                    "provider": auth_state.get("provider"),
                    "refresh_token_digest": self._secret_digest(auth_state.get("refresh_token")),
                }
            if explicit_access_token:
                return {
                    "mode": "user",
                    "access_token_digest": self._secret_digest(explicit_access_token),
                }

            credentials = load_feishu_app_credentials()
            return {
                "mode": "app",
                "app_id": credentials.app_id,
                "domain": credentials.domain,
                "app_secret_digest": self._secret_digest(credentials.app_secret),
            }
        except Exception as exc:
            logger.warning(
                "[WatchScheduler] Failed to capture Feishu auth context for %s: %s; "
                "falling back to full synchronization",
                task.path,
                exc,
            )
            return None

    @staticmethod
    def _secret_digest(value: Any) -> Optional[str]:
        if value is None:
            return None
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

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

    async def _check_target_uri_exists(self, uri: str, ctx: RequestContext) -> Optional[bool]:
        if self._viking_fs is None:
            return True
        try:
            await self._viking_fs.stat(uri, ctx=ctx)
            return True
        except NotFoundError:
            return False
        except Exception as e:
            if is_not_found_error(e):
                return False
            logger.warning(f"[WatchScheduler] Failed to check target URI existence {uri}: {e}")
            return None

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._running

    @property
    def executing_tasks(self) -> Set[str]:
        """Get the set of currently executing task IDs."""
        return self._executing_tasks.copy()
