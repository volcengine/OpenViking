# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Resource monitoring task manager.

Provides task creation, update, deletion, query, and persistence storage.
"""

import asyncio
import inspect
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from openviking.resource.watch_storage import (
    WATCH_TASK_STORAGE_BAK_URI,
    WATCH_TASK_STORAGE_TMP_URI,
    WATCH_TASK_STORAGE_URI,
)
from openviking_cli.exceptions import ConflictError, NotFoundError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_UNSET = object()


def _uri_matches_prefix(uri: Optional[str], prefix: str) -> bool:
    if not uri:
        return False
    normalized = prefix.rstrip("/")
    return uri == normalized or uri.startswith(normalized + "/")


def _rewrite_uri_prefix(uri: str, old_prefix: str, new_prefix: str) -> str:
    old_normalized = old_prefix.rstrip("/")
    new_normalized = new_prefix.rstrip("/")
    if uri == old_normalized:
        return new_normalized
    return new_normalized + uri[len(old_normalized) :]


def _parent_uri(uri: str) -> Optional[str]:
    normalized = uri.rstrip("/")
    if "/" not in normalized:
        return None
    parent = normalized.rsplit("/", 1)[0]
    if parent in {"viking:/", "viking://"}:
        return None
    return parent


class WatchTask(BaseModel):
    """Resource monitoring task data model."""

    task_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Unique task identifier"
    )
    path: str = Field(..., description="Resource path to monitor")
    to_uri: Optional[str] = Field(None, description="Target URI")
    parent_uri: Optional[str] = Field(None, description="Parent URI")
    reason: str = Field(default="", description="Reason for monitoring")
    instruction: str = Field(default="", description="Monitoring instruction")
    watch_interval: float = Field(default=60.0, description="Monitoring interval in minutes")
    build_index: bool = Field(default=True, description="Whether to build vector index")
    summarize: bool = Field(default=False, description="Whether to generate summary")
    processor_kwargs: Dict[str, Any] = Field(
        default_factory=dict, description="Extra kwargs forwarded to processor"
    )
    auth_state: Optional[Dict[str, Any]] = Field(
        default=None, description="Private authentication state for scheduled re-processing"
    )
    created_at: datetime = Field(default_factory=datetime.now, description="Task creation time")
    last_execution_time: Optional[datetime] = Field(None, description="Last execution time")
    next_execution_time: Optional[datetime] = Field(None, description="Next execution time")
    is_active: bool = Field(default=True, description="Whether the task is active")
    account_id: str = Field(default="default", description="Account ID (tenant)")
    user_id: str = Field(default="default", description="User ID who created this task")
    original_role: str = Field(default="user", description="Role used to execute this task")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat() if v else None}
        extra = "ignore"

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to public dictionary."""
        return {
            "task_id": self.task_id,
            "path": self.path,
            "to_uri": self.to_uri,
            "parent_uri": self.parent_uri,
            "reason": self.reason,
            "instruction": self.instruction,
            "watch_interval": self.watch_interval,
            "build_index": self.build_index,
            "summarize": self.summarize,
            "processor_kwargs": self.processor_kwargs,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_execution_time": self.last_execution_time.isoformat()
            if self.last_execution_time
            else None,
            "next_execution_time": self.next_execution_time.isoformat()
            if self.next_execution_time
            else None,
            "is_active": self.is_active,
            "account_id": self.account_id,
            "user_id": self.user_id,
            "original_role": self.original_role,
        }

    def to_storage_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary for watch-task persistence."""
        data = self.to_dict()
        if self.auth_state is not None:
            data["auth_state"] = self.auth_state
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchTask":
        """Create task from dictionary."""
        data = dict(data)
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("last_execution_time"), str):
            data["last_execution_time"] = datetime.fromisoformat(data["last_execution_time"])
        if isinstance(data.get("next_execution_time"), str):
            data["next_execution_time"] = datetime.fromisoformat(data["next_execution_time"])
        if data.get("processor_kwargs") is None:
            data["processor_kwargs"] = {}
        if data.get("auth_state") is not None and not isinstance(data.get("auth_state"), dict):
            data["auth_state"] = None
        return cls(**data)

    def calculate_next_execution_time(self) -> datetime:
        """Calculate next execution time based on interval."""
        base_time = self.last_execution_time or self.created_at
        return base_time + timedelta(minutes=self.watch_interval)


class PermissionDeniedError(Exception):
    """Permission denied error for watch operations."""

    pass


class WatchManager:
    """Resource monitoring task manager.

    Provides task creation, update, deletion, query, and persistence storage.
    Thread-safe with async lock for concurrent access protection.
    Supports multi-tenant authorization.
    """

    STORAGE_URI = WATCH_TASK_STORAGE_URI
    STORAGE_BAK_URI = WATCH_TASK_STORAGE_BAK_URI
    STORAGE_TMP_URI = WATCH_TASK_STORAGE_TMP_URI

    def __init__(self, viking_fs: Optional[Any] = None):
        """Initialize WatchManager.

        Args:
            viking_fs: VikingFS instance for persistence storage
        """
        self._tasks: Dict[str, WatchTask] = {}
        self._uri_to_task: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._viking_fs = viking_fs
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the manager by loading tasks from storage."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            await self._load_tasks()
            self._initialized = True
            logger.info(f"[WatchManager] Initialized with {len(self._tasks)} tasks")

    async def _load_tasks(self) -> None:
        """Load tasks from VikingFS storage."""
        if not self._viking_fs:
            logger.debug("[WatchManager] No VikingFS provided, skipping load")
            return

        try:
            from openviking.server.identity import RequestContext, Role
            from openviking_cli.session.user_id import UserIdentifier

            ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

            data = None
            try:
                content = await self._viking_fs.read_file(self.STORAGE_URI, ctx=ctx)
                if content and content.strip():
                    data = json.loads(content)
            except NotFoundError:
                data = None
            except json.JSONDecodeError as e:
                logger.warning(f"[WatchManager] Invalid task storage JSON: {e}")
            except Exception as e:
                logger.warning(f"[WatchManager] Failed to read task storage: {e}")

            recovered_from_backup = False
            if data is None:
                try:
                    bak_content = await self._viking_fs.read_file(self.STORAGE_BAK_URI, ctx=ctx)
                    if bak_content and bak_content.strip():
                        data = json.loads(bak_content)
                        recovered_from_backup = True
                except NotFoundError:
                    data = None
                except json.JSONDecodeError as e:
                    logger.warning(f"[WatchManager] Invalid backup task storage JSON: {e}")
                    data = None
                except Exception as e:
                    logger.warning(f"[WatchManager] Failed to read backup task storage: {e}")

            if not isinstance(data, dict):
                data = {"tasks": []}

            normalized = False
            for task_data in data.get("tasks", []):
                try:
                    task = WatchTask.from_dict(task_data)
                    if not task.is_active:
                        if task.next_execution_time is not None:
                            task.next_execution_time = None
                            normalized = True
                    else:
                        if task.watch_interval <= 0:
                            task.is_active = False
                            task.next_execution_time = None
                            normalized = True
                        elif task.next_execution_time is None:
                            task.next_execution_time = task.calculate_next_execution_time()
                            normalized = True
                    self._tasks[task.task_id] = task
                    if task.to_uri:
                        self._uri_to_task[task.to_uri] = task.task_id
                except Exception as e:
                    logger.warning(
                        f"[WatchManager] Failed to load task {task_data.get('task_id')}: {e}"
                    )

            logger.info(f"[WatchManager] Loaded {len(self._tasks)} tasks from storage")
            if recovered_from_backup:
                normalized = True
            if normalized:
                await self._save_tasks()
        except NotFoundError:
            logger.debug("[WatchManager] No existing task storage found, starting fresh")
        except Exception as e:
            logger.error(f"[WatchManager] Failed to load tasks: {e}")

    async def _save_tasks(self) -> None:
        """Save tasks to VikingFS storage."""
        if not self._viking_fs:
            logger.debug("[WatchManager] No VikingFS provided, skipping save")
            return

        try:
            from openviking.server.identity import RequestContext, Role
            from openviking_cli.session.user_id import UserIdentifier

            ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

            data = {
                "tasks": [task.to_storage_dict() for task in self._tasks.values()],
                "updated_at": datetime.now().isoformat(),
            }

            content = json.dumps(data, ensure_ascii=False, indent=2)
            if not content.strip():
                raise ValueError("Refusing to write empty watch task storage")
            json.loads(content)

            supports_atomic = all(
                hasattr(self._viking_fs, name) for name in ("mv", "rm", "exists", "write_file")
            )
            if not supports_atomic:
                await self._viking_fs.write_file(self.STORAGE_URI, content, ctx=ctx)
                logger.debug(f"[WatchManager] Saved {len(self._tasks)} tasks to storage")
                return

            await self._viking_fs.write_file(self.STORAGE_TMP_URI, content, ctx=ctx)

            try:
                if await self._viking_fs.exists(self.STORAGE_BAK_URI, ctx=ctx):
                    await self._viking_fs.rm(self.STORAGE_BAK_URI, ctx=ctx)
            except Exception:
                pass

            try:
                if await self._viking_fs.exists(self.STORAGE_URI, ctx=ctx):
                    await self._viking_fs.mv(self.STORAGE_URI, self.STORAGE_BAK_URI, ctx=ctx)
            except Exception as e:
                logger.warning(f"[WatchManager] Failed to rotate task storage backup: {e}")

            await self._viking_fs.mv(self.STORAGE_TMP_URI, self.STORAGE_URI, ctx=ctx)
            logger.debug(f"[WatchManager] Saved {len(self._tasks)} tasks to storage")
        except Exception as e:
            logger.error(f"[WatchManager] Failed to save tasks: {e}")
            raise

    def _check_permission(
        self,
        task: WatchTask,
        account_id: str,
        user_id: str,
        role: str,
    ) -> bool:
        """Check if user has permission to access/modify a task.

        Args:
            task: The task to check permission for
            account_id: Requester's account ID
            user_id: Requester's user ID
            role: Requester's role (ROOT/ADMIN/USER)

        Returns:
            True if has permission, False otherwise

        Notes:
            - ROOT can access all tasks.
            - ADMIN can access tasks within the same account.
            - USER can only access tasks they created within the same account.
        """
        role_value = (role or "").lower()
        if role_value == "root":
            return True

        if task.account_id != account_id:
            return False

        if role_value == "admin":
            return True

        return task.user_id == user_id

    def _check_uri_conflict(
        self, to_uri: Optional[str], exclude_task_id: Optional[str] = None
    ) -> bool:
        """Check if target URI conflicts with existing tasks.

        Args:
            to_uri: Target URI to check
            exclude_task_id: Task ID to exclude from conflict check (for updates)

        Returns:
            True if there's a conflict, False otherwise
        """
        if not to_uri:
            return False

        existing_task_id = self._uri_to_task.get(to_uri)
        if not existing_task_id:
            return False

        if exclude_task_id and existing_task_id == exclude_task_id:
            return False

        return True

    async def create_task(
        self,
        path: str,
        account_id: str = "default",
        user_id: str = "default",
        original_role: str = "user",
        to_uri: Optional[str] = None,
        parent_uri: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        watch_interval: float = 60.0,
        build_index: bool = True,
        summarize: bool = False,
        processor_kwargs: Optional[Dict[str, Any]] = None,
        auth_state: Optional[Dict[str, Any]] = None,
    ) -> WatchTask:
        """Create a new monitoring task.

        Args:
            path: Resource path to monitor
            account_id: Account ID (tenant)
            user_id: User ID who creates this task
            to_uri: Target URI
            parent_uri: Parent URI
            reason: Reason for monitoring
            instruction: Monitoring instruction
            watch_interval: Monitoring interval in minutes

        Returns:
            Created WatchTask

        Raises:
            ValueError: If required fields are missing
            ConflictError: If target URI conflicts with existing tasks
        """
        if not path:
            raise ValueError("Path is required")
        if watch_interval <= 0:
            raise ValueError("watch_interval must be > 0")

        async with self._lock:
            if self._check_uri_conflict(to_uri):
                raise ConflictError(
                    f"Target URI '{to_uri}' is already used by another task",
                    resource=to_uri,
                )

            task = WatchTask(
                path=path,
                to_uri=to_uri,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                build_index=build_index,
                summarize=summarize,
                processor_kwargs=processor_kwargs or {},
                auth_state=auth_state,
                account_id=account_id,
                user_id=user_id,
                original_role=original_role,
            )

            task.next_execution_time = task.calculate_next_execution_time()

            self._tasks[task.task_id] = task
            if to_uri:
                self._uri_to_task[to_uri] = task.task_id

            await self._save_tasks()

            logger.info(
                f"[WatchManager] Created task {task.task_id} for path {path} by user {account_id}/{user_id}"
            )
            return task

    async def update_task(
        self,
        task_id: str,
        account_id: str,
        user_id: str,
        role: str,
        path: Optional[str] = None,
        to_uri: Optional[str] = None,
        parent_uri: Optional[str] = None,
        reason: Optional[str] = None,
        instruction: Optional[str] = None,
        watch_interval: Optional[float] = None,
        build_index: Optional[bool] = None,
        summarize: Optional[bool] = None,
        processor_kwargs: Optional[Dict[str, Any]] = None,
        auth_state: Any = _UNSET,
        is_active: Optional[bool] = None,
    ) -> WatchTask:
        """Update an existing monitoring task.

        Args:
            task_id: Task ID to update
            account_id: Requester's account ID
            user_id: Requester's user ID
            role: Requester's role (ROOT/ADMIN/USER)
            path: New resource path
            to_uri: New target URI
            parent_uri: New parent URI
            reason: New reason
            instruction: New instruction
            watch_interval: New monitoring interval
            is_active: New active status

        Returns:
            Updated WatchTask

        Raises:
            ValueError: If task not found or invalid arguments
            ConflictError: If target URI conflicts with existing tasks
            PermissionDeniedError: If user doesn't have permission
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            if not self._check_permission(task, account_id, user_id, role):
                raise PermissionDeniedError(
                    f"User {account_id}/{user_id} does not have permission to update task {task_id}"
                )

            if self._check_uri_conflict(to_uri, exclude_task_id=task_id):
                raise ConflictError(
                    f"Target URI '{to_uri}' is already used by another task",
                    resource=to_uri,
                )

            old_to_uri = task.to_uri

            if path is not None:
                task.path = path
            if to_uri is not None:
                task.to_uri = to_uri
            if parent_uri is not None:
                task.parent_uri = parent_uri
            if reason is not None:
                task.reason = reason
            if instruction is not None:
                task.instruction = instruction
            if watch_interval is not None:
                if watch_interval <= 0:
                    if is_active is True:
                        raise ValueError("watch_interval must be > 0 for active tasks")
                    task.watch_interval = watch_interval
                    task.is_active = False
                    task.next_execution_time = None
                else:
                    task.watch_interval = watch_interval
            if build_index is not None:
                task.build_index = build_index
            if summarize is not None:
                task.summarize = summarize
            if processor_kwargs is not None:
                task.processor_kwargs = processor_kwargs
            if auth_state is not _UNSET:
                task.auth_state = auth_state
            if is_active is not None:
                task.is_active = is_active

            if watch_interval is not None:
                if task.is_active and task.watch_interval > 0:
                    task.next_execution_time = task.calculate_next_execution_time()
                else:
                    task.next_execution_time = None
            if is_active is not None and watch_interval is None:
                if task.is_active:
                    if task.watch_interval <= 0:
                        raise ValueError("watch_interval must be > 0 for active tasks")
                    if task.next_execution_time is None:
                        task.next_execution_time = task.calculate_next_execution_time()
                else:
                    task.next_execution_time = None

            if to_uri is not None:
                if old_to_uri and old_to_uri != to_uri:
                    self._uri_to_task.pop(old_to_uri, None)
                if to_uri:
                    self._uri_to_task[to_uri] = task_id

            await self._save_tasks()

            logger.info(f"[WatchManager] Updated task {task_id} by user {account_id}/{user_id}")
            return task

    async def update_auth_state(
        self,
        task_id: str,
        auth_state: Optional[Dict[str, Any]],
    ) -> None:
        """Update private auth state for an existing watch task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.auth_state = auth_state
            await self._save_tasks()

    def _plan_move_tasks_under_uri_unlocked(
        self,
        old_uri: str,
        new_uri: str,
    ) -> Dict[str, str]:
        old_prefix = old_uri.rstrip("/")
        new_prefix = new_uri.rstrip("/")
        plan: Dict[str, str] = {}
        for task_id, task in self._tasks.items():
            if _uri_matches_prefix(task.to_uri, old_prefix):
                plan[task_id] = _rewrite_uri_prefix(task.to_uri or "", old_prefix, new_prefix)

        moving_task_ids = set(plan)
        for task_id, target_uri in plan.items():
            existing_task_id = self._uri_to_task.get(target_uri)
            if existing_task_id and existing_task_id not in moving_task_ids:
                raise ConflictError(
                    f"Target URI '{target_uri}' is already used by another task",
                    resource=target_uri,
                )
            if existing_task_id in moving_task_ids and existing_task_id != task_id:
                raise ConflictError(
                    f"Target URI '{target_uri}' is already used by another moved task",
                    resource=target_uri,
                )
        return plan

    async def sync_tasks_with_resource_move_internal(
        self,
        old_uri: str,
        new_uri: str,
        move_resource: Callable[[], Awaitable[None]],
        rollback_resource: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> List[WatchTask]:
        """Move a resource and keep watch-task target URIs in sync under one lock."""
        async with self._lock:
            plan = self._plan_move_tasks_under_uri_unlocked(old_uri, new_uri)
            move_result = move_resource()
            if inspect.isawaitable(move_result):
                await move_result
            if not plan:
                return []

            original_targets = {
                task_id: (task.to_uri, task.parent_uri)
                for task_id, task in self._tasks.items()
                if task_id in plan
            }
            original_uri_to_task = dict(self._uri_to_task)

            try:
                for task_id in plan:
                    task = self._tasks[task_id]
                    if task.to_uri:
                        self._uri_to_task.pop(task.to_uri, None)

                updated: List[WatchTask] = []
                for task_id, target_uri in plan.items():
                    task = self._tasks[task_id]
                    old_parent = _parent_uri(task.to_uri or "")
                    task.to_uri = target_uri
                    if task.parent_uri is not None and task.parent_uri == old_parent:
                        task.parent_uri = _parent_uri(target_uri)
                    self._uri_to_task[target_uri] = task_id
                    updated.append(task)

                await self._save_tasks()
                logger.info(
                    f"[WatchManager] Rewrote {len(updated)} watch task target URI(s) "
                    f"under {old_uri} to {new_uri}"
                )
                return updated
            except Exception:
                for task_id, (original_to_uri, original_parent_uri) in original_targets.items():
                    task = self._tasks[task_id]
                    task.to_uri = original_to_uri
                    task.parent_uri = original_parent_uri
                self._uri_to_task = original_uri_to_task
                if rollback_resource is not None:
                    rollback_result = rollback_resource()
                    if inspect.isawaitable(rollback_result):
                        await rollback_result
                raise

    async def deactivate_tasks_under_uri_internal(self, uri: str) -> List[WatchTask]:
        """Deactivate watch tasks whose target URI is deleted."""
        async with self._lock:
            matched = [
                task
                for task in self._tasks.values()
                if _uri_matches_prefix(task.to_uri, uri)
            ]
            if not matched:
                return []

            for task in matched:
                task.is_active = False
                task.next_execution_time = None

            await self._save_tasks()
            logger.info(f"[WatchManager] Deactivated {len(matched)} watch task(s) under {uri}")
            return matched

    async def delete_task(
        self,
        task_id: str,
        account_id: str,
        user_id: str,
        role: str,
    ) -> bool:
        """Delete a monitoring task.

        Args:
            task_id: Task ID to delete
            account_id: Requester's account ID
            user_id: Requester's user ID
            role: Requester's role (ROOT/ADMIN/USER)

        Returns:
            True if task was deleted, False if not found

        Raises:
            PermissionDeniedError: If user doesn't have permission
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            if not self._check_permission(task, account_id, user_id, role):
                raise PermissionDeniedError(
                    f"User {account_id}/{user_id} does not have permission to delete task {task_id}"
                )

            self._tasks.pop(task_id, None)
            if task.to_uri:
                self._uri_to_task.pop(task.to_uri, None)

            await self._save_tasks()

            logger.info(f"[WatchManager] Deleted task {task_id} by user {account_id}/{user_id}")
            return True

    async def get_task(
        self,
        task_id: str,
        account_id: str = "default",
        user_id: str = "default",
        role: str = "root",
    ) -> Optional[WatchTask]:
        """Get a monitoring task by ID.

        Args:
            task_id: Task ID to query
            account_id: Requester's account ID
            user_id: Requester's user ID
            role: Requester's role (ROOT/ADMIN/USER)

        Returns:
            WatchTask if found and accessible, None otherwise
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            if not self._check_permission(task, account_id, user_id, role):
                return None

            return task

    async def get_all_tasks(
        self,
        account_id: str,
        user_id: str,
        role: str,
        active_only: bool = False,
    ) -> List[WatchTask]:
        """Get all monitoring tasks accessible by the user.

        Args:
            account_id: Requester's account ID
            user_id: Requester's user ID
            role: Requester's role (ROOT/ADMIN/USER)
            active_only: If True, only return active tasks

        Returns:
            List of accessible WatchTask objects
        """
        async with self._lock:
            tasks = []
            for task in self._tasks.values():
                if not self._check_permission(task, account_id, user_id, role):
                    continue
                if active_only and not task.is_active:
                    continue
                tasks.append(task)
            return tasks

    async def get_task_by_uri(
        self,
        to_uri: str,
        account_id: str,
        user_id: str,
        role: str,
    ) -> Optional[WatchTask]:
        """Get a monitoring task by target URI.

        Args:
            to_uri: Target URI to query
            account_id: Requester's account ID
            user_id: Requester's user ID
            role: Requester's role (ROOT/ADMIN/USER)

        Returns:
            WatchTask if found and accessible, None otherwise
        """
        async with self._lock:
            task_id = self._uri_to_task.get(to_uri)
            if not task_id:
                return None

            task = self._tasks.get(task_id)
            if not task:
                return None

            if not self._check_permission(task, account_id, user_id, role):
                return None

            return task

    async def update_execution_time(self, task_id: str) -> None:
        """Update task execution time after execution.

        Args:
            task_id: Task ID to update
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return

            if not task.is_active or task.watch_interval <= 0:
                task.is_active = False
                task.next_execution_time = None
                await self._save_tasks()
                return

            task.last_execution_time = datetime.now()
            task.next_execution_time = task.calculate_next_execution_time()

            await self._save_tasks()

    async def get_due_tasks(self, account_id: Optional[str] = None) -> List[WatchTask]:
        """Get all tasks that are due for execution.

        Args:
            account_id: Optional account ID filter (for scheduler)

        Returns:
            List of tasks that need to be executed
        """
        async with self._lock:
            now = datetime.now()
            due_tasks = []

            for task in self._tasks.values():
                if not task.is_active:
                    continue

                if account_id and task.account_id != account_id:
                    continue

                if task.next_execution_time and task.next_execution_time <= now:
                    due_tasks.append(task)

            return due_tasks

    async def get_next_execution_time(self, account_id: Optional[str] = None) -> Optional[datetime]:
        async with self._lock:
            next_times: List[datetime] = []
            for task in self._tasks.values():
                if not task.is_active:
                    continue
                if account_id and task.account_id != account_id:
                    continue
                if task.next_execution_time is None:
                    continue
                next_times.append(task.next_execution_time)
            return min(next_times) if next_times else None
