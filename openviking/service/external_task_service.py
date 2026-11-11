# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable execution of asynchronous tasks owned by external services."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol
from uuid import uuid4

from openviking.crypto.encryptor import MAGIC, build_credential_encryptor
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
from openviking_cli.exceptions import ConflictError, FailedPreconditionError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"pending", "running", "cancelling"})
_CREDENTIAL_PROVIDER = "encrypted_external_task"
_PRIVATE_AUTH_FIELDS = ("openviking_connection", "external_request_private")
_instance: ExternalTaskService | None = None


def set_external_task_service(service: ExternalTaskService | None) -> None:
    global _instance
    _instance = service


def get_external_task_service() -> ExternalTaskService:
    if _instance is None:
        raise RuntimeError("ExternalTaskService is not initialized")
    return _instance


class ExternalTaskError(RuntimeError):
    """A classified provider failure used by the durable retry loop."""

    def __init__(self, code: str, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


class ExternalTaskRetryExhaustedError(ExternalTaskError):
    """Retries are exhausted and remote acceptance may be unknown."""


@dataclass(frozen=True)
class ExternalTaskSnapshot:
    """Provider-independent state returned by an external task API."""

    status: str
    stage: str | None = None
    result: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ExternalTaskCancellation:
    """Provider cancellation result and any recovered external task identity."""

    external_task_id: str | None
    snapshot: ExternalTaskSnapshot
    terminal_snapshot: ExternalTaskSnapshot | None = None


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

    async def on_start(self, task: TaskRecord) -> str | ExternalTaskSnapshot | None: ...

    async def on_finish(
        self,
        task: TaskRecord,
        snapshot: ExternalTaskSnapshot,
    ) -> ExternalTaskSnapshot: ...

    async def on_cancel(
        self,
        task: TaskRecord,
        *,
        external_task_id: str | None,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
        failure: Exception | None,
    ) -> ExternalTaskCancellation: ...

    def submission_meta(self, external_task_id: str) -> dict[str, Any]: ...


class ExternalTaskService:
    """Own OV task state while registered providers perform the actual work."""

    def __init__(self, *, credential_encryptor: Any = None) -> None:
        self.credential_encryptor = credential_encryptor
        self._providers: dict[str, ExternalTaskProvider] = {}
        # Restored before workers start, then accessed only by the ExternalTask
        # queue's loop. Restored owners may include multiple
        # tasks submitted before target serialization was enabled.
        self._owners: dict[tuple[str | None, str, str], set[str]] = {}
        self._executing: set[str] = set()
        self._submission_locks = KeyedAsyncLockPool[str]()
        self._submission_dispatcher = OwnerLoopDispatcher()

    def _credential_encryptor(self) -> Any:
        if self.credential_encryptor is None:
            from openviking_cli.utils.config.open_viking_config import get_openviking_config

            self.credential_encryptor = build_credential_encryptor(
                get_openviking_config().encryption
            )
        return self.credential_encryptor

    async def _encrypt_auth(self, account_id: str, auth: Mapping[str, Any]) -> dict[str, Any]:
        encrypted = dict(auth)
        try:
            for field in _PRIVATE_AUTH_FIELDS:
                value = self._mapping(auth.get(field))
                if not value:
                    continue
                plaintext = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
                ciphertext = await self._credential_encryptor().encrypt(account_id, plaintext)
                encrypted[field] = {
                    "provider": _CREDENTIAL_PROVIDER,
                    "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                }
        except Exception as exc:
            raise FailedPreconditionError(
                "Cannot encrypt external task credentials; check encryption key configuration"
            ) from exc
        return encrypted

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
                or (
                    task.status != TaskStatus.CANCELLED
                    and (task.result is not None or task.error is not None)
                )
            ):
                continue
            if task.status not in {
                TaskStatus.RUNNING,
                TaskStatus.CANCELLING,
                TaskStatus.CANCELLED,
            }:
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
        connection: Mapping[str, Any] | None = None,
        ctx: RequestContext,
        idempotency_key: str | None = None,
        submission_payload: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        on_task_created: Callable[[TaskRecord], Awaitable[None]] | None = None,
    ) -> TaskRecord:
        """``submission_payload`` (default ``payload``) is what idempotent retries must match."""

        async def create_task():
            return await self._create(
                task_type,
                resource_id=resource_id,
                payload=payload,
                private_payload=private_payload,
                connection=connection or {},
                ctx=ctx,
                idempotency_key=idempotency_key,
                submission_payload=submission_payload,
                task_id=task_id,
                on_task_created=on_task_created,
            )

        if not idempotency_key and task_id is None:
            return await create_task()
        key = task_id or submission_task_id(ctx, task_type, "", idempotency_key)

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
        task_id: str | None = None,
        on_task_created: Callable[[TaskRecord], Awaitable[None]] | None = None,
    ) -> TaskRecord:
        # QueueFS imports parsers/VikingFS; providers must be importable without them.
        from openviking.storage.queuefs import QueueManager, get_queue_manager

        provider = self._provider(task_type)
        tracker = get_task_tracker()
        request_hash = submission_request_hash(
            payload if submission_payload is None else submission_payload, private_payload
        )
        token = uuid4().hex
        task_id = task_id or (
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
            auth=await self._encrypt_auth(
                ctx.account_id,
                {
                    "openviking_connection": dict(connection),
                    "external_request_private": dict(private_payload or {}),
                },
            ),
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
            if on_task_created is not None:
                await on_task_created(task)
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
                current = await tracker.get(
                    task.task_id,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                )
                if current is not None and not current.is_terminal() and not current.error:
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
        from openviking.storage.queuefs import QueueManager, get_queue_manager

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
            task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
            if task is not None:
                auth = await self._task_auth(task_id, account_id, user_id)
                if auth.get("external_terminal_snapshot") and isinstance(
                    exc, ExternalTaskRetryExhaustedError
                ):
                    # A storage outage during local finalization must not turn
                    # a successful remote task into a failed import.
                    await asyncio.sleep(self._provider(task.task_type).poll_interval_seconds)
                    return False
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
            }:
                self._release(task)
                return True
            if task.status in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}:
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
            task.auth = auth
            terminal = auth.get("external_terminal_snapshot")
            if auth.get("external_cleanup_failure") and terminal is None:
                await self.cancel_recovered(task_id, account_id, user_id)
                return True
            if terminal is not None:
                while not await self._apply_snapshot(
                    ExternalTaskSnapshot(**terminal),
                    task_id=task_id,
                    account_id=account_id,
                    user_id=user_id,
                    auth=auth,
                ):
                    await asyncio.sleep(provider.poll_interval_seconds)
                return True
            try:
                started = await provider.on_start(task)
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
        connection = {
            **self._mapping(auth.get("openviking_connection")),
            "account_id": task.account_id,
        }
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
                TaskPatch(
                    auth={"external_task_id": external_task_id},
                    meta=provider.submission_meta(external_task_id),
                ),
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
                except ExternalTaskRetryExhaustedError as exc:
                    task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
                    if task is None:
                        return
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
            task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}
            or (
                task.status != TaskStatus.CANCELLED
                and (task.result is not None or task.error is not None)
            )
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
        if (
            task.status in {TaskStatus.CANCELLING, TaskStatus.CANCELLED}
            and not auth.get("external_task_id")
            and "external_request_private" not in auth
            and "openviking_connection" not in auth
        ):
            # Cancellation outcome was recorded before its Work was ACKed.
            self._release(task)
            return
        provider = self._provider(task.task_type)
        private_payload = self._mapping(auth.get("external_request_private"))
        connection = {
            **self._mapping(auth.get("openviking_connection")),
            "account_id": task.account_id,
        }
        cancellation = await provider.on_cancel(
            task,
            external_task_id=str(auth.get("external_task_id") or "").strip() or None,
            payload=payload,
            private_payload=private_payload,
            connection=connection,
            failure=failure,
        )
        external_task_id = cancellation.external_task_id
        if external_task_id and auth.get("external_task_id") != external_task_id:
            await tracker.patch_task(
                task_id,
                TaskPatch(auth={"external_task_id": external_task_id}),
                account_id=account_id,
                user_id=user_id,
            )
            auth["external_task_id"] = external_task_id
        snapshot = cancellation.snapshot
        while True:
            if cancellation.terminal_snapshot is not None and snapshot.status in {
                "completed",
                "failed",
                "cancelled",
            }:
                snapshot = cancellation.terminal_snapshot
            if await self._apply_snapshot(
                snapshot,
                task_id=task_id,
                account_id=account_id,
                user_id=user_id,
                auth=auth,
            ):
                return
            await asyncio.sleep(provider.poll_interval_seconds)
            if snapshot.status not in _ACTIVE_STATUSES:
                continue
            if external_task_id is None:
                raise ExternalTaskError(
                    "INVALID_RESPONSE",
                    "Provider cancellation remained active without an external task ID",
                    transient=False,
                )
            snapshot = await provider.get(
                external_task_id,
                connection,
                payload=payload,
                private_payload=private_payload,
            )

    async def _task_auth(self, task_id: str, account_id: str, user_id: str) -> dict[str, Any]:
        auth = await get_task_tracker().get_task_auth(
            task_id,
            account_id=account_id,
            user_id=user_id,
        )
        try:
            for field in _PRIVATE_AUTH_FIELDS:
                value = auth.get(field)
                # Read tasks persisted before credential encryption was introduced.
                if not isinstance(value, dict) or value.get("provider") != _CREDENTIAL_PROVIDER:
                    continue
                ciphertext = base64.b64decode(value["ciphertext"], validate=True)
                if not ciphertext.startswith(MAGIC):
                    raise ValueError("Invalid encrypted credential envelope")
                plaintext = await self._credential_encryptor().decrypt(account_id, ciphertext)
                decoded = json.loads(plaintext)
                if not isinstance(decoded, dict):
                    raise ValueError("Invalid external task credentials")
                auth[field] = decoded
        except Exception as exc:
            raise FailedPreconditionError(
                "Cannot decrypt external task credentials; check encryption key configuration"
            ) from exc
        return auth

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
        if task is None:
            return True
        if task.status == TaskStatus.CANCELLED:
            if snapshot.status in _ACTIVE_STATUSES:
                return False
            if snapshot.status not in {"completed", "failed", "cancelled"}:
                raise ExternalTaskError(
                    "INVALID_RESPONSE",
                    f"Unknown external task status: {snapshot.status}",
                    transient=False,
                )
            task.auth = dict(auth)
            finished = await self._provider(task.task_type).on_finish(
                task, replace(snapshot, status="cancelled")
            )
            if finished.status in _ACTIVE_STATUSES:
                return False
            if finished.status not in {"completed", "failed", "cancelled"}:
                raise ExternalTaskError(
                    "INVALID_RESPONSE",
                    f"Unknown external task status: {finished.status}",
                    transient=False,
                )
            self._release(task)
            return True
        if task.is_terminal() or task.result is not None or task.error is not None:
            # The outcome may already be recorded while QueueFS still owns this Work.
            self._release(task)
            return True
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
            terminal = asdict(snapshot)
            if auth.get("external_terminal_snapshot") != terminal:
                await tracker.patch_task(
                    task_id,
                    TaskPatch(auth={"external_terminal_snapshot": terminal}),
                    account_id=account_id,
                    user_id=user_id,
                )
                auth["external_terminal_snapshot"] = terminal
            task.auth = dict(auth)
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
            snapshot = await self._provider(task.task_type).on_finish(task, snapshot)
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
                    raise ExternalTaskRetryExhaustedError(
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
    "ExternalTaskCancellation",
    "ExternalTaskError",
    "ExternalTaskRetryExhaustedError",
    "ExternalTaskProvider",
    "ExternalTaskService",
    "ExternalTaskSnapshot",
    "get_external_task_service",
    "set_external_task_service",
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
