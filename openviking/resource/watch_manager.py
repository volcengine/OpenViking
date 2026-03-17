# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Resource monitoring task manager.

Provides task creation, update, deletion, query, and persistence storage.
"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class WatchTask(BaseModel):
    """Resource monitoring task data model."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique task identifier")
    path: str = Field(..., description="Resource path to monitor")
    to_uri: Optional[str] = Field(None, description="Target URI")
    parent_uri: Optional[str] = Field(None, description="Parent URI")
    reason: str = Field(default="", description="Reason for monitoring")
    instruction: str = Field(default="", description="Monitoring instruction")
    watch_interval: float = Field(default=60.0, description="Monitoring interval in minutes")
    created_at: datetime = Field(default_factory=datetime.now, description="Task creation time")
    last_execution_time: Optional[datetime] = Field(None, description="Last execution time")
    next_execution_time: Optional[datetime] = Field(None, description="Next execution time")
    is_active: bool = Field(default=True, description="Whether the task is active")
    account_id: str = Field(default="default", description="Account ID (tenant)")
    user_id: str = Field(default="default", description="User ID who created this task")
    agent_id: str = Field(default="default", description="Agent ID who created this task")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "task_id": self.task_id,
            "path": self.path,
            "to_uri": self.to_uri,
            "parent_uri": self.parent_uri,
            "reason": self.reason,
            "instruction": self.instruction,
            "watch_interval": self.watch_interval,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_execution_time": self.last_execution_time.isoformat() if self.last_execution_time else None,
            "next_execution_time": self.next_execution_time.isoformat() if self.next_execution_time else None,
            "is_active": self.is_active,
            "account_id": self.account_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchTask":
        """Create task from dictionary."""
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("last_execution_time"), str):
            data["last_execution_time"] = datetime.fromisoformat(data["last_execution_time"])
        if isinstance(data.get("next_execution_time"), str):
            data["next_execution_time"] = datetime.fromisoformat(data["next_execution_time"])
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

    STORAGE_URI = "viking://resources/watch_tasks.json"

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

            content = await self._viking_fs.read_file(self.STORAGE_URI, ctx=ctx)
            data = json.loads(content)

            for task_data in data.get("tasks", []):
                try:
                    task = WatchTask.from_dict(task_data)
                    self._tasks[task.task_id] = task
                    if task.to_uri:
                        self._uri_to_task[task.to_uri] = task.task_id
                except Exception as e:
                    logger.warning(f"[WatchManager] Failed to load task {task_data.get('task_id')}: {e}")

            logger.info(f"[WatchManager] Loaded {len(self._tasks)} tasks from storage")
        except FileNotFoundError:
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
                "tasks": [task.to_dict() for task in self._tasks.values()],
                "updated_at": datetime.now().isoformat(),
            }

            content = json.dumps(data, ensure_ascii=False, indent=2)
            await self._viking_fs.write_file(self.STORAGE_URI, content, ctx=ctx)
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
        require_owner: bool = False,
    ) -> bool:
        """Check if user has permission to access/modify a task.

        Args:
            task: The task to check permission for
            account_id: Requester's account ID
            user_id: Requester's user ID
            role: Requester's role (ROOT/ADMIN/USER)
            require_owner: If True, only owner can access (for delete/update)

        Returns:
            True if has permission, False otherwise
        """
        if role == "ROOT":
            return True

        if task.account_id != account_id:
            return False

        if role == "ADMIN":
            return True

        if require_owner:
            return task.user_id == user_id

        return task.user_id == user_id

    def _check_uri_conflict(self, to_uri: Optional[str], exclude_task_id: Optional[str] = None) -> bool:
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
        account_id: str,
        user_id: str,
        agent_id: str,
        to_uri: Optional[str] = None,
        parent_uri: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        watch_interval: float = 60.0,
    ) -> WatchTask:
        """Create a new monitoring task.

        Args:
            path: Resource path to monitor
            account_id: Account ID (tenant)
            user_id: User ID who creates this task
            agent_id: Agent ID who creates this task
            to_uri: Target URI
            parent_uri: Parent URI
            reason: Reason for monitoring
            instruction: Monitoring instruction
            watch_interval: Monitoring interval in minutes

        Returns:
            Created WatchTask

        Raises:
            ValueError: If required fields are missing or URI conflicts
        """
        if not path:
            raise ValueError("Path is required")

        async with self._lock:
            if self._check_uri_conflict(to_uri):
                raise ValueError(f"Target URI '{to_uri}' is already used by another task")

            task = WatchTask(
                path=path,
                to_uri=to_uri,
                parent_uri=parent_uri,
                reason=reason,
                instruction=instruction,
                watch_interval=watch_interval,
                account_id=account_id,
                user_id=user_id,
                agent_id=agent_id,
            )

            task.next_execution_time = task.calculate_next_execution_time()

            self._tasks[task.task_id] = task
            if to_uri:
                self._uri_to_task[to_uri] = task.task_id

            await self._save_tasks()

            logger.info(f"[WatchManager] Created task {task.task_id} for path {path} by user {account_id}/{user_id}")
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
            ValueError: If task not found or URI conflicts
            PermissionDeniedError: If user doesn't have permission
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            if not self._check_permission(task, account_id, user_id, role, require_owner=True):
                raise PermissionDeniedError(
                    f"User {account_id}/{user_id} does not have permission to update task {task_id}"
                )

            if self._check_uri_conflict(to_uri, exclude_task_id=task_id):
                raise ValueError(f"Target URI '{to_uri}' is already used by another task")

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
                task.watch_interval = watch_interval
            if is_active is not None:
                task.is_active = is_active

            if watch_interval is not None:
                task.next_execution_time = task.calculate_next_execution_time()

            if to_uri is not None:
                if old_to_uri and old_to_uri != to_uri:
                    self._uri_to_task.pop(old_to_uri, None)
                if to_uri:
                    self._uri_to_task[to_uri] = task_id

            await self._save_tasks()

            logger.info(f"[WatchManager] Updated task {task_id} by user {account_id}/{user_id}")
            return task

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

            if not self._check_permission(task, account_id, user_id, role, require_owner=True):
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
        account_id: str,
        user_id: str,
        role: str,
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

    async def clear_all_tasks(self) -> int:
        """Clear all tasks (for testing purposes).

        Returns:
            Number of tasks cleared
        """
        async with self._lock:
            count = len(self._tasks)
            self._tasks.clear()
            self._uri_to_task.clear()

            await self._save_tasks()

            logger.info(f"[WatchManager] Cleared {count} tasks")
            return count
