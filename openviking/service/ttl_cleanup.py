# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable physical cleanup for TTL-expired events and sessions.

Visibility is enforced synchronously by the read barriers.  This service only
does the slower physical half: it walks the small TTL registry, schedules due
records on QueueFS, and removes a complete lifecycle directory using file
locks. Completion requires all content, vectors, Meta and expiry registration
to be gone. Parent directories and their summaries are untouched.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import NAMESPACE_URL, uuid5

from openviking.core.ttl import (
    OBJECT_TYPE_EVENT,
    OBJECT_TYPE_SESSION,
    hidden_by_ttl,
    ttl_metadata_uri,
)
from openviking.pyagfs.exceptions import AGFSConfigError, AGFSPermissionDeniedError
from openviking.server.identity import RequestContext, Role
from openviking.service.periodic_task import PeriodicTask
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.session.ttl_renewal import reconcile_session_ttl
from openviking.storage.errors import StorageException, VikingDBException
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.ttl_registry import TTLRecord, TTLRegistry, cleanup_not_before
from openviking.utils.time_utils import format_iso8601
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class _CleanupDeferred(Exception):
    def __init__(self, run_at: str, reason: str):
        super().__init__(reason)
        self.run_at = run_at


def _cleanup_settings():
    return get_openviking_config().ttl_cleanup


def _cleanup_task_id(record: TTLRecord, *, attempt: int = 0) -> str:
    """Return a stable task id for one scheduled expiry revision.

    A renewal advances expires_at, so the next deadline receives a new task.
    """
    key = (
        f"openviking:ttl:{record.account_id}:{record.object_uri}:"
        f"{record.expires_at}:{max(0, attempt)}"
    )
    return str(uuid5(NAMESPACE_URL, key))


def _ttl_cleanup_message(
    *,
    record: TTLRecord,
    task_id: Optional[str] = None,
    retry_count: int = 0,
    verify_only: bool = False,
) -> dict[str, Any]:
    return {
        "task_id": task_id or _cleanup_task_id(record, attempt=retry_count),
        "account_id": SYSTEM_TASK_ACCOUNT_ID,
        "user_id": SYSTEM_TASK_USER_ID,
        "target": asdict(record),
        "retry_count": max(0, int(retry_count)),
        "verify_only": verify_only,
    }


class TTLCleanupService:
    """Own the TTL cleanup consumer and registry scheduler."""

    MAX_FAST_RETRIES = 3

    def __init__(
        self,
        *,
        service: Any,
        check_interval: Optional[float] = None,
    ) -> None:
        self._service = service
        self._closed = False
        self._scheduler = TTLCleanupScheduler(service, check_interval=check_interval)

    async def initialize(self) -> None:
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.TTL_CLEANUP)
        queue.set_dequeue_handler(_TTLCleanupProcessor(self))
        await self._scheduler.start()

    async def close(self) -> None:
        self._closed = True
        await self._scheduler.stop()

    def _check_running(self) -> None:
        config = _cleanup_settings()
        if getattr(self, "_closed", False) or not config.enabled:
            raise _CleanupDeferred(
                format_iso8601(
                    datetime.now(timezone.utc) + timedelta(seconds=config.check_interval_seconds)
                ),
                "paused",
            )

    async def _defer(self, record, message, tracker, owner, deferred):
        retry_count = int(message.get("retry_count", 0))
        saved = await self._service.viking_fs.ttl_registry.defer_retry(
            record,
            retry_count=retry_count,
            expected_retry_count=retry_count,
            task_id=message["task_id"],
            next_retry_at=deferred.run_at,
            verify_only=bool(message.get("verify_only", False)),
        )
        if not saved:
            await tracker.complete(
                message["task_id"],
                {"deleted": False, "skipped": "superseded"},
                **owner,
            )
            return ProcessResult.success()
        await tracker.update_stage(message["task_id"], str(deferred), **owner)
        return ProcessResult.requeued()

    async def _process(self, message: dict[str, Any]) -> ProcessResult:
        """Settle or durably replace one cleanup delivery."""
        task_id = message["task_id"]
        owner = {"account_id": message["account_id"], "user_id": message["user_id"]}
        record = TTLRecord.from_dict(message["target"])

        tracker = get_task_tracker()
        task = await tracker.create(
            "ttl_cleanup",
            resource_id=record.object_uri,
            task_id=task_id,
            **owner,
        )
        # The durable deadline index is authoritative even after task history
        # expires or an older delivery was marked complete.
        if task.status is TaskStatus.COMPLETED:
            current = await self._service.viking_fs.ttl_registry.get(
                record.account_id, record.object_uri
            )
            if current != record:
                return ProcessResult.success()
        if task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            # TaskTracker terminal states are immutable.  A delivery restored
            # from an older implementation therefore needs a fresh task id;
            # otherwise physical cleanup may succeed while the persisted task
            # remains permanently failed/cancelled.  The deterministic attempt
            # id keeps duplicate recovery deliveries idempotent.
            retry_count = int(message.get("retry_count", 0)) + 1
            await self._service.viking_fs.ttl_registry.defer_retry(
                record,
                retry_count=retry_count,
                expected_retry_count=retry_count - 1,
                task_id=_cleanup_task_id(record, attempt=retry_count),
                next_retry_at=format_iso8601(datetime.now(timezone.utc) + timedelta(days=1)),
                verify_only=bool(message.get("verify_only", False)),
            )
            return ProcessResult.requeued()

        try:
            self._check_running()
            result = await run_to_completion(lambda: self._run_delivery(record, message))
        except _CleanupDeferred as deferred:
            return await self._defer(record, message, tracker, owner, deferred)
        if result is None:
            return ProcessResult.requeued()

        await tracker.complete(task_id, result, **owner)
        return ProcessResult.success()

    async def _defer_retry(self, record: TTLRecord, message: dict, exc: Exception) -> None:
        """One retry owner: persist bounded backoff before acknowledging QueueFS."""
        confirmation = isinstance(exc, StorageException) and exc.action == "confirm_delete"
        cause = exc.__cause__ if confirmation and exc.__cause__ is not None else exc
        status = getattr(cause, "status_code", None)
        permanent = (
            isinstance(
                cause,
                (
                    AGFSConfigError,
                    AGFSPermissionDeniedError,
                    PermissionError,
                    PermissionDeniedError,
                    InvalidArgumentError,
                    ValueError,
                ),
            )
            or (isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 429))
            or (
                isinstance(cause, VikingDBException)
                and cause.action is not None
                and not cause.retryable
            )
        )
        retry_count = int(message.get("retry_count", 0)) + 1
        cooldown = permanent or retry_count > self.MAX_FAST_RETRIES
        delay = 86400.0 if cooldown else 30.0 * 2 ** (retry_count - 1)
        next_retry_at = format_iso8601(
            datetime.now(timezone.utc) + timedelta(seconds=delay * random.uniform(1.0, 1.2))
        )
        # Give an accepted deletion one confirmation-only retry. If it still
        # has residue, the following attempt reissues deletion to repair a real
        # partial/no-op delete rather than polling forever.
        verify_only = confirmation and not message.get("verify_only", False)
        deferred = await self._service.viking_fs.ttl_registry.defer_retry(
            record,
            retry_count=retry_count,
            expected_retry_count=retry_count - 1,
            task_id=message["task_id"],
            next_retry_at=next_retry_at,
            verify_only=verify_only,
        )
        if deferred:
            stage = "retry_cooldown" if cooldown else "retrying"
            await get_task_tracker().update_stage(
                message["task_id"],
                stage,
                account_id=message["account_id"],
                user_id=message["user_id"],
                meta={
                    "last_error": str(exc),
                    "retry_count": retry_count,
                    "next_retry_at": next_retry_at,
                    "verify_only": verify_only,
                },
            )
            logger.warning(
                "TTL cleanup %s uri=%s retry_count=%d verify_only=%s next_retry_at=%s: %s",
                stage,
                record.object_uri,
                retry_count,
                verify_only,
                next_retry_at,
                exc,
            )

    async def _run_delivery(self, scheduled: TTLRecord, message: dict) -> Optional[dict]:
        """Fence duplicate deliveries and persist retry progress under the object lock."""
        viking_fs = self._service.viking_fs
        registry = viking_fs.ttl_registry
        from openviking.core.ttl import ttl_object_for_uri

        if ttl_object_for_uri(scheduled.object_uri) != (
            scheduled.object_type,
            scheduled.object_uri,
        ):
            await registry.remove_if_current(scheduled)
            return {"deleted": False, "skipped": "out_of_scope"}
        try:
            ctx, lease = await self._acquire_object_lock(scheduled)
        except Exception as exc:
            await self._defer_retry(scheduled, message, exc)
            return None
        try:
            item = await registry.get_scheduled(scheduled.account_id, scheduled.object_uri)
            if item is None or TTLRecord.from_dict(item["payload"]["record"]) != scheduled:
                return {"deleted": False, "skipped": "superseded"}
            if item["payload"].get("retry_count", 0) != message.get("retry_count", 0):
                # Another delivery already persisted the next attempt. Do not
                # reset its backoff or complete its still-running business task.
                return None
            message = {**message, "verify_only": bool(item["payload"].get("verify_only"))}
            await get_task_tracker().start(
                message["task_id"],
                stage="confirm_cleanup" if message["verify_only"] else "strict_cleanup",
                account_id=message["account_id"],
                user_id=message["user_id"],
            )
            try:
                return await self._cleanup_record(
                    scheduled, ctx, lease, verify_only=message["verify_only"]
                )
            except _CleanupDeferred:
                raise
            except Exception as exc:
                await self._defer_retry(scheduled, message, exc)
                return None
        finally:
            await viking_fs._async_agfs.pathlock_release(lease)

    async def _acquire_object_lock(self, scheduled: TTLRecord) -> tuple[RequestContext, Any]:
        viking_fs = self._service.viking_fs
        ctx = RequestContext(
            user=UserIdentifier(scheduled.account_id, scheduled.user_id or SYSTEM_TASK_USER_ID),
            role=Role.ROOT,
        )
        metadata_path = viking_fs._uri_to_path(
            ttl_metadata_uri(scheduled.object_type, scheduled.object_uri), ctx=ctx
        )
        requests = [{"path": metadata_path, "kind": "exact"}]
        requests.append(
            {
                "path": TTLRegistry.vector_lock_path(scheduled.account_id, scheduled.object_uri),
                "kind": "exact",
            }
        )
        if scheduled.object_type == OBJECT_TYPE_SESSION:
            # Reuse the existing session mutation mutex; exact acquisition does
            # not traverse or block every child as a tree lock would.
            requests.append(
                {"path": viking_fs._uri_to_path(scheduled.object_uri, ctx=ctx), "kind": "exact"}
            )
        lease = await viking_fs._async_agfs.pathlock_acquire_batch(requests, timeout_secs=0.0)
        return ctx, lease

    async def _cleanup_record(
        self,
        scheduled: TTLRecord,
        ctx: Optional[RequestContext] = None,
        lease: Any = None,
        *,
        verify_only: bool = False,
    ) -> dict[str, Any]:
        """Delete one expired directory under its metadata lock."""
        if ctx is None and lease is None:
            owned_ctx, owned_lease = await self._acquire_object_lock(scheduled)
            try:
                return await self._cleanup_record(
                    scheduled,
                    owned_ctx,
                    owned_lease,
                    verify_only=verify_only,
                )
            finally:
                await self._service.viking_fs._async_agfs.pathlock_release(owned_lease)
        if ctx is None or lease is None:
            raise ValueError("ctx and lease must be provided together")

        viking_fs = self._service.viking_fs
        registry = viking_fs.ttl_registry
        from openviking.core.ttl import ttl_object_for_uri

        if ttl_object_for_uri(scheduled.object_uri) != (
            scheduled.object_type,
            scheduled.object_uri,
        ):
            await registry.remove_if_current(scheduled)
            return {"deleted": False, "skipped": "out_of_scope"}
        self._check_running()
        registered = await registry.get(scheduled.account_id, scheduled.object_uri)
        if registered != scheduled:
            return {"deleted": False, "skipped": "superseded"}

        if scheduled.object_type == OBJECT_TYPE_SESSION:
            await reconcile_session_ttl(
                viking_fs,
                ctx,
                session_uri=scheduled.object_uri,
                lease_ref=lease,
            )
        live = await self._read_live_record(scheduled, ctx)
        if live is not None and not hidden_by_ttl(live.expires_at):
            if live.expires_at:
                if live != registered:
                    await registry.upsert(live)
            else:
                await registry.remove_if_current(scheduled)
            return {"deleted": False, "skipped": "renewed" if live.expires_at else "unmanaged"}

        run_at = cleanup_not_before(live or registered)
        if not hidden_by_ttl(run_at):
            raise _CleanupDeferred(run_at, "waiting_cleanup_window")

        # One owner deadline covers every file and vector, including L0/L1.
        await viking_fs.rm(
            scheduled.object_uri,
            recursive=True,
            ctx=ctx,
            lease_ref=lease,
            strict=True,
            file_locks=True,
            **({"verify_only": True} if verify_only else {}),
        )
        removed = await registry.remove_if_current(scheduled)
        if (
            not removed
            and await registry.get(scheduled.account_id, scheduled.object_uri) is not None
        ):
            raise RuntimeError(
                f"TTL registry record changed before cleanup completion: {scheduled.object_uri}"
            )
        # The only process-local VikingFS cache stores count-based engine
        # selection hints.  Clear it after physical deletion so no stale
        # scope count survives cleanup.
        viking_fs._count_cache.clear()
        return {"deleted": True, "source_missing": live is None}

    async def _read_live_record(
        self, scheduled: TTLRecord, ctx: RequestContext
    ) -> Optional[TTLRecord]:
        viking_fs = self._service.viking_fs
        from openviking.storage.directory_ttl import read_directory_fields

        fields = await read_directory_fields(viking_fs, scheduled.object_uri, ctx=ctx)
        if not fields:
            return None
        if not isinstance(fields, dict):
            raise ValueError(f"Invalid TTL metadata for {scheduled.object_uri}")
        return TTLRecord(
            object_uri=scheduled.object_uri,
            object_type=scheduled.object_type,
            account_id=scheduled.account_id,
            user_id=scheduled.user_id,
            expires_at=str(fields.get("expires_at") or ""),
        )


class TTLCleanupScheduler(PeriodicTask):
    """Enqueue due records after each object's stable cleanup jitter window."""

    DEFAULT_CHECK_INTERVAL = 30.0

    def __init__(
        self,
        service: Any,
        *,
        check_interval: Optional[float] = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._service = service
        self._interval_override = check_interval
        self._check_interval = (
            self.DEFAULT_CHECK_INTERVAL if check_interval is None else float(check_interval)
        )
        super().__init__(interval=self._check_interval, sleep=sleep)

    def _next_interval(self) -> float:
        config = _cleanup_settings()
        interval = (
            config.check_interval_seconds
            if self._interval_override is None
            else self._interval_override
        )
        return interval + random.uniform(0.0, config.scan_jitter_seconds)

    async def _scan_once(self) -> None:
        config = _cleanup_settings()
        if not config.enabled:
            return
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.TTL_CLEANUP)
        # Drain the durable queue before leasing another page. Otherwise a slow
        # backend can accumulate repeated deliveries after claim leases expire.
        get_status = getattr(queue, "get_status", None)
        if callable(get_status):
            status = await get_status()
            if status.pending or status.in_progress:
                return
        scheduled = 0
        async for record, payload in self._service.viking_fs.ttl_registry.claim_due(
            now=datetime.now(timezone.utc),
            limit=config.batch_size,
            max_bytes=config.max_batch_bytes,
            time_budget=config.scan_time_budget_seconds,
        ):
            await queue.enqueue(
                _ttl_cleanup_message(
                    record=record,
                    task_id=payload.get("task_id"),
                    retry_count=int(payload.get("retry_count", 0)),
                    verify_only=bool(payload.get("verify_only", False)),
                )
            )
            scheduled += 1
        if scheduled:
            logger.info("TTLCleanupScheduler scheduled=%d", scheduled)


class _TTLCleanupProcessor(DequeueHandlerBase):
    """Execute cleanup on the QueueFS worker, like SessionCommitProcessor."""

    def __init__(self, cleanup_service: TTLCleanupService) -> None:
        self._cleanup_service = cleanup_service

    @staticmethod
    def _parse_message(data: dict[str, Any]) -> dict[str, Any]:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("Invalid TTL cleanup message")
        target = payload.get("target")
        if not isinstance(target, dict):
            raise ValueError("Invalid TTL cleanup target")
        required_owner = (
            payload.get("task_id") and payload.get("account_id") and payload.get("user_id")
        )
        if not required_owner:
            raise ValueError("Invalid TTL cleanup owner")
        object_type = str(target.get("object_type") or "")
        object_uri = str(target.get("object_uri") or "")
        if object_type not in (
            OBJECT_TYPE_EVENT,
            OBJECT_TYPE_SESSION,
        ):
            raise ValueError("Invalid TTL cleanup object type")
        if not object_uri:
            raise ValueError("Invalid TTL cleanup object URI")
        return {
            "task_id": str(payload["task_id"]),
            "account_id": str(payload["account_id"]),
            "user_id": str(payload["user_id"]),
            "retry_count": max(0, int(payload.get("retry_count", 0))),
            "verify_only": bool(payload.get("verify_only", False)),
            "target": {
                "object_type": object_type,
                "object_uri": object_uri,
                "account_id": str(target.get("account_id") or ""),
                "user_id": str(target.get("user_id") or ""),
                "expires_at": str(target.get("expires_at") or ""),
            },
        }

    async def on_dequeue(self, data: Optional[dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        try:
            message = self._parse_message(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        return await self._cleanup_service._process(message)

    async def on_cancelled(self, data: Optional[dict[str, Any]]) -> ProcessResult:
        """Recover legacy terminal cleanup tasks instead of dropping work."""
        return await self.on_dequeue(data)


async def setup_ttl_cleanup(*, service: Any) -> Optional[TTLCleanupService]:
    if service.viking_fs is None or service._queue_manager is None:
        return None
    cleanup_service = TTLCleanupService(
        service=service,
    )
    await cleanup_service.initialize()
    service._ttl_cleanup_service = cleanup_service
    return cleanup_service
