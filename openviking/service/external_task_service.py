# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable execution of asynchronous tasks owned by external services."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol
from uuid import uuid4

from openviking.pyagfs import GitConcurrentCommitError
from openviking.pyagfs.exceptions import AGFSConnectionError, AGFSNetworkError, AGFSTimeoutError
from openviking.server.identity import RequestContext
from openviking.service.task_domain import TaskPatch, sanitize_error
from openviking.service.task_tracker import TaskRecord, TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import (
    KeyedAsyncLockPool,
    OwnerLoopDispatcher,
    run_to_completion,
)
from openviking.service.task_work_index import bind_task_context
from openviking.storage.errors import ResourceBusyError
from openviking.storage.queuefs import QueueManager, get_queue_manager
from openviking_cli.exceptions import ConflictError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"pending", "running", "cancelling"})


class ExternalTaskError(RuntimeError):
    """A classified provider failure used by the durable retry loop."""

    def __init__(self, code: str, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


class _RetryExhaustedError(ExternalTaskError):
    """Retries are exhausted; Runtime acceptance remains unknown."""


@dataclass(frozen=True)
class ExternalTaskSnapshot:
    """Provider-independent state returned by an external task API."""

    status: str
    stage: str | None = None
    result: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


class ExternalTaskProvider(Protocol):
    """Adapter contract for one external asynchronous task type."""

    task_type: str
    task_id_prefix: str
    poll_max_attempts: int

    @property
    def poll_interval_seconds(self) -> float: ...

    def serialization_key(self, payload: Mapping[str, Any]) -> str | None: ...

    async def submit(
        self,
        ov_task_id: str,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
    ) -> str: ...

    async def get(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
        *,
        payload: Mapping[str, Any] | None = None,
        private_payload: Mapping[str, Any] | None = None,
    ) -> ExternalTaskSnapshot: ...

    async def cancel(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
        *,
        payload: Mapping[str, Any] | None = None,
        private_payload: Mapping[str, Any] | None = None,
    ) -> ExternalTaskSnapshot: ...


class ExternalTaskService:
    """Own OV task state while registered providers perform the actual work."""

    def __init__(self) -> None:
        self._providers: dict[str, ExternalTaskProvider] = {}
        # Restored before workers start, then accessed only by the ExternalTask
        # queue's loop. Restored owners may include multiple
        # tasks submitted before target serialization was enabled.
        self._owners: dict[tuple[str | None, str, str], set[str]] = {}
        self._executing: set[str] = set()
        self._submission_locks = KeyedAsyncLockPool[str]()
        self._submission_dispatcher = OwnerLoopDispatcher()

    def register(self, provider: ExternalTaskProvider) -> None:
        if provider.task_type in self._providers:
            raise ValueError(f"External task provider already registered: {provider.task_type}")
        self._providers[provider.task_type] = provider

    def _provider(self, task_type: str) -> ExternalTaskProvider:
        provider = self._providers.get(task_type)
        if provider is None:
            raise ExternalTaskError(
                "UNAVAILABLE",
                f"External task provider is not registered: {task_type}",
                transient=False,
            )
        return provider

    def _serialization_key(self, task: TaskRecord) -> tuple[str | None, str, str] | None:
        key = self._provider(task.task_type).serialization_key(task.meta["request"])
        return (task.account_id, task.task_type, key) if key is not None else None

    async def restore_tasks(self, tasks: Iterable[TaskRecord]) -> None:
        """Reserve submitted work before any recovered queue delivery can run."""
        for task in tasks:
            if (
                task.task_type not in self._providers
                or task.result is not None
                or task.error is not None
            ):
                continue
            if task.status not in {TaskStatus.RUNNING, TaskStatus.CANCELLING}:
                continue
            if task.status == TaskStatus.CANCELLING and task.stage in {None, "queued"}:
                auth = await self._task_auth(task.task_id, task.account_id, task.user_id)
                if not auth.get("external_task_id"):
                    continue
            key = self._serialization_key(task)
            if key is not None:
                self._owners.setdefault(key, set()).add(task.task_id)

    def _release(self, task: TaskRecord) -> None:
        key = self._serialization_key(task)
        if key is not None and key in self._owners:
            self._owners[key].discard(task.task_id)
            if not self._owners[key]:
                del self._owners[key]

    async def create(
        self,
        task_type: str,
        *,
        resource_id: str | None,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any] | None = None,
        connection: Mapping[str, Any],
        ctx: RequestContext,
        idempotency_key: str | None = None,
        submission_payload: Mapping[str, Any] | None = None,
    ) -> TaskRecord:
        """``submission_payload`` (default ``payload``) is what idempotent retries must match."""

        async def create_task():
            return await self._create(
                task_type,
                resource_id=resource_id,
                payload=payload,
                private_payload=private_payload,
                connection=connection,
                ctx=ctx,
                idempotency_key=idempotency_key,
                submission_payload=submission_payload,
            )

        if not idempotency_key:
            return await create_task()
        key = submission_task_id(ctx, task_type, "", idempotency_key)

        async def serialized():
            async with self._submission_locks.acquire(key):
                return await run_to_completion(create_task)

        return await self._submission_dispatcher.run(serialized)

    async def _create(
        self,
        task_type: str,
        *,
        resource_id: str | None,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any] | None = None,
        connection: Mapping[str, Any],
        ctx: RequestContext,
        idempotency_key: str | None = None,
        submission_payload: Mapping[str, Any] | None = None,
    ) -> TaskRecord:
        provider = self._provider(task_type)
        tracker = get_task_tracker()
        request_hash = submission_request_hash(
            payload if submission_payload is None else submission_payload, private_payload
        )
        token = uuid4().hex
        task_id = (
            submission_task_id(ctx, task_type, provider.task_id_prefix, idempotency_key)
            if idempotency_key
            else f"{provider.task_id_prefix}{token}"
        )
        task = await tracker.create(
            task_type,
            resource_id=resource_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            task_id=task_id,
            meta={
                "request": dict(payload),
                **(
                    {"submission_hash": request_hash, "submission_token": token}
                    if idempotency_key
                    else {}
                ),
            },
            auth={
                "openviking_connection": dict(connection),
                "external_request_private": dict(private_payload or {}),
            },
        )
        if idempotency_key and task.meta.get("submission_hash") != request_hash:
            raise ConflictError("Idempotency-Key was already used with different parameters")
        if idempotency_key and task.meta.get("submission_token") != token:
            # Recover a creation interrupted before queue delivery. Duplicate
            # deliveries are safe: execute claims each task before submitting.
            await self._resume_submission(task, ctx)
            return task
        enqueued = False
        try:
            await tracker.patch_task(
                task.task_id,
                TaskPatch(stage="queued"),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            with bind_task_context(task.task_id, ctx.account_id, ctx.user.user_id):
                await get_queue_manager().enqueue(
                    QueueManager.EXTERNAL_TASK,
                    {
                        "task_id": task.task_id,
                        "account_id": ctx.account_id,
                        "user_id": ctx.user.user_id,
                    },
                )
            enqueued = True
            current = await tracker.get(
                task.task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            if current is not None:
                task = current
        except BaseException:
            if not enqueued:
                await tracker.fail(
                    task.task_id,
                    "Failed to enqueue external task",
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
            raise
        return task

    async def recover_submission(
        self,
        task_type: str,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        ctx: RequestContext,
        key: str,
    ) -> TaskRecord | None:
        async def recover():
            provider = self._provider(task_type)
            task = await get_task_tracker().get(
                submission_task_id(ctx, task_type, provider.task_id_prefix, key),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            if task is None:
                return None
            if task.meta.get("submission_hash") != submission_request_hash(
                payload, private_payload
            ):
                raise ConflictError("Idempotency-Key was already used with different parameters")
            await self._resume_submission(task, ctx)
            return task

        async def serialized():
            async with self._submission_locks.acquire(submission_task_id(ctx, task_type, "", key)):
                return await run_to_completion(recover)

        return await self._submission_dispatcher.run(serialized)

    async def _resume_submission(self, task: TaskRecord, ctx: RequestContext) -> None:
        if task.status == TaskStatus.PENDING:
            with bind_task_context(task.task_id, ctx.account_id, ctx.user.user_id):
                await get_queue_manager().enqueue(
                    QueueManager.EXTERNAL_TASK,
                    {
                        "task_id": task.task_id,
                        "account_id": ctx.account_id,
                        "user_id": ctx.user.user_id,
                    },
                )

    async def execute(self, task_id: str, account_id: str, user_id: str) -> bool:
        """Execute a delivery; return False when it needs requeueing."""
        tracker = get_task_tracker()
        try:
            return await self._retry(
                lambda: self._execute_once(task_id, account_id, user_id),
                task_id=task_id,
                operation_name="execute",
                poll_interval=0.2,
                max_attempts=3,
            )
        except asyncio.CancelledError:
            if await tracker.is_cancellation_requested(
                task_id, account_id=account_id, user_id=user_id
            ):
                await self.cancel_recovered(task_id, account_id, user_id)
            raise
        except Exception as exc:
            await self.cancel_recovered(task_id, account_id, user_id, failure=exc)
            return True

    async def _execute_once(self, task_id: str, account_id: str, user_id: str) -> bool:
        """Resume a delivery; return False while admission or finalization is pending."""
        tracker = get_task_tracker()
        claimed = False
        try:
            task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
            if task is None:
                return True
            if task.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                self._release(task)
                return True
            if task.status == TaskStatus.CANCELLING:
                raise asyncio.CancelledError()
            # The outcome is persisted before QueueFS ACK removes the owned work.
            # After a crash in that window, let the recovered delivery ACK without
            # resubmitting credentials that have already been cleared.
            if task.result is not None or task.error is not None:
                self._release(task)
                return True

            provider = self._provider(task.task_type)
            payload = task.meta.get("request")
            if not isinstance(payload, dict):
                await tracker.fail(
                    task_id,
                    "INVALID_ARGUMENT: External task request is missing",
                    account_id=account_id,
                    user_id=user_id,
                )
                return True
            key = self._serialization_key(task)
            owners = self._owners.get(key, set()) if key is not None else set()
            if task_id in self._executing or (owners and task_id not in owners):
                return False
            # No await between checking and claiming: concurrent deliveries on the
            # queue loop cannot both acquire an unoccupied target.
            if key is not None:
                self._owners.setdefault(key, set()).add(task_id)
            self._executing.add(task_id)
            claimed = True
            auth = await self._task_auth(task_id, account_id, user_id)
            if auth.get("external_cleanup_failure"):
                await self.cancel_recovered(task_id, account_id, user_id)
                return True
            on_start = getattr(provider, "on_start", None)
            if on_start is not None:
                try:
                    started = await on_start(task)
                except (ResourceBusyError, GitConcurrentCommitError) as exc:
                    if isinstance(exc, ResourceBusyError) and not exc.retryable:
                        raise
                    started = "busy"
                if started == "busy":
                    self._release(task)
                    return False
                if isinstance(started, ExternalTaskSnapshot):
                    finished = await self._apply_snapshot(
                        started,
                        task_id=task_id,
                        account_id=account_id,
                        user_id=user_id,
                        auth=auth,
                    )
                    if not finished:
                        await asyncio.sleep(provider.poll_interval_seconds)
                    return finished
            await self._execute_task(task, provider, payload, account_id, user_id)
            return True
        finally:
            # An interrupted delivery keeps ownership until recovery confirms
            # the Runtime has stopped; it must not admit another target writer.
            if claimed:
                self._executing.remove(task_id)

    async def _execute_task(
        self,
        task: TaskRecord,
        provider: ExternalTaskProvider,
        payload: Mapping[str, Any],
        account_id: str,
        user_id: str,
    ) -> None:
        tracker = get_task_tracker()
        task_id = task.task_id
        auth = await self._task_auth(task_id, account_id, user_id)
        connection = self._mapping(auth.get("openviking_connection"))
        private_payload = self._mapping(auth.get("external_request_private"))
        external_task_id = str(auth.get("external_task_id") or "").strip() or None
        await tracker.start(
            task_id,
            account_id=account_id,
            user_id=user_id,
            stage="polling" if external_task_id else "submitting",
        )
        if external_task_id is None:
            external_task_id = await self._retry(
                lambda: provider.submit(task_id, payload, private_payload, connection),
                task_id=task_id,
                operation_name="submit",
                poll_interval=provider.poll_interval_seconds,
                max_attempts=provider.poll_max_attempts,
            )
            await tracker.patch_task(
                task_id,
                TaskPatch(auth={"external_task_id": external_task_id}),
                account_id=account_id,
                user_id=user_id,
            )
        snapshot = None
        while True:
            if snapshot is None or snapshot.status in _ACTIVE_STATUSES:
                snapshot = await self._retry(
                    lambda: provider.get(
                        external_task_id,
                        connection,
                        payload=payload,
                        private_payload=private_payload,
                    ),
                    task_id=task_id,
                    operation_name="poll",
                    poll_interval=provider.poll_interval_seconds,
                    max_attempts=provider.poll_max_attempts,
                )
            if await self._apply_snapshot(
                snapshot,
                task_id=task_id,
                account_id=account_id,
                user_id=user_id,
                auth=auth,
            ):
                return
            await asyncio.sleep(provider.poll_interval_seconds)

    async def cancel_recovered(
        self, task_id: str, account_id: str, user_id: str, *, failure: Exception | None = None
    ) -> None:
        """Finish cleanup; persist failures for restart recovery."""
        tracker = get_task_tracker()
        try:
            # Repeated cancellation must not interrupt cleanup of the open Work.
            await tracker.unregister_running_task(task_id)
            auth = await self._task_auth(task_id, account_id, user_id)
            saved_failure = auth.get("external_cleanup_failure")
            if saved_failure:
                failure = ExternalTaskError(
                    saved_failure["code"], saved_failure["message"], transient=False
                )
            elif failure is not None:
                await run_to_completion(
                    lambda: tracker.patch_task(
                        task_id,
                        TaskPatch(
                            auth={
                                "external_cleanup_failure": {
                                    "code": getattr(failure, "code", None) or "INTERNAL",
                                    "message": sanitize_error(
                                        str(failure) or type(failure).__name__
                                    ),
                                }
                            }
                        ),
                        account_id=account_id,
                        user_id=user_id,
                    )
                )
            while True:
                try:
                    await self._retry(
                        lambda: self._cancel_recovered_once(task_id, account_id, user_id, failure),
                        task_id=task_id,
                        operation_name="cleanup",
                        poll_interval=0.2,
                        max_attempts=3,
                    )
                    return
                except _RetryExhaustedError as exc:
                    task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
                    if task is None or task.task_type != "compile":
                        raise
                    # Keep exhausted cleanup retries on the unacknowledged Work.
                    cleanup_error = sanitize_error(str(exc))
                    if task.meta.get("cleanup_error") != cleanup_error:
                        await tracker.patch_task(
                            task_id,
                            TaskPatch(meta={"cleanup_error": cleanup_error}),
                            account_id=account_id,
                            user_id=user_id,
                        )
                    await asyncio.sleep(
                        max(self._provider(task.task_type).poll_interval_seconds, 0.2)
                    )
        except asyncio.CancelledError as exc:
            if await tracker.is_cancellation_requested(
                task_id, account_id=account_id, user_id=user_id
            ):
                # Preserve delivery when shutdown interrupts cancellation cleanup.
                raise ExternalTaskError(
                    "CLEANUP_INTERRUPTED", "Cancellation cleanup was interrupted", transient=True
                ) from exc
            raise
        except Exception as exc:
            await get_task_tracker().patch_task(
                task_id,
                TaskPatch(meta={"cleanup_error": sanitize_error(str(exc) or type(exc).__name__)}),
                account_id=account_id,
                user_id=user_id,
            )
            raise

    async def _cancel_recovered_once(
        self, task_id: str, account_id: str, user_id: str, failure: Exception | None = None
    ) -> None:
        """Resume cancellation without releasing a submitted task before Runtime stops."""
        tracker = get_task_tracker()
        task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
        if task is None:
            return
        if (
            task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            or task.result is not None
            or task.error is not None
        ):
            self._release(task)
            return
        payload = task.meta.get("request")
        if not isinstance(payload, dict):
            raise ExternalTaskError(
                "INVALID_ARGUMENT",
                "External task request is missing",
                transient=False,
            )
        auth = await self._task_auth(task_id, account_id, user_id)
        outcome = (
            ExternalTaskSnapshot(
                status="failed",
                error_code=getattr(failure, "code", None) or "INTERNAL",
                error_message=str(failure) or type(failure).__name__,
            )
            if failure is not None
            else ExternalTaskSnapshot(status="cancelled")
        )
        rejected = (
            task.stage == "submitting"
            and isinstance(failure, ExternalTaskError)
            and not isinstance(failure, _RetryExhaustedError)
            and not failure.transient
            and failure.code
            in {"INVALID_ARGUMENT", "NOT_FOUND", "PERMISSION_DENIED", "UNAUTHENTICATED"}
        )
        if not auth.get("external_task_id") and (task.stage in {None, "queued"} or rejected):
            while not await self._apply_snapshot(
                outcome,
                task_id=task_id,
                account_id=account_id,
                user_id=user_id,
                auth=auth,
            ):
                await asyncio.sleep(self._provider(task.task_type).poll_interval_seconds)
            return
        await self._cancel_external(
            self._provider(task.task_type),
            ov_task_id=task_id,
            payload=payload,
            private_payload=self._mapping(auth.get("external_request_private")),
            connection=self._mapping(auth.get("openviking_connection")),
            external_task_id=str(auth.get("external_task_id") or "").strip() or None,
            account_id=account_id,
            user_id=user_id,
            auth=auth,
            failure=outcome if failure is not None else None,
        )

    @staticmethod
    async def _task_auth(task_id: str, account_id: str, user_id: str) -> dict[str, Any]:
        return await get_task_tracker().get_task_auth(
            task_id,
            account_id=account_id,
            user_id=user_id,
        )

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    async def _apply_snapshot(
        self,
        snapshot: ExternalTaskSnapshot,
        *,
        task_id: str,
        account_id: str,
        user_id: str,
        auth: dict[str, Any],
    ) -> bool:
        """Apply state; return False while execution or finalization is active.

        ``auth`` is this delivery's private Task auth snapshot. Its cleanup
        failure marker changes only after the matching patch is persisted.
        """
        tracker = get_task_tracker()
        task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
        if snapshot.stage is not None or snapshot.meta:
            # Runtime termination can still require local finalization before the Task ends.
            stage = (
                task.stage
                if task is not None and snapshot.status in {"completed", "failed", "cancelled"}
                else snapshot.stage or snapshot.status
            )
            meta = snapshot.meta or {}
            if task is not None and (
                task.stage != stage
                or any(
                    key not in task.meta or task.meta[key] != value for key, value in meta.items()
                )
            ):
                await tracker.patch_task(
                    task_id,
                    TaskPatch(stage=stage, meta=meta),
                    account_id=account_id,
                    user_id=user_id,
                )
        if snapshot.status in _ACTIVE_STATUSES:
            return False
        if snapshot.status not in {"completed", "failed", "cancelled"}:
            raise ExternalTaskError(
                "INVALID_RESPONSE",
                f"Unknown external task status: {snapshot.status}",
                transient=False,
            )
        if task is not None:
            on_finish = getattr(self._providers.get(task.task_type), "on_finish", None)
            if on_finish is not None:
                # Persist failure intent before a deferred rollback.
                if snapshot.status == "failed":
                    cleanup_failure = {
                        "code": snapshot.error_code or "UNKNOWN",
                        "message": sanitize_error(snapshot.error_message or "External task failed"),
                    }
                    if auth.get("external_cleanup_failure") != cleanup_failure:
                        result = await run_to_completion(
                            lambda: tracker.patch_task(
                                task_id,
                                TaskPatch(auth={"external_cleanup_failure": cleanup_failure}),
                                account_id=account_id,
                                user_id=user_id,
                            )
                        )
                        if result.is_applied:
                            auth["external_cleanup_failure"] = cleanup_failure
                if task.task_type == "compile":
                    # Protect finite commits while keeping L0 waits cancellable.
                    snapshot = await on_finish(task, snapshot)
                else:
                    snapshot = await run_to_completion(
                        lambda: self._retry(
                            lambda: on_finish(task, snapshot),
                            task_id=task_id,
                            operation_name="finalize",
                            poll_interval=0.2,
                            max_attempts=3,
                        )
                    )
                if snapshot.status in _ACTIVE_STATUSES:
                    stage = snapshot.stage or snapshot.status
                    if task.stage != stage:
                        await tracker.patch_task(
                            task_id,
                            TaskPatch(stage=stage),
                            account_id=account_id,
                            user_id=user_id,
                        )
                    return False

        async def finalize() -> None:
            if snapshot.status == "failed":
                await tracker.fail(
                    task_id,
                    self._format_error(
                        snapshot.error_code or "UNKNOWN",
                        snapshot.error_message or "External task failed",
                    ),
                    account_id=account_id,
                    user_id=user_id,
                )
            elif snapshot.status == "completed":
                await tracker.complete(
                    task_id,
                    snapshot.result or snapshot.meta or {},
                    account_id=account_id,
                    user_id=user_id,
                )
            else:
                await tracker.record_cancelled(
                    task_id,
                    account_id=account_id,
                    user_id=user_id,
                )
            if task is not None:
                self._release(task)

        await run_to_completion(finalize)
        return True

    async def _cancel_external(
        self,
        provider: ExternalTaskProvider,
        *,
        ov_task_id: str,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
        external_task_id: str | None,
        account_id: str,
        user_id: str,
        auth: dict[str, Any],
        failure: ExternalTaskSnapshot | None = None,
    ) -> None:
        """Cancel provider work and finalize it using this delivery's auth snapshot."""
        max_attempts = provider.poll_max_attempts
        # Returning allows QueueFS to ACK the work and finalize cancellation.
        # Keep ownership until the provider confirms a terminal state.
        if external_task_id is None:
            # Submission may have reached the provider before local cancellation
            # interrupted its response. Recover it with the same idempotency key.
            external_task_id = await self._retry(
                lambda: provider.submit(ov_task_id, payload, private_payload, connection),
                task_id=ov_task_id,
                operation_name="recover before cancel",
                poll_interval=provider.poll_interval_seconds,
                max_attempts=max_attempts,
            )
            await get_task_tracker().patch_task(
                ov_task_id,
                TaskPatch(auth={"external_task_id": external_task_id}),
                account_id=account_id,
                user_id=user_id,
            )
        snapshot = await self._retry(
            lambda task_id=external_task_id: provider.cancel(
                task_id, connection, payload=payload, private_payload=private_payload
            ),
            task_id=ov_task_id,
            operation_name="cancel",
            poll_interval=provider.poll_interval_seconds,
            max_attempts=max_attempts,
        )
        while True:
            if failure is not None and snapshot.status in {"completed", "failed", "cancelled"}:
                snapshot = failure
            if await self._apply_snapshot(
                snapshot,
                task_id=ov_task_id,
                account_id=account_id,
                user_id=user_id,
                auth=auth,
            ):
                return
            await asyncio.sleep(provider.poll_interval_seconds)
            if snapshot.status not in _ACTIVE_STATUSES:
                continue
            snapshot = await self._retry(
                lambda task_id=external_task_id: provider.get(
                    task_id, connection, payload=payload, private_payload=private_payload
                ),
                task_id=ov_task_id,
                operation_name="poll cancellation",
                poll_interval=provider.poll_interval_seconds,
                max_attempts=max_attempts,
            )

    @staticmethod
    async def _retry(
        action: Callable[[], Awaitable[Any]],
        *,
        task_id: str,
        operation_name: str,
        poll_interval: float,
        max_attempts: int,
    ) -> Any:
        """Try recoverable failures up to three times; permanent errors and cancellation propagate."""
        delay = max(poll_interval, 0.2)
        max_attempts = max(1, min(max_attempts, 3))
        for attempt in range(max_attempts):
            try:
                return await action()
            except (
                ExternalTaskError,
                ConnectionError,
                TimeoutError,
                AGFSConnectionError,
                AGFSNetworkError,
                AGFSTimeoutError,
                ResourceBusyError,
                GitConcurrentCommitError,
            ) as exc:
                if isinstance(exc, ExternalTaskError) and not exc.transient:
                    raise
                if isinstance(exc, ResourceBusyError) and not exc.retryable:
                    raise
                if attempt + 1 == max_attempts:
                    raise _RetryExhaustedError(
                        getattr(exc, "code", None) or "UNAVAILABLE",
                        sanitize_error(str(exc) or type(exc).__name__),
                        transient=False,
                    ) from exc
                logger.warning(
                    "External task %s will retry task=%s code=%s: %s",
                    operation_name,
                    task_id,
                    getattr(exc, "code", type(exc).__name__),
                    sanitize_error(str(exc)),
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    @staticmethod
    def _format_error(code: str, message: str) -> str:
        return f"{code}: {message}"


__all__ = [
    "ExternalTaskError",
    "ExternalTaskProvider",
    "ExternalTaskService",
    "ExternalTaskSnapshot",
]


def submission_task_id(ctx: RequestContext, task_type: str, prefix: str, key: str) -> str:
    scope = json.dumps([ctx.account_id, ctx.user.user_id, task_type, key])
    return prefix + hashlib.sha256(scope.encode()).hexdigest()


def submission_request_hash(
    payload: Mapping[str, Any], private_payload: Mapping[str, Any] | None
) -> str:
    return hashlib.sha256(
        json.dumps(
            {"payload": dict(payload), "private": dict(private_payload or {})},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
