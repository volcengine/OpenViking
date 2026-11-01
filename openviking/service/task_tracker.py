# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Coordinate Task lifecycle, queue Work, waiting, and cancellation."""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Collection,
    Dict,
    Generator,
    List,
    Optional,
    TypeVar,
    cast,
)
from uuid import uuid4

from openviking.service.task_domain import (
    ACTIVE_TASK_STATUSES,
    MAX_QUEUE_ERROR_DETAILS,
    SENSITIVE_TASK_KEYS,
    TERMINAL_TASK_STATUSES,
    TERMINAL_WORK_STATES,
    DeleteExpiredResult,
    QueueWorkSummary,
    TaskPatch,
    TaskRecord,
    TaskRetentionPolicy,
    TaskStatus,
    TaskWorkRejected,
    WorkRecord,
    WorkRecoveryMode,
    WorkState,
    WorkSummary,
    sanitize_error,
    sanitize_task_result,
)
from openviking.service.task_events import PROCESS_EVENT_KINDS
from openviking.service.task_store import (
    SYSTEM_TASK_ACCOUNT_ID,
    SYSTEM_TASK_USER_ID,
    StoreOperationCode,
    StoreOperationResult,
    TaskAlreadyActive,
    TaskCreationResult,
    TaskWorkStore,
    WorkRegistration,
)
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.task_work_index import TaskWorkIndex
from openviking_cli.exceptions import (
    DeadlineExceededError,
    FailedPreconditionError,
    OpenVikingError,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
_OperationResult = TypeVar("_OperationResult")

__all__ = [
    "SENSITIVE_TASK_KEYS",
    "TaskAlreadyActive",
    "TaskRecord",
    "TaskStatus",
    "TaskTracker",
    "get_task_tracker",
    "run_tracked_operation",
    "set_task_tracker",
]

_CANCELLABLE_TASK_TYPES = {
    "add_resource",
    "add_skill",
    "compile",
    "rule_compile",
    "session_commit",
    "admin_reindex",
    "snapshot_restore_reindex",
}
_IDENTITY_CLEANUP_TASK_TYPES = {"account_delete", "user_delete"}
_CANCEL_POLL_INTERVAL = 30
_WAIT_INITIAL_POLL_INTERVAL = 0.05
_WAIT_MAX_POLL_INTERVAL = 10.0
_PUBLIC_QUEUE_NAMES = ("Semantic", "Embedding")

_sanitize_error = sanitize_error
_sanitize_task_result = sanitize_task_result


@dataclass
class _WaitBudget:
    task_id: str
    remaining: float


_wait_budget: ContextVar[Optional[_WaitBudget]] = ContextVar("task_wait_budget", default=None)


_admission: ContextVar[Optional[tuple[str, Callable[[], Awaitable[bool]]]]] = ContextVar(
    "task_admission", default=None
)


@contextmanager
def task_admission(
    task_id: str, admitted: Callable[[], Awaitable[bool]]
) -> Generator[None, None, None]:
    """Create *task_id* within this context only while *admitted* holds.

    create checks before the insert and again after it. A confirmed revocation
    withdraws an unused Task or cancels one that another caller has taken over.
    A failed check or cancelled caller only withdraws an unused Task.
    """
    token = _admission.set((task_id, admitted))
    try:
        yield
    finally:
        _admission.reset(token)


@contextmanager
def _consume_wait_budget(
    task_id: str, timeout: Optional[float]
) -> Generator[Optional[float], None, None]:
    """Charge only time spent waiting, shared across an inline operation."""
    budget = _wait_budget.get()
    if budget is None or budget.task_id != task_id:
        yield timeout
        return
    allowed = budget.remaining if timeout is None else min(timeout, budget.remaining)
    started = time.monotonic()
    try:
        yield max(0.0, allowed)
    finally:
        budget.remaining = max(0.0, budget.remaining - (time.monotonic() - started))


_instance: Optional["TaskTracker"] = None
_init_lock = threading.Lock()


def get_task_tracker() -> "TaskTracker":
    """Return the tracker installed during service storage initialization."""
    with _init_lock:
        if _instance is None:
            raise RuntimeError(
                "TaskTracker not initialized. OpenVikingService must install the shared "
                "tracker with set_task_tracker() during storage initialization."
            )
        return _instance


def set_task_tracker(tracker: Optional["TaskTracker"]) -> None:
    global _instance
    with _init_lock:
        _instance = tracker


async def run_tracked_operation(
    task_type: str,
    *,
    account_id: str,
    user_id: str,
    operation: Callable[[], Awaitable[_OperationResult]],
    wait: bool,
    timeout: Optional[float] = None,
    resource_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    existing_task: Optional[TaskRecord] = None,
    accept_failed_result: Callable[[_OperationResult], bool] | None = None,
    return_terminal_result: bool = False,
) -> tuple[_OperationResult, str]:
    """Run a top-level operation under a Task, reusing an existing Task context.

    ``meta`` applies only to a newly created Task; reused Tasks keep their metadata.
    ``accept_failed_result`` is reserved for operations whose result explicitly
    reports record-level failures while the Task retains its failed status.
    ``return_terminal_result`` returns the Task's final projected result after
    waiting instead of the operation's pre-Work result.
    """
    from openviking.service.task_work_index import bind_task_context, get_task_context

    current = get_task_context()
    if current is not None:
        return await operation(), current.task_id

    tracker = get_task_tracker()
    created_task = existing_task

    async def create_task() -> None:
        nonlocal created_task
        created_task = await tracker.create(
            task_type,
            resource_id=resource_id,
            account_id=account_id,
            user_id=user_id,
            meta=meta,
        )

    try:
        if created_task is None:
            await run_to_completion(create_task)
        assert created_task is not None
        task = created_task
        await run_to_completion(
            lambda: tracker.start(task.task_id, account_id=account_id, user_id=user_id)
        )
    except BaseException as exc:
        if created_task is not None:
            failure_message = str(exc) or type(exc).__name__
            await run_to_completion(
                lambda: tracker.fail(
                    created_task.task_id,
                    failure_message,
                    account_id=account_id,
                    user_id=user_id,
                )
            )
        raise

    async def execute_operation() -> _OperationResult:
        running_work_id = await tracker.register_running_task(
            task.task_id,
            account_id=account_id,
            user_id=user_id,
        )
        try:
            with bind_task_context(
                task.task_id,
                account_id,
                user_id,
                enqueued_work=[],
                work_id=running_work_id,
            ):
                result = await operation()
            task_result = deepcopy(result) if isinstance(result, dict) else {"result": result}
            task_result.setdefault("task_id", task.task_id)
            await tracker.complete(
                task.task_id,
                task_result,
                account_id=account_id,
                user_id=user_id,
                resource_id=resource_id,
            )
            if isinstance(result, dict) and not wait:
                result.setdefault("task_id", task.task_id)
            return result
        except BaseException as exc:
            failure_message = str(exc) or type(exc).__name__
            await run_to_completion(
                lambda: tracker.fail(
                    task.task_id,
                    failure_message,
                    account_id=account_id,
                    user_id=user_id,
                )
            )
            raise
        finally:
            await tracker.unregister_running_task(task.task_id)

    token = _wait_budget.set(
        _WaitBudget(task.task_id, timeout) if wait and timeout is not None else None
    )
    try:
        result = await execute_operation()
        if wait:
            try:
                terminal = await tracker.wait(
                    task.task_id,
                    account_id=account_id,
                    user_id=user_id,
                    timeout=timeout,
                )
                if return_terminal_result and terminal.result is not None:
                    result = cast(_OperationResult, deepcopy(terminal.result))
                    if isinstance(result, dict):
                        result.pop("task_id", None)
                if terminal.status == TaskStatus.FAILED and not (
                    accept_failed_result is not None and accept_failed_result(result)
                ):
                    raise OpenVikingError(
                        terminal.error or "Task processing failed",
                        code="PROCESSING_ERROR",
                        details={"task_id": task.task_id},
                    )
                if terminal.status == TaskStatus.CANCELLED:
                    raise OpenVikingError(
                        "Task processing was cancelled",
                        code="PROCESSING_ERROR",
                        details={"task_id": task.task_id, "status": "cancelled"},
                    )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise DeadlineExceededError("queue processing", timeout) from exc
        return result, task.task_id
    finally:
        _wait_budget.reset(token)


class TaskTracker:
    """Thin coordinator over the semantic :class:`TaskWorkStore` contract."""

    TTL_COMPLETED = 86_400
    TTL_FAILED = 604_800
    CLEANUP_INTERVAL = 300
    CLEANUP_BATCH = 256

    def __init__(self, store: TaskWorkStore) -> None:
        self._store = store
        self._cleanup_task: Optional[asyncio.Task[Any]] = None
        # Bound after Compile storage initialization; shares the cleanup clock and batch size.
        self.compile_history_cleanup: Optional[Callable[..., Awaitable[int]]] = None
        self._cancel_poll_task: Optional[asyncio.Task[Any]] = None
        self._work_index = TaskWorkIndex()
        self._running: ContextVar[tuple[str, str, str, bool] | None] = ContextVar(
            "task_tracker_running_work", default=None
        )
        self._deletion_guard: Optional[Callable[[str, str], Any]] = None

    @staticmethod
    def _validate_owner(account_id: str, user_id: str) -> None:
        if not account_id or not user_id:
            raise ValueError("Task ownership requires non-empty account_id and user_id")

    def set_deletion_guard(self, guard: Optional[Callable[[str, str], Any]]) -> None:
        """Reject new Tasks for identities the guard reports as being deleted.

        Identity cleanup records its fence and then lists the Tasks to cancel;
        create inserts the Task and then reads the fence again. One of the two
        reads observes the other's write, so a Task inserted concurrently with
        a deletion is either listed by cleanup or withdrawn/cancelled here.
        """
        self._deletion_guard = guard

    async def _owner_deleting(self, account_id: str, user_id: str) -> bool:
        guard = self._deletion_guard
        if guard is None or account_id == SYSTEM_TASK_ACCOUNT_ID:
            return False
        result = guard(account_id, user_id)
        return bool(await result if inspect.isawaitable(result) else result)

    async def _withdraw(
        self, task_id: str, *, account_id: str, user_id: str, refusal: Optional[str]
    ) -> None:
        withdrawn = await self._store.withdraw_unaccepted(
            task_id, account_id=account_id, user_id=user_id
        )
        if withdrawn.is_applied:
            self._work_index.forget_task(task_id, account_id=account_id, user_id=user_id)
            return
        if withdrawn.accepted or refusal is None:
            return
        cancellation = await self._request_cancellation(
            task_id,
            account_id=account_id,
            user_id=user_id,
            allow_terminal=False,
        )
        if cancellation.accepted:
            self._work_index.cancel_active(task_id, account_id=account_id, user_id=user_id)
        elif cancellation.code is StoreOperationCode.NOT_CANCELLABLE:
            await self.fail(task_id, refusal, account_id=account_id, user_id=user_id)

    def attach_work_index(self, work_index: TaskWorkIndex) -> None:
        """Replace the process-local active-handle index."""
        self._work_index = work_index

    @property
    def work_recovery_mode(self) -> WorkRecoveryMode:
        return self._store.work_recovery_mode

    async def load(self) -> None:
        """Load backend resources without exposing backend transaction details."""
        loader = getattr(self._store, "load", None)
        if loader is not None:
            result = loader()
            if inspect.isawaitable(result):
                await result

    async def close(self) -> None:
        """Release backend resources owned by the tracker."""
        closer = getattr(self._store, "close", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                await result

    async def restore_work_tasks(self, owners: Dict[str, tuple[str, str]]) -> List[TaskRecord]:
        restored = []
        for task_id, (account_id, user_id) in owners.items():
            task = await self.get(task_id, account_id=account_id, user_id=user_id)
            if task is not None:
                restored.append(task)
        return restored

    def start_cleanup_loop(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        if self._cancel_poll_task is None or self._cancel_poll_task.done():
            self._cancel_poll_task = asyncio.create_task(self._cancel_poll_loop())

    def stop_cleanup_loop(self) -> None:
        for task in (self._cleanup_task, self._cancel_poll_task):
            if task is not None and not task.done():
                task.cancel()

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                await self.delete_expired()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[TaskTracker] cleanup error")

    async def _cancel_poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_CANCEL_POLL_INTERVAL)
                for (
                    account_id,
                    user_id,
                ), task_ids in self._work_index.active_tasks_by_owner().items():
                    cancelling = await self._store.filter_cancellation_requested(
                        task_ids,
                        account_id=account_id,
                        user_id=user_id,
                    )
                    for task_id in cancelling:
                        self._work_index.cancel_active(
                            task_id,
                            account_id=account_id,
                            user_id=user_id,
                        )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[TaskTracker] cancel-poll error")

    async def delete_expired(self) -> DeleteExpiredResult:
        now = time.time()
        if self.compile_history_cleanup is not None:
            try:
                await self.compile_history_cleanup(now=now, limit=self.CLEANUP_BATCH)
            except Exception:
                logger.exception("[TaskTracker] Compile history cleanup error")
        result = await self._store.delete_expired(
            TaskRetentionPolicy(self.TTL_COMPLETED, self.TTL_FAILED),
            now=now,
            limit=self.CLEANUP_BATCH,
        )
        for deleted in result.deleted:
            logger.info(
                "[TaskTracker] deleted expired task task_id=%s account_id=%s user_id=%s",
                deleted.task_id,
                deleted.account_id,
                deleted.user_id,
            )
        return result

    async def create(
        self,
        task_type: str,
        resource_id: Optional[str] = None,
        *,
        account_id: str,
        user_id: str,
        task_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        auth: Optional[Dict[str, Any]] = None,
        reject_if_active: bool = False,
    ) -> TaskRecord:
        self._validate_owner(account_id, user_id)
        if task_id is not None:
            existing = await self.get(task_id, account_id=account_id, user_id=user_id)
            if existing is not None:
                return existing
        admission = _admission.get()
        admitted = admission[1] if admission and admission[0] == task_id else None
        # Identity cleanup may be owned by an actor that is itself being deleted;
        # fencing it would strand the target deletion without a cleanup task.
        enforce_identity_admission = task_type not in _IDENTITY_CLEANUP_TASK_TYPES
        if enforce_identity_admission and await self._owner_deleting(account_id, user_id):
            raise FailedPreconditionError("Identity deletion is in progress")
        if admitted is not None and not await admitted():
            raise FailedPreconditionError("Task creation is no longer admitted")
        task = TaskRecord(
            task_id=task_id or str(uuid4()),
            task_type=task_type,
            resource_id=resource_id,
            account_id=account_id,
            user_id=user_id,
            meta=dict(meta or {}),
            auth=dict(auth or {}),
        )
        creation: Optional[TaskCreationResult] = None
        refusal: Optional[str] = None

        async def persist() -> None:
            nonlocal creation
            # Retain the committed result even when the caller cancels during I/O.
            creation = await self._store.create(task, reject_if_active=reject_if_active)

        try:
            await run_to_completion(persist)
            assert creation is not None
            created = self._public(creation.task)
            if not creation.created:
                return created
            if enforce_identity_admission and await self._owner_deleting(account_id, user_id):
                refusal = "Identity deletion is in progress"
                raise FailedPreconditionError(refusal)
            if admitted is not None and not await admitted():
                refusal = "Task creation is no longer admitted"
                raise FailedPreconditionError(refusal)
        except BaseException:
            if creation is not None and creation.created:
                try:
                    await run_to_completion(
                        lambda: self._withdraw(
                            task.task_id,
                            account_id=account_id,
                            user_id=user_id,
                            refusal=refusal,
                        )
                    )
                except BaseException:
                    logger.exception("[TaskTracker] Failed to withdraw Task %s", task.task_id)
            raise
        return created

    async def create_if_no_running(
        self,
        task_type: str,
        resource_id: str,
        *,
        account_id: str,
        user_id: str,
        task_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        """Create an operation atomically, or return None while another is active.

        An explicit task_id replays the same operation; a task with a different
        type or resource raises ValueError. Owner validation belongs to create.
        """
        try:
            task = await self.create(
                task_type,
                resource_id,
                account_id=account_id,
                user_id=user_id,
                task_id=task_id,
                reject_if_active=True,
            )
        except TaskAlreadyActive:
            return None
        if task.task_type != task_type or task.resource_id != resource_id:
            raise ValueError("Task ID belongs to another operation")
        return task

    async def start(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> None:
        await self._store.start_task(task_id, stage=stage, account_id=account_id, user_id=user_id)

    async def patch_task(
        self,
        task_id: str,
        patch: TaskPatch,
        *,
        account_id: str,
        user_id: str,
    ) -> StoreOperationResult:
        """Atomically apply permitted Task fields without changing task state."""
        self._validate_owner(account_id, user_id)
        return await self._store.patch_task(
            task_id,
            patch,
            account_id=account_id,
            user_id=user_id,
        )

    async def complete(
        self,
        task_id: str,
        result: Dict[str, Any],
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        *,
        resource_id: Optional[str] = None,
    ) -> None:
        await self._record_outcome(
            task_id,
            result=result,
            error=None,
            resource_id=resource_id,
            cancelled=False,
            account_id=account_id,
            user_id=user_id,
        )

    async def fail(
        self,
        task_id: str,
        error: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        *,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self._record_outcome(
            task_id,
            result=result,
            error=sanitize_error(error),
            resource_id=None,
            cancelled=False,
            account_id=account_id,
            user_id=user_id,
        )

    async def mark_cancelled(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        await self._record_outcome(
            task_id,
            result=None,
            error=None,
            resource_id=None,
            cancelled=True,
            account_id=account_id,
            user_id=user_id,
        )

    async def record_cancelled(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        await self.mark_cancelled(task_id, account_id, user_id)

    async def _record_outcome(
        self,
        task_id: str,
        *,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        resource_id: Optional[str],
        cancelled: bool,
        account_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        await run_to_completion(
            lambda: self._store.record_outcome(
                task_id,
                result=result,
                error=error,
                resource_id=resource_id,
                cancelled=cancelled,
                account_id=account_id,
                user_id=user_id,
            )
        )

    async def cancel(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> Optional[TaskRecord]:
        self._validate_owner(account_id, user_id)
        found = await self._request_cancellation(
            task_id,
            account_id=account_id,
            user_id=user_id,
            allow_terminal=False,
        )
        if found.code is StoreOperationCode.NOT_FOUND:
            return None
        if not found.accepted:
            raise ValueError(found.message or found.code.value)
        self._work_index.cancel_active(task_id, account_id=account_id, user_id=user_id)
        return await self.get(task_id, account_id=account_id, user_id=user_id)

    async def cancel_skill_update_for_rollback(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> Optional[TaskRecord]:
        task = await self.get(task_id, account_id=account_id, user_id=user_id)
        if task is not None and task.task_type != "add_skill":
            raise ValueError("Only Skill processing can be cancelled for package rollback")
        found = await self._request_cancellation(
            task_id,
            account_id=account_id,
            user_id=user_id,
            allow_terminal=True,
        )
        if found.code is StoreOperationCode.NOT_FOUND:
            return None
        if not found.accepted:
            raise ValueError(found.message or found.code.value)
        self._work_index.cancel_active(task_id, account_id=account_id, user_id=user_id)
        return await self.get(task_id, account_id=account_id, user_id=user_id)

    async def _request_cancellation(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
        allow_terminal: bool,
    ) -> StoreOperationResult:
        return await run_to_completion(
            lambda: self._store.request_cancellation(
                task_id,
                cancellable_types=_CANCELLABLE_TASK_TYPES,
                allow_terminal=allow_terminal,
                account_id=account_id,
                user_id=user_id,
            )
        )

    async def register_running_task(
        self,
        task_id: str,
        *,
        work_id: Optional[str] = None,
        account_id: str,
        user_id: str,
    ) -> str:
        self._validate_owner(account_id, user_id)
        active_task = asyncio.current_task()
        status = await self._store.get_task_status(task_id, account_id=account_id, user_id=user_id)
        if active_task is None or status is None or status in TERMINAL_TASK_STATUSES:
            raise TaskWorkRejected(f"Task {task_id} is unavailable")
        inline = work_id is None
        running_work_id = work_id or f"inline:{uuid4()}"
        if inline:
            registration = await self.register_work(
                task_id,
                running_work_id,
                "Inline",
                account_id=account_id,
                user_id=user_id,
            )
            if registration is WorkRegistration.REJECTED:
                raise TaskWorkRejected(f"Task {task_id} rejects inline work")
            try:
                await self.start_work(
                    task_id,
                    running_work_id,
                    account_id=account_id,
                    user_id=user_id,
                )
            except BaseException:
                if registration is WorkRegistration.CREATED:
                    await run_to_completion(
                        lambda: self.discard_work(
                            task_id,
                            running_work_id,
                            account_id=account_id,
                            user_id=user_id,
                        )
                    )
                raise
        self._work_index.register_active(
            task_id,
            active_task,
            account_id=account_id,
            user_id=user_id,
        )
        self._running.set((running_work_id, account_id, user_id, inline))
        if await self.is_cancellation_requested(task_id, account_id=account_id, user_id=user_id):
            active_task.cancel()
        return running_work_id

    async def unregister_running_task(self, task_id: str) -> None:
        active_task = asyncio.current_task()
        state = self._running.get()
        self._running.set(None)
        if active_task is not None and state is not None:
            _, account_id, user_id, _ = state
            self._work_index.unregister_active(
                task_id,
                active_task,
                account_id=account_id,
                user_id=user_id,
            )
        if state is not None and state[3]:
            work_id, account_id, user_id, _inline = state
            await run_to_completion(
                lambda: self.mark_work_done(
                    task_id,
                    work_id,
                    account_id=account_id,
                    user_id=user_id,
                )
            )

    async def get(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        task = await self._store.get_task(task_id, account_id=account_id, user_id=user_id)
        return self._public(task) if task is not None else None

    async def get_with_events(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        task = await self._store.get_task_with_events(
            task_id, account_id=account_id, user_id=user_id
        )
        return self._public(task) if task is not None else None

    async def get_task_status(
        self, task_id: str, *, account_id: str, user_id: str
    ) -> Optional[TaskStatus]:
        self._validate_owner(account_id, user_id)
        return await self._store.get_task_status(task_id, account_id=account_id, user_id=user_id)

    async def get_task_auth(self, task_id: str, *, account_id: str, user_id: str) -> Dict[str, Any]:
        self._validate_owner(account_id, user_id)
        task = await self._store.get_task(task_id, account_id=account_id, user_id=user_id)
        return deepcopy(task.auth) if task is not None else {}

    async def wait(
        self,
        task_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> TaskRecord:
        with _consume_wait_budget(task_id, timeout) as remaining:
            deadline = None if remaining is None else time.monotonic() + remaining
            next_poll_interval = _WAIT_INITIAL_POLL_INTERVAL
            while True:
                if account_id is not None and user_id is not None:
                    status = await self.get_task_status(
                        task_id,
                        account_id=account_id,
                        user_id=user_id,
                    )
                    if status is None:
                        raise KeyError(f"Task not found: {task_id}")
                    if status in TERMINAL_TASK_STATUSES:
                        task = await self.get(
                            task_id,
                            account_id=account_id,
                            user_id=user_id,
                        )
                        if task is None:
                            raise KeyError(f"Task not found: {task_id}")
                        return task
                else:
                    task = await self.get(task_id, account_id=account_id, user_id=user_id)
                    if task is None:
                        raise KeyError(f"Task not found: {task_id}")
                    if task.status in TERMINAL_TASK_STATUSES:
                        return task
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for Task {task_id}")
                await asyncio.sleep(
                    next_poll_interval
                    if remaining is None
                    else min(next_poll_interval, remaining)
                )
                next_poll_interval = min(_WAIT_MAX_POLL_INTERVAL, next_poll_interval * 2)

    async def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: Optional[int] = 50,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        include_internal: bool = True,
        *,
        before: tuple[float, str] | None = None,
        q: Optional[str] = None,
    ) -> List[TaskRecord]:
        from openviking.service.task_pagination import matches

        records = await self._store.list(
            account_id,
            user_id=user_id,
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
            before=before,
            include_internal=include_internal,
            q=q,
        )
        visible = {
            task.task_id: task
            for task in records
            if (before is None or (task.created_at, task.task_id) < before)
            and matches(
                task.to_dict(),
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                include_internal=include_internal,
                q=q,
            )
        }
        ordered = sorted(
            visible.values(),
            key=lambda task: (task.created_at, task.task_id),
            reverse=True,
        )
        selected = ordered if limit is None else ordered[:limit]
        return [self._public(task) for task in selected]

    async def list_root_tasks(
        self,
        *,
        account_id: str,
        user_id: str,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
        before: tuple[float, str] | None = None,
        include_internal: bool = True,
        q: Optional[str] = None,
    ) -> List[TaskRecord]:
        """Build the ROOT compatibility view over the FileStore limitations.

        The logical meaning is "all Tasks". The file-backed implementation
        cannot enumerate every account without an owner, so include the
        durable system/current-owner scopes and its cache-only global view.
        """

        source_limit = limit + 1
        records = await asyncio.gather(
            self.list_tasks(
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                limit=source_limit,
                account_id=SYSTEM_TASK_ACCOUNT_ID,
                user_id=SYSTEM_TASK_USER_ID,
                before=before,
                include_internal=include_internal,
                q=q,
            ),
            self.list_tasks(
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                limit=source_limit,
                account_id=account_id,
                user_id=user_id,
                before=before,
                include_internal=include_internal,
                q=q,
            ),
            self.list_tasks(
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                limit=source_limit,
                before=before,
                include_internal=include_internal,
                q=q,
            ),
        )
        visible = {task.task_id: task for source in records for task in source}
        ordered = sorted(
            visible.values(),
            key=lambda task: (task.created_at, task.task_id),
            reverse=True,
        )
        return ordered[:limit]

    async def has_running(
        self,
        task_type: str,
        resource_id: str,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        tasks = await self._store.list(
            account_id,
            user_id=user_id,
            task_type=task_type,
            resource_id=resource_id,
        )
        return any(task.status in ACTIVE_TASK_STATUSES for task in tasks)

    async def register_work(
        self,
        task_id: str,
        work_id: str,
        queue_name: str,
        *,
        account_id: str,
        user_id: str,
    ) -> WorkRegistration:
        work = WorkRecord(
            work_id=work_id,
            task_id=task_id,
            queue_name=queue_name,
            account_id=account_id,
            user_id=user_id,
        )

        async def register() -> WorkRegistration:
            try:
                result = await self._store.add_work(work, account_id=account_id, user_id=user_id)
            except TaskWorkRejected:
                return WorkRegistration.REJECTED
            if result.is_applied:
                return WorkRegistration.CREATED
            if result.accepted and result.code is StoreOperationCode.DUPLICATE:
                return WorkRegistration.EXISTING
            return WorkRegistration.REJECTED

        return await register()

    async def discard_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await run_to_completion(
            lambda: self._store.discard_work(
                task_id, work_id, account_id=account_id, user_id=user_id
            )
        )

    async def reconcile_delivery(
        self,
        task_id: str,
        work_id: str,
        queue_name: str,
        *,
        account_id: str,
        user_id: str,
    ) -> None:
        work = WorkRecord(
            work_id=work_id,
            task_id=task_id,
            queue_name=queue_name,
            account_id=account_id,
            user_id=user_id,
        )
        await self._store.reconcile_delivery(work, account_id=account_id, user_id=user_id)

    async def get_work_state(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> Optional[WorkState]:
        work = await self._store.get_work(task_id, work_id, account_id=account_id, user_id=user_id)
        return work.state if work is not None else None

    async def start_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await self._store.start_work(task_id, work_id, account_id=account_id, user_id=user_id)

    async def mark_work_done(
        self,
        task_id: str,
        work_id: str,
        *,
        context_count_delta: int = 0,
        vector_written: bool = False,
        account_id: str,
        user_id: str,
    ) -> None:
        await self._finish_work(
            task_id,
            work_id,
            WorkState.DONE,
            error=None,
            errors=None,
            context_count_delta=max(context_count_delta, int(vector_written)),
            account_id=account_id,
            user_id=user_id,
        )

    async def mark_work_failed(
        self,
        task_id: str,
        work_id: str,
        error: Optional[str] = None,
        *,
        errors: Optional[Collection[str]] = None,
        account_id: str,
        user_id: str,
    ) -> None:
        await self._finish_work(
            task_id,
            work_id,
            WorkState.FAILED,
            error=sanitize_error(error) if error else None,
            errors=errors,
            context_count_delta=0,
            account_id=account_id,
            user_id=user_id,
        )

    async def mark_work_requeued(
        self,
        task_id: str,
        work_id: str,
        *,
        delta: int = 1,
        account_id: str,
        user_id: str,
    ) -> None:
        await self._store.requeue_work(
            task_id,
            work_id,
            delta=delta,
            account_id=account_id,
            user_id=user_id,
        )

    async def _finish_work(
        self,
        task_id: str,
        work_id: str,
        state: WorkState,
        *,
        error: Optional[str],
        errors: Optional[Collection[str]],
        context_count_delta: int,
        account_id: str,
        user_id: str,
    ) -> None:
        await run_to_completion(
            lambda: self._store.finish_work(
                task_id,
                work_id,
                state,
                error=error,
                errors=errors,
                context_count_delta=context_count_delta,
                account_id=account_id,
                user_id=user_id,
            )
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
        await self._store.record_work_error(
            task_id,
            work_id,
            sanitize_error(error),
            account_id=account_id,
            user_id=user_id,
        )

    async def settle_work(
        self, task_id: str, work_id: str, *, account_id: str, user_id: str
    ) -> None:
        await run_to_completion(
            lambda: self._store.settle_work(
                task_id, work_id, account_id=account_id, user_id=user_id
            )
        )

    async def is_cancelling(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> bool:
        return await self.is_cancellation_requested(task_id, account_id=account_id, user_id=user_id)

    async def is_cancellation_requested(
        self,
        task_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> bool:
        status = await self.get_task_status(task_id, account_id=account_id, user_id=user_id)
        return status in (TaskStatus.CANCELLING, TaskStatus.CANCELLED)

    async def wait_for_work(
        self,
        task_id: str,
        work_ids: Collection[str],
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: Optional[float] = None,
        stop_waiting: Optional[Callable[[], bool]] = None,
    ) -> None:
        wanted = set(work_ids)
        if not wanted:
            return
        with _consume_wait_budget(task_id, timeout) as remaining:
            deadline = None if remaining is None else time.monotonic() + remaining
            next_poll_interval = _WAIT_INITIAL_POLL_INTERVAL
            while True:
                works = await self._store.get_works(
                    task_id,
                    wanted,
                    account_id=account_id,
                    user_id=user_id,
                )
                missing = wanted.difference(works)
                if missing:
                    raise KeyError(f"Work not found for Task {task_id}: {sorted(missing)}")
                if all(work.state in TERMINAL_WORK_STATES for work in works.values()):
                    return
                if stop_waiting is not None and stop_waiting():
                    raise asyncio.CancelledError("Worker stopped with queued Work")
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for Work in Task {task_id}")
                await asyncio.sleep(
                    next_poll_interval
                    if remaining is None
                    else min(next_poll_interval, remaining)
                )
                next_poll_interval = min(_WAIT_MAX_POLL_INTERVAL, next_poll_interval * 2)

    async def wait_for_task_work(
        self,
        task_id: str,
        *,
        exclude_work_id: Optional[str] = None,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        with _consume_wait_budget(task_id, timeout) as remaining:
            deadline = None if remaining is None else time.monotonic() + remaining
            next_poll_interval = _WAIT_INITIAL_POLL_INTERVAL
            while await self._store.has_open_work(
                task_id,
                exclude_work_id=exclude_work_id,
                account_id=account_id,
                user_id=user_id,
            ):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for Work in Task {task_id}")
                await asyncio.sleep(
                    next_poll_interval
                    if remaining is None
                    else min(next_poll_interval, remaining)
                )
                next_poll_interval = min(_WAIT_MAX_POLL_INTERVAL, next_poll_interval * 2)

    async def is_enqueue_complete(
        self,
        task_id: str,
        work_id: str,
        *,
        account_id: str,
        user_id: str,
    ) -> bool:
        auth = await self.get_task_auth(task_id, account_id=account_id, user_id=user_id)
        return work_id in set(auth.get("enqueue_complete_work_ids") or ())

    async def build_queue_status(
        self,
        task_id: str,
        work_ids: Optional[Collection[str]] = None,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, object]]:
        summary = await self.get_work_summary(
            task_id,
            work_ids=work_ids,
            account_id=account_id,
            user_id=user_id,
        )
        return self.format_queue_status(summary)

    @staticmethod
    def format_queue_status(
        summary: WorkSummary,
    ) -> Dict[str, Dict[str, object]]:
        result: Dict[str, Dict[str, object]] = {}
        for queue_name in _PUBLIC_QUEUE_NAMES:
            queue = summary.queues.get(queue_name, QueueWorkSummary())
            result[queue_name] = {
                "processed": queue.processed,
                "requeue_count": queue.requeue_count,
                "error_count": queue.error_count,
                "errors": [
                    {"message": sanitize_error(str(error))}
                    for error in queue.errors[:MAX_QUEUE_ERROR_DETAILS]
                ],
            }
        return result

    async def get_work_summary(
        self,
        task_id: str,
        work_ids: Optional[Collection[str]] = None,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> WorkSummary:
        return await self._store.summarize_works(
            task_id,
            work_ids=work_ids,
            account_id=account_id,
            user_id=user_id,
        )

    @staticmethod
    def format_queue_timing(
        summary: WorkSummary,
    ) -> Dict[str, Dict[str, float]]:
        return {
            queue_name.lower(): {
                "queue_wait_ms": queue.queue_wait_ms,
                "execute_ms": queue.execute_ms,
            }
            for queue_name, queue in summary.queues.items()
        }

    async def get_queue_timing(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, float]]:
        summary = await self.get_work_summary(
            task_id,
            work_ids=None,
            account_id=account_id,
            user_id=user_id,
        )
        return self.format_queue_timing(summary)

    async def get_context_count(
        self,
        task_id: str,
        *,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        summary = await self.get_work_summary(
            task_id,
            work_ids=None,
            account_id=account_id,
            user_id=user_id,
        )
        return summary.context_count

    async def record_event(
        self,
        task_id: str,
        kind: str,
        *,
        account_id: str,
        user_id: str,
        operation: Optional[str] = None,
    ) -> None:
        self._validate_owner(account_id, user_id)
        if kind not in PROCESS_EVENT_KINDS:
            raise ValueError(f"Unknown task process event: {kind}")
        if operation is not None and not re.fullmatch(r"[\w.:-]{1,128}", operation):
            raise ValueError("operation must be a bounded operation identifier")
        await self._store.record_event(
            task_id,
            kind,
            operation=operation,
            account_id=account_id,
            user_id=user_id,
        )

    async def has_work(
        self,
        task_id: str,
        *,
        exclude_work_id: Optional[str] = None,
        account_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        return await self._store.has_open_work(
            task_id,
            exclude_work_id=exclude_work_id,
            account_id=account_id,
            user_id=user_id,
        )

    def has_active_producer(self, task_id: str) -> bool:
        state = self._running.get()
        return self._work_index.has_other_active(
            task_id,
            asyncio.current_task(),
            account_id=state[1] if state is not None else None,
            user_id=state[2] if state is not None else None,
        )

    async def delete_tasks(
        self,
        account_id: str,
        user_id: Optional[str] = None,
        *,
        allow_active_types: Collection[str] = (),
    ) -> int:
        if not account_id:
            raise ValueError("Task deletion requires a non-empty account_id")
        allowed = set(allow_active_types)
        delete_result = await self._store.delete_tasks(
            account_id=account_id,
            user_id=user_id,
            allow_active_types=allowed,
        )
        if not delete_result.accepted:
            raise RuntimeError(delete_result.result.message or delete_result.result.code.value)
        for task in delete_result.deleted:
            self._work_index.forget_task(
                task.task_id,
                account_id=task.account_id,
                user_id=task.user_id,
            )
        return len(delete_result.deleted)

    def count(self) -> int:
        return sum(sum(statuses.values()) for statuses in self.snapshot_counts_by_type().values())

    def snapshot_counts_by_type(self) -> Dict[str, Dict[str, int]]:
        return self._store.snapshot_task_stats()

    @staticmethod
    def _public(task: TaskRecord) -> TaskRecord:
        copied = deepcopy(task)
        copied.meta = sanitize_task_result(copied.meta)
        copied.result = sanitize_task_result(copied.result)
        copied.auth = {}
        return copied
