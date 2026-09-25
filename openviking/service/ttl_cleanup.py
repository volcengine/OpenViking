# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable physical cleanup for TTL-expired events and sessions.

Visibility is enforced synchronously by the read barriers.  This service only
does the slower physical half: it walks the small TTL registry, schedules due
records on QueueFS, and removes one exact object incarnation under its path
lock. A task completes only after L2 content, its vectors, and its registry
record are gone. L0/L1 summaries and their directory scaffolding are retained.
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
    OBJECT_TYPE_RESOURCE,
    OBJECT_TYPE_RESOURCE_FILE,
    OBJECT_TYPE_SESSION,
    hidden_by_ttl,
    ttl_metadata_uri,
)
from openviking.pyagfs.exceptions import AGFSConfigError, AGFSPermissionDeniedError
from openviking.server.error_mapping import is_storage_not_found
from openviking.server.identity import RequestContext, Role
from openviking.service.periodic_task import PeriodicTask
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.service.task_tracker_concurrency import OwnerLoopDispatcher, run_to_completion
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking.session.ttl_fence import reconcile_session_ttl
from openviking.storage.errors import StorageException, VikingDBException
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.ttl_registry import TTLRecord, cleanup_not_before
from openviking.utils.content_hash import content_md5
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

    Session renewal keeps the incarnation generation but advances expires_at.
    Including both prevents a completed pre-renewal no-op from suppressing the
    next legitimate cleanup while still deduplicating duplicate scan passes.
    """
    key = (
        f"openviking:ttl:{record.account_id}:{record.object_uri}:"
        f"{record.generation}:{record.expires_at}:{max(0, attempt)}"
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
        service_loop: asyncio.AbstractEventLoop,
        check_interval: Optional[float] = None,
    ) -> None:
        self._service = service
        self._service_loop = service_loop
        self._closed = False
        self._scheduler = TTLCleanupScheduler(service, check_interval=check_interval)

    async def initialize(self) -> None:
        queue_manager = self._service._queue_manager
        queue = queue_manager.get_queue(queue_manager.TTL_CLEANUP)
        queue.set_dequeue_handler(_TTLCleanupProcessor(self, self._service_loop))
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
        # Only a completed cleanup proves that physical data and the registry
        # projection are both gone. A persisted FAILED/CANCELLED task from an
        # older process must not make a recovered QueueFS delivery ACK without
        # retrying the strict delete.
        if task.status is TaskStatus.COMPLETED:
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
        try:
            ctx, lease = await self._acquire_object_lock(scheduled)
        except Exception as exc:
            await self._defer_retry(scheduled, message, exc)
            return None
        try:
            item = await registry.get_scheduled(scheduled.account_id, scheduled.object_uri)
            if item is None or item["payload"]["record"] != asdict(scheduled):
                return {"deleted": False, "skipped": "stale_registry_generation"}
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
        object_path = viking_fs._uri_to_path(scheduled.object_uri, ctx=ctx)
        if scheduled.object_type == OBJECT_TYPE_SESSION:
            lease = await viking_fs._async_agfs.pathlock_acquire_tree(object_path)
        else:
            lease = await viking_fs._async_agfs.pathlock_acquire_batch(
                [
                    {
                        "path": object_path,
                        "kind": "exact",
                    },
                    *(
                        [
                            {
                                "path": viking_fs._uri_to_path(
                                    ttl_metadata_uri(scheduled.object_type, scheduled.object_uri),
                                    ctx=ctx,
                                ),
                                "kind": "exact",
                            }
                        ]
                        if scheduled.object_type in {OBJECT_TYPE_RESOURCE, OBJECT_TYPE_RESOURCE_FILE}
                        else []
                    ),
                ]
            )
        return ctx, lease

    async def _cleanup_record(
        self,
        scheduled: TTLRecord,
        ctx: Optional[RequestContext] = None,
        lease: Any = None,
        *,
        verify_only: bool = False,
    ) -> dict[str, Any]:
        """Strictly delete one generation under the caller's object lock."""
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
        self._check_running()
        registered = await registry.get(scheduled.account_id, scheduled.object_uri)
        if registered is None or registered.generation != scheduled.generation:
            return {"deleted": False, "skipped": "stale_registry_generation"}

        if scheduled.object_type == OBJECT_TYPE_SESSION:
            await reconcile_session_ttl(
                viking_fs,
                ctx,
                session_uri=scheduled.object_uri,
                generation=scheduled.generation,
                lease_ref=lease,
            )
        live = await self._read_live_record(scheduled, ctx)
        if live is not None and live.generation != scheduled.generation:
            # An import/restore may have replaced the source without going
            # through the normal registry-first writer.  Repair the
            # projection when the replacement has its own complete TTL
            # snapshot; otherwise discard only the stale old projection.
            if live.generation and live.expires_at:
                await registry.upsert(live)
            else:
                await registry.remove_if_generation(
                    scheduled.account_id, scheduled.object_uri, scheduled.generation
                )
            return {"deleted": False, "skipped": "stale_object_generation"}
        if live is not None and not hidden_by_ttl(live.expires_at):
            if live.expires_at != registered.expires_at:
                await registry.upsert(live)
            return {"deleted": False, "skipped": "renewed"}

        run_at = cleanup_not_before(live or registered)
        if not hidden_by_ttl(run_at):
            raise _CleanupDeferred(run_at, "waiting_cleanup_window")

        if scheduled.object_type == OBJECT_TYPE_RESOURCE:
            # Older builds registered a directory as one lifecycle owner. A
            # directory is now only a default-policy boundary, so retire only
            # that legacy fence and never recurse into independently-lived files.
            metadata_uri = ttl_metadata_uri(scheduled.object_type, scheduled.object_uri)
            try:
                await viking_fs.remove_files(metadata_uri, ctx=ctx, lease_ref=lease)
            except Exception as exc:
                if not is_storage_not_found(exc):
                    raise
            await registry.remove_if_generation(
                scheduled.account_id, scheduled.object_uri, scheduled.generation
            )
            return {"deleted": False, "skipped": "legacy_resource_directory"}

        if scheduled.object_type == OBJECT_TYPE_RESOURCE_FILE and live is not None:
            # Retain a source fingerprint in the sidecar tombstone. Watch skips
            # unchanged expired sources but accepts a genuinely updated version.
            from openviking.storage.resource_ttl import (
                read_resource_fields,
                write_resource_fields,
            )

            try:
                fields = await read_resource_fields(
                    viking_fs, OBJECT_TYPE_RESOURCE_FILE, scheduled.object_uri, ctx=ctx
                )
                if fields is not None and not fields.get("content_md5"):
                    read_bytes = getattr(viking_fs, "read_file_bytes", None)
                    if not callable(read_bytes):
                        raise AttributeError("binary resource reads are unavailable")
                    raw = await read_bytes(scheduled.object_uri, ctx=ctx, include_expired=True)
                    fields["content_md5"] = content_md5(raw)
                    await write_resource_fields(
                        viking_fs,
                        OBJECT_TYPE_RESOURCE_FILE,
                        scheduled.object_uri,
                        fields,
                        ctx=ctx,
                        lease_ref=lease,
                    )
            except Exception as exc:
                logger.debug(
                    "Unable to persist TTL tombstone fingerprint for %s: %s",
                    scheduled.object_uri,
                    exc,
                )

        # Missing source still requires strict vector cleanup.  Passing the
        # already-held lease makes the live re-check and the whole delete
        # one critical section; writers cannot renew or recreate between.
        remove = (
            self._service.fs.rm
            if scheduled.object_type == OBJECT_TYPE_RESOURCE_FILE
            else viking_fs.rm
        )
        await remove(
            scheduled.object_uri,
            recursive=scheduled.object_type == OBJECT_TYPE_SESSION,
            ctx=ctx,
            lease_ref=lease,
            strict=True,
            preserve_summaries=True,
            **({"verify_only": True} if verify_only else {}),
        )
        removed = await registry.remove_if_generation(
            scheduled.account_id, scheduled.object_uri, scheduled.generation
        )
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
        read_uri = ttl_metadata_uri(scheduled.object_type, scheduled.object_uri)
        try:
            raw = await viking_fs.read_file(read_uri, ctx=ctx, include_expired=True)
        except Exception as exc:
            if is_storage_not_found(exc):
                return None
            raise
        if scheduled.object_type != OBJECT_TYPE_EVENT:
            fields = json.loads(raw)
        else:
            fields = parse_memory_file_with_fields(raw)
        if not isinstance(fields, dict):
            raise ValueError(f"Invalid TTL metadata for {scheduled.object_uri}")
        return TTLRecord(
            object_uri=scheduled.object_uri,
            object_type=scheduled.object_type,
            account_id=scheduled.account_id,
            user_id=scheduled.user_id,
            expires_at=str(fields.get("expires_at") or ""),
            generation=str(fields.get("ttl_generation") or ""),
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
    """Single-consumer QueueFS bridge into the service owner loop."""

    def __init__(
        self, cleanup_service: TTLCleanupService, service_loop: asyncio.AbstractEventLoop
    ) -> None:
        self._cleanup_service = cleanup_service
        self._dispatcher = OwnerLoopDispatcher(service_loop)

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
        generation = str(target.get("generation") or "")
        if object_type not in (
            OBJECT_TYPE_EVENT,
            OBJECT_TYPE_SESSION,
            OBJECT_TYPE_RESOURCE,
            OBJECT_TYPE_RESOURCE_FILE,
        ):
            raise ValueError("Invalid TTL cleanup object type")
        if not object_uri or not generation:
            raise ValueError("Invalid TTL cleanup object fence")
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
                "generation": generation,
            },
        }

    async def on_dequeue(self, data: Optional[dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        try:
            message = self._parse_message(data)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        return await self._dispatcher.run(lambda: self._cleanup_service._process(message))

    async def on_cancelled(self, data: Optional[dict[str, Any]]) -> ProcessResult:
        """Recover legacy terminal cleanup tasks instead of dropping work."""
        return await self.on_dequeue(data)


async def setup_ttl_cleanup(*, service: Any) -> Optional[TTLCleanupService]:
    if service.viking_fs is None or service._queue_manager is None:
        return None
    cleanup_service = TTLCleanupService(
        service=service,
        service_loop=asyncio.get_running_loop(),
    )
    await cleanup_service.initialize()
    service._ttl_cleanup_service = cleanup_service
    return cleanup_service
