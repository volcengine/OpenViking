# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Async Task Tracker for OpenViking.

Provides a lightweight, in-memory registry for tracking background operations
(e.g. session commit with wait=false). Callers receive a task_id that can be
polled via the /tasks API to check completion status, results, or errors.

Design decisions:
  - v1 is pure in-memory (no persistence). Tasks are lost on restart.
  - Thread-safe (QueueManager workers run in separate threads).
  - TTL-based cleanup prevents unbounded memory growth.
  - Error messages are sanitized to avoid leaking sensitive data.
"""

import asyncio
import re
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class TaskStatus(str, Enum):
    """Lifecycle states of an async task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskRecord:
    """Immutable snapshot of an async task."""

    task_id: str
    task_type: str  # e.g. "session_commit"
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resource_id: Optional[str] = None  # e.g. session_id
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON response."""
        d = asdict(self)
        d["status"] = self.status.value
        return d


# ── Singleton ──

_instance: Optional["TaskTracker"] = None
_init_lock = threading.Lock()


def get_task_tracker() -> "TaskTracker":
    """Get or create the global TaskTracker singleton."""
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = TaskTracker()
    return _instance


def reset_task_tracker() -> None:
    """Reset singleton (for testing)."""
    global _instance
    _instance = None


# ── Sanitization ──

_SENSITIVE_PATTERNS = re.compile(
    r"(sk-|cr_|ghp_|ntn_|xox[baprs]-|Bearer\s+)[a-zA-Z0-9._-]+",
    re.IGNORECASE,
)

_MAX_ERROR_LEN = 500


def _sanitize_error(error: str) -> str:
    """Remove potential secrets from error messages."""
    sanitized = _SENSITIVE_PATTERNS.sub("[REDACTED]", error)
    if len(sanitized) > _MAX_ERROR_LEN:
        sanitized = sanitized[:_MAX_ERROR_LEN] + "...[truncated]"
    return sanitized


# ── TaskTracker ──


class TaskTracker:
    """In-memory async task tracker with TTL-based cleanup.

    Thread-safe: all mutations go through ``_lock``.
    """

    MAX_TASKS = 10_000
    TTL_COMPLETED = 86_400  # 24 hours
    TTL_FAILED = 604_800  # 7 days
    CLEANUP_INTERVAL = 300  # 5 minutes

    def __init__(self) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        logger.info("[TaskTracker] Initialized (in-memory, max_tasks=%d)", self.MAX_TASKS)

    # ── Lifecycle ──

    def start_cleanup_loop(self) -> None:
        """Start the background TTL cleanup coroutine.

        Safe to call multiple times; subsequent calls are no-ops.
        Must be called from within a running event loop.
        """
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("[TaskTracker] Cleanup loop started")

    def stop_cleanup_loop(self) -> None:
        """Cancel the background cleanup task. Safe to call if not started."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.debug("[TaskTracker] Cleanup loop stopped")

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                self._evict_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[TaskTracker] Cleanup error")

    def _evict_expired(self) -> None:
        """Remove expired tasks and enforce MAX_TASKS."""
        now = time.time()
        with self._lock:
            to_delete = []
            for tid, t in self._tasks.items():
                if t.status == TaskStatus.COMPLETED and (now - t.updated_at) > self.TTL_COMPLETED:
                    to_delete.append(tid)
                elif t.status == TaskStatus.FAILED and (now - t.updated_at) > self.TTL_FAILED:
                    to_delete.append(tid)
            for tid in to_delete:
                del self._tasks[tid]

            # FIFO eviction if still over limit
            if len(self._tasks) > self.MAX_TASKS:
                sorted_tasks = sorted(self._tasks.items(), key=lambda x: x[1].created_at)
                excess = len(self._tasks) - self.MAX_TASKS
                for tid, _ in sorted_tasks[:excess]:
                    del self._tasks[tid]

            if to_delete:
                logger.debug("[TaskTracker] Evicted %d expired tasks", len(to_delete))

    # ── CRUD ──

    def create(self, task_type: str, resource_id: Optional[str] = None) -> TaskRecord:
        """Register a new pending task. Returns a snapshot copy."""
        task = TaskRecord(
            task_id=str(uuid4()),
            task_type=task_type,
            resource_id=resource_id,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        logger.debug(
            "[TaskTracker] Created task %s type=%s resource=%s",
            task.task_id,
            task_type,
            resource_id,
        )
        return self._copy(task)

    def create_if_no_running(self, task_type: str, resource_id: str) -> Optional[TaskRecord]:
        """Atomically check for running tasks and create a new one if none exist.

        Returns TaskRecord on success, None if a running task already exists.
        This eliminates the race condition between has_running() and create().
        """
        with self._lock:
            # Check for existing running tasks
            has_active = any(
                t.task_type == task_type
                and t.resource_id == resource_id
                and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
                for t in self._tasks.values()
            )
            if has_active:
                return None
            # Create atomically within same lock
            task = TaskRecord(
                task_id=str(uuid4()),
                task_type=task_type,
                resource_id=resource_id,
            )
            self._tasks[task.task_id] = task
        logger.debug(
            "[TaskTracker] Created task %s type=%s resource=%s",
            task.task_id,
            task_type,
            resource_id,
        )
        return self._copy(task)

    def start(self, task_id: str) -> None:
        """Transition task to RUNNING."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.RUNNING
                task.updated_at = time.time()

    def complete(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        """Transition task to COMPLETED with optional result."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.COMPLETED
                task.result = result
                task.updated_at = time.time()
        logger.info("[TaskTracker] Task %s completed", task_id)

    def fail(self, task_id: str, error: str) -> None:
        """Transition task to FAILED with sanitized error."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.error = _sanitize_error(error)
                task.updated_at = time.time()
        logger.warning("[TaskTracker] Task %s failed: %s", task_id, _sanitize_error(error))

    def get(self, task_id: str) -> Optional[TaskRecord]:
        """Look up a single task. Returns a snapshot copy (None if not found)."""
        with self._lock:
            task = self._tasks.get(task_id)
            return self._copy(task) if task else None

    def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[TaskRecord]:
        """List tasks with optional filters. Most-recent first. Returns snapshot copies."""
        with self._lock:
            tasks = [self._copy(t) for t in self._tasks.values()]
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        if resource_id:
            tasks = [t for t in tasks if t.resource_id == resource_id]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def has_running(self, task_type: str, resource_id: str) -> bool:
        """Check if there is already a running task for the given type+resource."""
        with self._lock:
            return any(
                t.task_type == task_type
                and t.resource_id == resource_id
                and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
                for t in self._tasks.values()
            )

    @staticmethod
    def _copy(task: TaskRecord) -> TaskRecord:
        """Return a defensive copy of a TaskRecord."""
        return deepcopy(task)

    def count(self) -> int:
        """Return total task count."""
        with self._lock:
            return len(self._tasks)
