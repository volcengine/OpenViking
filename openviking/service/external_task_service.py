# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable execution of asynchronous tasks owned by external services."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol
from uuid import uuid4

from openviking.server.identity import RequestContext
from openviking.service.task_tracker import TaskRecord, TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.storage.queuefs import QueueManager, get_queue_manager
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"pending", "running", "cancelling"})


class ExternalTaskError(RuntimeError):
    """A classified provider failure used by the durable retry loop."""

    def __init__(self, code: str, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


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
    ) -> ExternalTaskSnapshot: ...

    async def cancel(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
    ) -> ExternalTaskSnapshot: ...


class ExternalTaskService:
    """Own OV task state while registered providers perform the actual work."""

    def __init__(self) -> None:
        self._providers: dict[str, ExternalTaskProvider] = {}

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

    async def create(
        self,
        task_type: str,
        *,
        resource_id: str | None,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any] | None = None,
        connection: Mapping[str, Any],
        ctx: RequestContext,
    ) -> TaskRecord:
        provider = self._provider(task_type)
        tracker = get_task_tracker()
        task = await tracker.create(
            task_type,
            resource_id=resource_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            task_id=f"{provider.task_id_prefix}{uuid4().hex}",
            meta={"request": dict(payload)},
            auth={
                "openviking_connection": dict(connection),
                "external_request_private": dict(private_payload or {}),
            },
        )
        enqueued = False
        try:
            await get_queue_manager().enqueue(
                QueueManager.EXTERNAL_TASK,
                {
                    "task_id": task.task_id,
                    "account_id": ctx.account_id,
                    "user_id": ctx.user.user_id,
                },
            )
            enqueued = True
            await tracker.update_stage(
                task.task_id,
                "queued",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
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

    async def execute(self, task_id: str, account_id: str, user_id: str) -> None:
        tracker = get_task_tracker()
        task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
        if task is None or task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            return
        # The outcome is persisted before QueueFS ACK removes the owned work.
        # After a crash in that window, let the recovered delivery ACK without
        # resubmitting credentials that have already been cleared.
        if task.result is not None or task.error is not None:
            return

        provider = self._provider(task.task_type)
        payload = task.meta.get("request")
        if not isinstance(payload, dict):
            await tracker.fail(
                task_id,
                "INVALID_ARGUMENT: External task request is missing",
                account_id=account_id,
                user_id=user_id,
            )
            return
        auth = await self._task_auth(task_id, account_id, user_id)
        connection = self._mapping(auth.get("openviking_connection"))
        private_payload = self._mapping(auth.get("external_request_private"))
        external_task_id = str(auth.get("external_task_id") or "").strip() or None
        try:
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
                await tracker.update_task_auth(
                    task_id,
                    {"external_task_id": external_task_id},
                    account_id=account_id,
                    user_id=user_id,
                )
            while True:
                snapshot = await self._retry(
                    lambda: provider.get(external_task_id, connection),
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
                ):
                    return
                await asyncio.sleep(provider.poll_interval_seconds)
        except ExternalTaskError as exc:
            await tracker.fail(
                task_id,
                self._format_error(exc.code, str(exc)),
                account_id=account_id,
                user_id=user_id,
            )
        except asyncio.CancelledError:
            if not tracker.is_cancellation_requested(task_id):
                raise
            await run_to_completion(
                lambda: self._cancel_external(
                    provider,
                    ov_task_id=task_id,
                    payload=payload,
                    private_payload=private_payload,
                    connection=connection,
                    external_task_id=external_task_id,
                    account_id=account_id,
                    user_id=user_id,
                )
            )
            raise

    async def cancel_recovered(self, task_id: str, account_id: str, user_id: str) -> None:
        tracker = get_task_tracker()
        task = await tracker.get(task_id, account_id=account_id, user_id=user_id)
        if task is None or task.stage in {None, "queued"}:
            return
        payload = task.meta.get("request")
        if not isinstance(payload, dict):
            raise ExternalTaskError(
                "INVALID_ARGUMENT",
                "External task request is missing",
                transient=False,
            )
        auth = await self._task_auth(task_id, account_id, user_id)
        await self._cancel_external(
            self._provider(task.task_type),
            ov_task_id=task_id,
            payload=payload,
            private_payload=self._mapping(auth.get("external_request_private")),
            connection=self._mapping(auth.get("openviking_connection")),
            external_task_id=str(auth.get("external_task_id") or "").strip() or None,
            account_id=account_id,
            user_id=user_id,
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
    ) -> bool:
        tracker = get_task_tracker()
        if snapshot.stage is not None or snapshot.meta:
            task = await tracker.get(
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            stage = snapshot.stage or snapshot.status
            meta = snapshot.meta or {}
            if task is not None and (
                task.stage != stage
                or any(key not in task.meta or task.meta[key] != value for key, value in meta.items())
            ):
                await tracker.update_stage(
                    task_id,
                    stage,
                    account_id=account_id,
                    user_id=user_id,
                    meta=meta,
                )
        if snapshot.status in _ACTIVE_STATUSES:
            return False
        if snapshot.status == "completed":
            await tracker.complete(
                task_id,
                snapshot.result or snapshot.meta or {},
                account_id=account_id,
                user_id=user_id,
            )
            return True
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
            return True
        if snapshot.status == "cancelled":
            await tracker.record_cancelled(
                task_id,
                account_id=account_id,
                user_id=user_id,
            )
            return True
        raise ExternalTaskError(
            "INVALID_RESPONSE",
            f"Unknown external task status: {snapshot.status}",
            transient=False,
        )

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
    ) -> None:
        max_attempts = provider.poll_max_attempts
        # Returning allows QueueFS to ACK the work and finalize cancellation.
        # Keep ownership until the provider confirms a terminal state.
        while True:
            try:
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
                    await get_task_tracker().update_task_auth(
                        ov_task_id,
                        {"external_task_id": external_task_id},
                        account_id=account_id,
                        user_id=user_id,
                    )
                snapshot = await self._retry(
                    lambda task_id=external_task_id: provider.cancel(task_id, connection),
                    task_id=ov_task_id,
                    operation_name="cancel",
                    poll_interval=provider.poll_interval_seconds,
                    max_attempts=max_attempts,
                )
                while True:
                    if await self._apply_snapshot(
                        snapshot,
                        task_id=ov_task_id,
                        account_id=account_id,
                        user_id=user_id,
                    ):
                        return
                    await asyncio.sleep(provider.poll_interval_seconds)
                    snapshot = await self._retry(
                        lambda task_id=external_task_id: provider.get(task_id, connection),
                        task_id=ov_task_id,
                        operation_name="poll cancellation",
                        poll_interval=provider.poll_interval_seconds,
                        max_attempts=max_attempts,
                    )
            except ExternalTaskError as exc:
                logger.warning(
                    "External task cancellation will retry task=%s code=%s: %s",
                    ov_task_id,
                    exc.code,
                    exc,
                )
                await asyncio.sleep(provider.poll_interval_seconds)

    @staticmethod
    async def _retry(
        action: Callable[[], Awaitable[Any]],
        *,
        task_id: str,
        operation_name: str,
        poll_interval: float,
        max_attempts: int,
    ) -> Any:
        delay = max(poll_interval, 0.2)
        attempts = 0
        while True:
            try:
                return await action()
            except ExternalTaskError as exc:
                if not exc.transient:
                    raise
                attempts += 1
                if attempts >= max_attempts:
                    raise
                logger.warning(
                    "External task %s will retry task=%s code=%s: %s",
                    operation_name,
                    task_id,
                    exc.code,
                    exc,
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
