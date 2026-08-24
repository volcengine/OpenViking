# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task/work storage contract and local file-backed implementations.

``TaskTracker`` depends only on ``TaskWorkStore``. ``PersistentTaskStore`` keeps
each task and its work records in one owner-scoped JSON file;
``CachingTaskWorkStore`` adds the process-local lookup and statistics cache.

The file store has no compare-and-swap or global owner index. TaskTracker must
serialize local writes, and ownerless reads are cache-only. Older task files
without ``works`` or ``version`` remain readable; queued work is restored from
QueueFS snapshots during startup.
"""

from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Protocol

from openviking.pyagfs import AGFSSyncClientProtocol, AsyncAGFSClient
from openviking.pyagfs.exceptions import AGFSAlreadyExistsError, AGFSNotFoundError
from openviking.service.task_domain import (
    ACTIVE_TASK_STATUSES,
    TaskAggregate,
    TaskRecord,
    TaskStatus,
    TaskWorkRejected,
    WorkRecord,
    WorkState,
)
from openviking.utils.async_utils import run_to_completion
from openviking_cli.utils.logger import get_logger

SYSTEM_TASK_ACCOUNT_ID = "_system"
SYSTEM_TASK_USER_ID = "root"
logger = get_logger(__name__)

TaskStatsSnapshot = Dict[str, Dict[str, int]]


class TaskWorkStore(Protocol):
    """Storage operations required by ``TaskTracker``.

    Implementations own persistence concurrency and may store a task and its
    works together or separately.
    """

    # ── Task writes ──
    async def create(self, task: TaskRecord) -> None: ...

    async def create_if_no_active(self, task: TaskRecord) -> bool:
        """Atomically create unless an active task has the same business key."""
        ...

    async def update(self, task: TaskRecord) -> bool: ...

    # ── Task reads ──
    async def get(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskAggregate]: ...

    async def list(
        self,
        account_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[TaskAggregate]: ...

    async def delete(self, task_id: str, *, account_id: str, user_id: str) -> None: ...

    async def cleanup(self) -> None:
        """Run storage-owned retention and cache maintenance."""
        ...

    def snapshot_task_stats(self) -> TaskStatsSnapshot:
        """Return a store-owned point-in-time task stats snapshot."""
        ...

    # ── Work writes ──
    async def add_work(self, work: WorkRecord, *, account_id: str, user_id: str) -> bool: ...

    async def restore_work(self, work: WorkRecord, *, account_id: str, user_id: str) -> None:
        """Restore missing/open transport work during startup recovery.

        Terminal work is preserved so a stale redelivery can skip its handler
        and retry only the physical QueueFS acknowledgement.
        """
        ...

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        """Delete work that never reached the durable queue."""
        ...

    async def mark_work_done(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None: ...

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None: ...

    async def mark_work_failed(
        self,
        task_id: str,
        work_id: str,
        error: Optional[str] = None,
        *,
        account_id: str,
        user_id: str,
    ) -> None: ...

    async def mark_work_requeued(
        self, task_id: str, work_id: str, *, delta: int = 1, account_id: str, user_id: str
    ) -> None: ...

    async def record_work_error(
        self,
        task_id: str,
        work_id: str,
        error: str,
        *,
        account_id: str,
        user_id: str,
    ) -> None: ...

    async def list_open_works(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> List[WorkRecord]: ...

    async def clear_works(self, task_id: str, *, account_id: str, user_id: str) -> None: ...

    # ── Cancel-state reads ──
    async def list_cancelling_tasks(self) -> set[tuple[str, str, str]]:
        """Return ``(account_id, user_id, task_id)`` keys in ``cancelling``.

        Polled by TaskTracker, which intersects the result with
        the asyncio handles it locally owns and cancels them. Scope depends on
        the implementation: the caching store reports cancelling tasks *in its
        cache* (sufficient in one process, since a just-cancelled task is always
        cached); a distributed store reports the authoritative full set; a bare
        file store returns empty because it has no cheap global scan.
        """
        ...

    def is_cancelling(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Synchronous cancellation view for the queue-worker hot path."""
        ...


class PersistentTaskStore(TaskWorkStore):
    """Persist task aggregates as per-owner JSON blobs under the AGFS root."""

    ROOT_PREFIX = "/local"
    SYSTEM_DIRNAME = "_system"
    TASKS_DIRNAME = "tasks"

    def __init__(self, agfs: AGFSSyncClientProtocol | AsyncAGFSClient) -> None:
        self._agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)

    async def cleanup(self) -> None:
        """No-op because retention requires the cache's global task view."""

    def snapshot_task_stats(self) -> TaskStatsSnapshot:
        """A bare file store has no synchronous global stats view."""
        return {}

    # ── Task reads ──

    async def get(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskAggregate]:
        if not account_id or not user_id:
            return None
        path = self._task_path(account_id, user_id, task_id)
        try:
            raw = await self._agfs.read(path)
        except (AGFSNotFoundError, FileNotFoundError):
            return None
        return _aggregate_from_payload(
            json.loads(_decode_bytes(raw)),
            account_id=account_id,
            user_id=user_id,
        )

    async def list(
        self,
        account_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[TaskAggregate]:
        if not account_id or not user_id:
            return []
        directory = self._task_dir(account_id, user_id)
        try:
            items = await self._agfs.ls(directory)
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        aggregates: List[TaskAggregate] = []
        for item in items:
            path = item.get("path") or f"{directory}/{item.get('name', '')}"
            if not path.endswith(".json"):
                continue
            try:
                raw = await self._agfs.read(path)
            except (AGFSNotFoundError, FileNotFoundError):
                continue
            aggregates.append(
                _aggregate_from_payload(
                    json.loads(_decode_bytes(raw)),
                    account_id=account_id,
                    user_id=user_id,
                )
            )
        return _filter_aggregates(
            aggregates,
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
        )

    # ── Task writes ──

    async def create(self, task: TaskRecord) -> None:
        await self._write_aggregate(TaskAggregate(task=task))

    async def create_if_no_active(self, task: TaskRecord) -> bool:
        if not task.account_id or not task.user_id or not task.resource_id:
            raise ValueError("create_if_no_active requires task owner and resource_id")
        aggregates = await self.list(task.account_id, user_id=task.user_id)
        if any(
            aggregate.task.task_type == task.task_type
            and aggregate.task.resource_id == task.resource_id
            and aggregate.task.status in ACTIVE_TASK_STATUSES
            for aggregate in aggregates
        ):
            return False
        await self._write_aggregate(TaskAggregate(task=task))
        return True

    async def update(self, task: TaskRecord) -> bool:
        """Unconditional overwrite (no CAS on a file system); always True.

        Preserves works already persisted for this task and bumps the recorded
        version. Per-task serialization upstream prevents lost updates.
        """
        works = await self._load_works(task.account_id, task.user_id, task.task_id)
        task.version += 1
        await self._write_aggregate(TaskAggregate(task=task, works=works))
        return True

    async def delete(self, task_id: str, *, account_id: str, user_id: str) -> None:
        if not user_id:
            return
        await self._agfs.rm(self._task_path(account_id, user_id, task_id), force=True)

    # ── Work writes (embedded in the task blob) ──

    async def add_work(self, work: WorkRecord, *, account_id: str, user_id: str) -> bool:
        agg = await self.get(work.task_id, account_id=account_id, user_id=user_id)
        if agg is None:
            return False
        if agg.task.is_terminal() or agg.task.status == TaskStatus.CANCELLING:
            raise TaskWorkRejected(
                f"Task {work.task_id} is {agg.task.status.value}; rejected work {work.work_id}"
            )
        if work.work_id in agg.works:
            return True
        agg.works[work.work_id] = work
        agg.task.version += 1
        await self._write_aggregate(agg)
        return True

    async def restore_work(self, work: WorkRecord, *, account_id: str, user_id: str) -> None:
        agg = await self.get(work.task_id, account_id=account_id, user_id=user_id)
        if agg is None:
            return
        existing = agg.works.get(work.work_id)
        if existing is not None and not existing.is_open():
            return
        agg.restore_work(work.work_id, work.queue_name)
        agg.reopen_after_work_restore()
        agg.task.version += 1
        await self._write_aggregate(agg)

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        agg = await self.get(task_id, account_id=account_id, user_id=user_id)
        if agg is None:
            return
        agg.works.pop(work_id, None)
        agg.task.version += 1
        await self._write_aggregate(agg)

    async def mark_work_done(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._mutate_work(
            task_id, account_id, user_id, lambda agg: agg.mark_work_done(work_id)
        )

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._mutate_work(task_id, account_id, user_id, lambda agg: agg.start_work(work_id))

    async def mark_work_failed(
        self,
        task_id: str,
        work_id: str,
        error: Optional[str] = None,
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        await self._mutate_work(
            task_id, account_id, user_id, lambda agg: agg.mark_work_failed(work_id, error)
        )

    async def mark_work_requeued(
        self, task_id: str, work_id: str, *, delta: int = 1, account_id: str, user_id: str
    ) -> None:
        await self._mutate_work(
            task_id, account_id, user_id, lambda agg: agg.mark_work_requeued(work_id, delta)
        )

    async def record_work_error(
        self,
        task_id: str,
        work_id: str,
        error: str,
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        await self._mutate_work(
            task_id, account_id, user_id, lambda agg: agg.record_work_error(work_id, error)
        )

    async def list_open_works(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> List[WorkRecord]:
        agg = await self.get(task_id, account_id=account_id, user_id=user_id)
        if agg is None:
            return []
        return [w for w in agg.works.values() if w.is_open()]

    async def clear_works(self, task_id: str, *, account_id: str, user_id: str) -> None:
        agg = await self.get(task_id, account_id=account_id, user_id=user_id)
        if agg is None or not agg.works:
            return
        agg.works = {}
        agg.task.version += 1
        await self._write_aggregate(agg)

    async def list_cancelling_tasks(self) -> set[tuple[str, str, str]]:
        # Cancellation polling needs a global task view, which this store lacks.
        return set()

    def is_cancelling(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        # Synchronous cancellation checks are provided by the cache wrapper.
        return False

    # ── Internal helpers ──

    async def _mutate_work(
        self,
        task_id: str,
        account_id: str,
        user_id: str,
        mutate: Callable[[TaskAggregate], None],
    ) -> None:
        agg = await self.get(task_id, account_id=account_id, user_id=user_id)
        if agg is None:
            return
        mutate(agg)
        # Work changes advance the aggregate revision so stale file reads cannot
        # replace newer cached work with an equal-version snapshot.
        agg.task.version += 1
        await self._write_aggregate(agg)

    async def _load_works(
        self, account_id: Optional[str], user_id: Optional[str], task_id: str
    ) -> Dict[str, WorkRecord]:
        if not account_id or not user_id:
            return {}
        agg = await self.get(task_id, account_id=account_id, user_id=user_id)
        return agg.works if agg is not None else {}

    async def _write_aggregate(self, agg: TaskAggregate) -> None:
        account_id = agg.task.account_id
        user_id = agg.task.user_id
        if not account_id or not user_id:
            raise ValueError("PersistentTaskStore requires account_id and user_id")
        await self._ensure_task_dir(account_id, user_id)
        await self._agfs.write(
            self._task_path(account_id, user_id, agg.task.task_id),
            json.dumps(_aggregate_to_payload(agg), ensure_ascii=False).encode("utf-8"),
        )

    async def _ensure_task_dir(self, account_id: str, user_id: str) -> None:
        await self._mkdir_if_missing(self._account_dir(account_id))
        await self._mkdir_if_missing(self._system_dir(account_id))
        await self._mkdir_if_missing(self._task_root_dir(account_id))
        await self._mkdir_if_missing(self._task_dir(account_id, user_id))

    async def _mkdir_if_missing(self, path: str) -> None:
        try:
            await self._agfs.mkdir(path)
        except AGFSAlreadyExistsError:
            return
        except Exception as exc:
            if "already exists" in str(exc).lower():
                return
            raise

    def _account_dir(self, account_id: str) -> str:
        return f"{self.ROOT_PREFIX}/{account_id}"

    def _system_dir(self, account_id: str) -> str:
        if account_id == SYSTEM_TASK_ACCOUNT_ID:
            return self._account_dir(account_id)
        return f"{self._account_dir(account_id)}/{self.SYSTEM_DIRNAME}"

    def _task_root_dir(self, account_id: str) -> str:
        return f"{self._system_dir(account_id)}/{self.TASKS_DIRNAME}"

    def _task_dir(self, account_id: str, user_id: str) -> str:
        return f"{self._task_root_dir(account_id)}/{user_id}"

    def _task_path(self, account_id: str, user_id: str, task_id: str) -> str:
        return f"{self._task_dir(account_id, user_id)}/{task_id}.json"


class CachingTaskWorkStore(TaskWorkStore):
    """Process-local cache for the file store.

    It serves ownerless lookups, task statistics, cancellation polling, and
    retention without exposing cache internals to TaskTracker. Distributed
    stores should implement those operations directly instead of using this
    wrapper. Writes reach the inner store before the cache.
    """

    MAX_TASKS = 10_000
    TTL_COMPLETED = 86_400
    TTL_FAILED = 604_800

    def __init__(self, inner: "TaskWorkStore") -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._cache: Dict[tuple[str, str, str], TaskAggregate] = {}

    async def cleanup(self) -> None:
        """Delete expired terminal records and cap only the local cache."""
        snapshots = self._cached_snapshot()
        now = time.time()
        for aggregate in snapshots:
            task = aggregate.task
            age = now - task.updated_at
            expired = (
                task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
                and age > self.TTL_COMPLETED
            ) or (task.status == TaskStatus.FAILED and age > self.TTL_FAILED)
            if not expired or not task.account_id or not task.user_id:
                continue
            try:
                await self._inner.delete(
                    task.task_id, account_id=task.account_id, user_id=task.user_id
                )
            except Exception:
                logger.warning(
                    "[CachingTaskWorkStore] Failed to delete expired task %s",
                    task.task_id,
                    exc_info=True,
                )
                continue
            self._invalidate(task.task_id, account_id=task.account_id, user_id=task.user_id)

        with self._lock:
            remaining = sorted(self._cache.items(), key=lambda item: item[1].task.created_at)
            for key, _aggregate in remaining[: max(0, len(remaining) - self.MAX_TASKS)]:
                self._cache.pop(key, None)

    def snapshot_task_stats(self) -> TaskStatsSnapshot:
        from collections import defaultdict

        grouped: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for aggregate in self._cached_snapshot():
            grouped[aggregate.task.task_type][aggregate.task.status.value] += 1
        return {task_type: dict(statuses) for task_type, statuses in grouped.items()}

    # ── Task reads ──

    async def get(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskAggregate]:
        cached = self._cached(task_id, account_id, user_id)
        if cached is not None:
            return cached
        if not account_id or not user_id:
            return None
        agg = await self._inner.get(task_id, account_id=account_id, user_id=user_id)
        if agg is not None:
            self._put(agg)
        return _copy_aggregate(agg) if agg is not None else None

    async def list(
        self,
        account_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[TaskAggregate]:
        # Incomplete owner filters must not trigger an unscoped file-system scan.
        if account_id and user_id:
            aggregates = await self._inner.list(account_id, user_id=user_id)
            for aggregate in aggregates:
                self._put(aggregate)
        return _filter_aggregates(
            self._cached_matching(account_id=account_id, user_id=user_id),
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
        )

    # ── Task writes (write-through: inner first, then cache) ──

    async def create(self, task: TaskRecord) -> None:
        async def write_through() -> None:
            await self._inner.create(task)
            self._put(TaskAggregate(task=task))

        await run_to_completion(write_through)

    async def create_if_no_active(self, task: TaskRecord) -> bool:
        async def write_through() -> bool:
            created = await self._inner.create_if_no_active(task)
            if created:
                self._put(TaskAggregate(task=task))
            return created

        return await run_to_completion(write_through)

    async def update(self, task: TaskRecord) -> bool:
        async def write_through() -> bool:
            ok = await self._inner.update(task)
            if not ok:
                self._invalidate(task.task_id, account_id=task.account_id, user_id=task.user_id)
                return False
            works = self._cached_works(task.task_id, task.account_id, task.user_id)
            self._put(TaskAggregate(task=task, works=works))
            return True

        return await run_to_completion(write_through)

    async def delete(self, task_id: str, *, account_id: str, user_id: str) -> None:
        async def write_through() -> None:
            await self._inner.delete(task_id, account_id=account_id, user_id=user_id)
            self._invalidate(task_id, account_id=account_id, user_id=user_id)

        await run_to_completion(write_through)

    # ── Work writes ──

    async def add_work(self, work: WorkRecord, *, account_id: str, user_id: str) -> bool:
        added = await self._inner.add_work(work, account_id=account_id, user_id=user_id)
        if not added:
            return False
        await self._refresh(work.task_id, account_id, user_id)
        return True

    async def restore_work(self, work: WorkRecord, *, account_id: str, user_id: str) -> None:
        await self._inner.restore_work(work, account_id=account_id, user_id=user_id)
        await self._refresh(work.task_id, account_id, user_id)

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._inner.discard_work(task_id, work_id, account_id=account_id, user_id=user_id)
        await self._refresh(task_id, account_id, user_id)

    async def mark_work_done(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._inner.mark_work_done(task_id, work_id, account_id=account_id, user_id=user_id)
        await self._refresh(task_id, account_id, user_id)

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._inner.start_work(task_id, work_id, account_id=account_id, user_id=user_id)
        await self._refresh(task_id, account_id, user_id)

    async def mark_work_failed(
        self,
        task_id: str,
        work_id: str,
        error: Optional[str] = None,
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        await self._inner.mark_work_failed(
            task_id, work_id, error, account_id=account_id, user_id=user_id
        )
        await self._refresh(task_id, account_id, user_id)

    async def mark_work_requeued(
        self, task_id: str, work_id: str, *, delta: int = 1, account_id: str, user_id: str
    ) -> None:
        await self._inner.mark_work_requeued(
            task_id, work_id, delta=delta, account_id=account_id, user_id=user_id
        )
        await self._refresh(task_id, account_id, user_id)

    async def record_work_error(
        self,
        task_id: str,
        work_id: str,
        error: str,
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        await self._inner.record_work_error(
            task_id, work_id, error, account_id=account_id, user_id=user_id
        )
        await self._refresh(task_id, account_id, user_id)

    async def list_open_works(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> List[WorkRecord]:
        cached = self._cached(task_id, account_id, user_id)
        if cached is not None:
            return [w for w in cached.works.values() if w.is_open()]
        return await self._inner.list_open_works(task_id, account_id=account_id, user_id=user_id)

    async def clear_works(self, task_id: str, *, account_id: str, user_id: str) -> None:
        await self._inner.clear_works(task_id, account_id=account_id, user_id=user_id)
        await self._refresh(task_id, account_id, user_id)

    async def list_cancelling_tasks(self) -> set[tuple[str, str, str]]:
        """Return cancelling tasks known to this process."""
        return self.cancelling_tasks_sync()

    def cancelling_tasks_sync(self) -> set[tuple[str, str, str]]:
        """Return cached cancellation keys without performing I/O."""
        with self._lock:
            return {
                task_key
                for task_key, agg in self._cache.items()
                if agg.task.status == TaskStatus.CANCELLING
            }

    def is_cancelling(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        aggregate = self._cached(task_id, account_id, user_id)
        return bool(aggregate and aggregate.task.status == TaskStatus.CANCELLING)

    # ── Cache internals ──

    def _invalidate(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        with self._lock:
            self._cache.pop((account_id, user_id, task_id), None)

    def _replace_cached_for_test(self, aggregate: TaskAggregate) -> None:
        """Seed a cache entry for focused store tests."""
        key = _aggregate_key(aggregate)
        with self._lock:
            self._cache[key] = _copy_aggregate(aggregate)

    def _cached_snapshot(self) -> List[TaskAggregate]:
        """Defensive internal snapshot used by cleanup and stats."""
        with self._lock:
            aggregates = list(self._cache.values())
        return [_copy_aggregate(agg) for agg in aggregates]

    # ── Internal ──

    async def _refresh(self, task_id: str, account_id: str, user_id: str) -> None:
        """Reload the aggregate from the inner store after a work mutation."""
        agg = await self._inner.get(task_id, account_id=account_id, user_id=user_id)
        if agg is None:
            self._invalidate(task_id, account_id=account_id, user_id=user_id)
        else:
            self._put(agg)

    def _cached(
        self,
        task_id: str,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[TaskAggregate]:
        with self._lock:
            if account_id and user_id:
                agg = self._cache.get((account_id, user_id, task_id))
            else:
                matches = [
                    aggregate
                    for (
                        cached_account,
                        cached_user,
                        cached_task_id,
                    ), aggregate in self._cache.items()
                    if cached_task_id == task_id
                    and (account_id is None or cached_account == account_id)
                    and (user_id is None or cached_user == user_id)
                ]
                agg = max(
                    matches,
                    key=lambda aggregate: (
                        aggregate.task.updated_at,
                        aggregate.task.version,
                    ),
                    default=None,
                )
        return _copy_aggregate(agg) if agg is not None else None

    def _cached_matching(
        self,
        *,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> List[TaskAggregate]:
        with self._lock:
            aggregates = [
                aggregate
                for (cached_account, cached_user, _task_id), aggregate in self._cache.items()
                if (account_id is None or cached_account == account_id)
                and (user_id is None or cached_user == user_id)
            ]
        return [_copy_aggregate(aggregate) for aggregate in aggregates]

    def _cached_works(
        self, task_id: str, account_id: Optional[str], user_id: Optional[str]
    ) -> Dict[str, WorkRecord]:
        if not account_id or not user_id:
            return {}
        with self._lock:
            agg = self._cache.get((account_id, user_id, task_id))
        return deepcopy(agg.works) if agg is not None else {}

    def _put(self, agg: TaskAggregate) -> None:
        stored = _copy_aggregate(agg)
        key = _aggregate_key(stored)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                incoming_key = (stored.task.version, stored.task.updated_at)
                cached_key = (cached.task.version, cached.task.updated_at)
                if incoming_key < cached_key:
                    return
            self._cache[key] = stored


def _aggregate_key(aggregate: TaskAggregate) -> tuple[str, str, str]:
    task = aggregate.task
    if not task.account_id or not task.user_id:
        raise ValueError("Cached task aggregate requires account_id and user_id")
    return task.account_id, task.user_id, task.task_id


def _filter_aggregates(
    aggregates: List[TaskAggregate],
    *,
    task_type: Optional[str],
    status: Optional[str],
    resource_id: Optional[str],
    limit: Optional[int],
) -> List[TaskAggregate]:
    filtered = aggregates
    if task_type:
        filtered = [aggregate for aggregate in filtered if aggregate.task.task_type == task_type]
    if status:
        filtered = [aggregate for aggregate in filtered if aggregate.task.status.value == status]
    if resource_id:
        filtered = [
            aggregate for aggregate in filtered if aggregate.task.resource_id == resource_id
        ]
    filtered.sort(key=lambda aggregate: aggregate.task.created_at, reverse=True)
    if limit is not None:
        filtered = filtered[:limit]
    return [_copy_aggregate(aggregate) for aggregate in filtered]


def _copy_aggregate(agg: TaskAggregate) -> TaskAggregate:
    return TaskAggregate(task=deepcopy(agg.task), works=deepcopy(agg.works))


def _aggregate_to_payload(agg: TaskAggregate) -> Dict[str, Any]:
    task = agg.task
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status.value,
        "version": task.version,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "resource_id": task.resource_id,
        "account_id": task.account_id,
        "user_id": task.user_id,
        "meta": deepcopy(task.meta),
        "stage": task.stage,
        "result": deepcopy(task.result),
        "error": task.error,
        "auth": deepcopy(task.auth),
        "works": [_work_to_payload(w) for w in agg.works.values()],
    }


def _work_to_payload(work: WorkRecord) -> Dict[str, Any]:
    return {
        "work_id": work.work_id,
        "task_id": work.task_id,
        "queue_name": work.queue_name,
        "state": work.state.value,
        "requeue_count": work.requeue_count,
        "error": work.error,
        "updated_at": work.updated_at,
    }


def _aggregate_from_payload(
    payload: Dict[str, Any],
    *,
    account_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> TaskAggregate:
    data = dict(payload)
    works_payload = data.pop("works", None) or []
    data["status"] = TaskStatus(data["status"])
    data.setdefault("version", 0)
    # Older task blobs may not carry ownership. The file path is already
    # owner-scoped, so use the requested path owner as the authoritative value.
    if account_id is not None:
        data["account_id"] = account_id
    if user_id is not None:
        data["user_id"] = user_id
    task = TaskRecord(**{k: v for k, v in data.items() if k in _TASK_FIELDS})
    works: Dict[str, WorkRecord] = {}
    for wp in works_payload:
        work = _work_from_payload(wp)
        works[work.work_id] = work
    return TaskAggregate(task=task, works=works)


def _work_from_payload(payload: Dict[str, Any]) -> WorkRecord:
    data = dict(payload)
    data["state"] = WorkState(data["state"])
    return WorkRecord(**{k: v for k, v in data.items() if k in _WORK_FIELDS})


_TASK_FIELDS = {
    "task_id",
    "task_type",
    "status",
    "version",
    "created_at",
    "updated_at",
    "resource_id",
    "account_id",
    "user_id",
    "meta",
    "stage",
    "result",
    "error",
    "auth",
}

_WORK_FIELDS = {
    "work_id",
    "task_id",
    "queue_name",
    "state",
    "requeue_count",
    "error",
    "updated_at",
}


def _decode_bytes(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)
