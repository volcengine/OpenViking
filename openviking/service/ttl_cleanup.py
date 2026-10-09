# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scan directory deadlines and use QueueFS for strict, daily physical cleanup."""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from openviking.core.ttl import (
    OBJECT_TYPE_SESSION,
    hidden_by_ttl,
    ttl_metadata_uri,
    ttl_object_for_uri,
)
from openviking.server.identity import RequestContext, Role
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
from openviking.service.task_tracker import get_task_tracker
from openviking.service.task_tracker_concurrency import run_to_completion
from openviking.service.ttl_policy import _directories, _owners, _roots
from openviking.storage.directory_ttl import read_directory_fields
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.ttl_registry import TTLRecord, TTLRegistry
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def _cleanup_settings():
    return get_openviking_config().ttl_cleanup


def _ttl_cleanup_message(*, record: TTLRecord, task_id: Optional[str] = None) -> dict[str, Any]:
    return {
        "task_id": task_id or str(uuid4()),
        "account_id": SYSTEM_TASK_ACCOUNT_ID,
        "user_id": SYSTEM_TASK_USER_ID,
        "target": asdict(record),
    }


class TTLCleanupService:
    def __init__(self, *, service: Any) -> None:
        self._service = service
        self._closed = False
        self._scheduler = TTLCleanupScheduler(service)

    async def initialize(self) -> None:
        manager = self._service._queue_manager
        manager.get_queue(manager.TTL_CLEANUP).set_dequeue_handler(_TTLCleanupProcessor(self))
        await self._scheduler.start()

    async def close(self) -> None:
        self._closed = True
        await self._scheduler.stop()

    async def _process(self, message: dict[str, Any]) -> ProcessResult:
        try:
            record = TTLRecord.from_dict(message["target"])
            task_id = message["task_id"]
            if not task_id or not record.account_id or not record.object_uri:
                raise ValueError("Invalid TTL cleanup message")
        except (KeyError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        # Cancellation must not release the object lease during physical I/O.
        return await run_to_completion(lambda: self._run_delivery(record, str(task_id)))

    async def _run_delivery(self, record: TTLRecord, task_id: str) -> ProcessResult:
        if ttl_object_for_uri(record.object_uri) != (record.object_type, record.object_uri):
            return ProcessResult.failed("Invalid TTL cleanup target")
        tracker = get_task_tracker()
        owner: dict[str, Any] = {
            "account_id": SYSTEM_TASK_ACCOUNT_ID,
            "user_id": SYSTEM_TASK_USER_ID,
        }
        lease = None
        try:
            await tracker.create(
                "ttl_cleanup", resource_id=record.object_uri, task_id=task_id, **owner
            )
            await tracker.start(task_id, stage="strict_cleanup", **owner)
            ctx, lease = await self._acquire_object_lock(record)
            result = await self._cleanup_record(record, ctx, lease)
            await tracker.complete(task_id, result, **owner)
            return ProcessResult.success(result)
        except (LockAcquisitionError, ResourceBusyError):
            # The owner deadline is untouched. The next scan will try again.
            await tracker.complete(task_id, {"deleted": False, "skipped": "busy"}, **owner)
            return ProcessResult.success()
        except Exception as exc:
            await tracker.fail(task_id, str(exc), **owner)
            logger.warning(
                "TTL cleanup failed; retained for next scan: %s: %s", record.object_uri, exc
            )
            return ProcessResult.failed(str(exc))
        finally:
            if lease is not None:
                await self._service.viking_fs._async_agfs.pathlock_release(lease)

    async def _acquire_object_lock(self, scheduled: TTLRecord) -> tuple[RequestContext, Any]:
        fs = self._service.viking_fs
        ctx = RequestContext(
            user=UserIdentifier(scheduled.account_id, SYSTEM_TASK_USER_ID),
            role=Role.ROOT,
        )
        metadata_path = fs._uri_to_path(
            ttl_metadata_uri(scheduled.object_type, scheduled.object_uri), ctx=ctx
        )
        requests = [
            {"path": metadata_path, "kind": "exact"},
            {
                "path": TTLRegistry.vector_lock_path(scheduled.account_id, scheduled.object_uri),
                "kind": "exact",
            },
        ]
        if scheduled.object_type == OBJECT_TYPE_SESSION:
            requests.append(
                {"path": fs._uri_to_path(scheduled.object_uri, ctx=ctx), "kind": "exact"}
            )
        lease = await fs._async_agfs.pathlock_acquire_batch(requests, timeout_secs=0.0)
        return ctx, lease

    async def _cleanup_record(
        self, scheduled: TTLRecord, ctx: RequestContext, lease: Any
    ) -> dict[str, Any]:
        fs = self._service.viking_fs
        if self._closed or not _cleanup_settings().enabled:
            return {"deleted": False, "skipped": "paused"}
        fields = await read_directory_fields(fs, scheduled.object_uri, ctx=ctx)
        if not fields:
            # Exact locks may recreate an empty parent for an old queue item.
            await fs._remove_empty_lock_directory(fs._uri_to_path(scheduled.object_uri, ctx=ctx))
        if not hidden_by_ttl(fields.get("expires_at")):
            return {"deleted": False, "skipped": "live_or_unmanaged"}
        await fs.rm(
            scheduled.object_uri,
            recursive=True,
            ctx=ctx,
            lease_ref=lease,
            strict=True,
            file_locks=True,
        )
        fs._count_cache.clear()
        return {"deleted": True}


class TTLCleanupScheduler:
    """Finish one paced pass, then wait a day; deadlines are the retry source."""

    def __init__(self, service: Any):
        self._service = service
        self._task = None
        self._candidates = None
        self._next_scan_at = datetime.min.replace(tzinfo=timezone.utc)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                config = _cleanup_settings()
                await asyncio.sleep(
                    config.check_interval_seconds + random.uniform(0.0, config.scan_jitter_seconds)
                )
                await self._scan_once()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("TTL cleanup scan failed")

    async def _iter_candidates(self):
        fs = self._service.viking_fs
        async for account_id in _directories(fs, "/local"):
            if not await fs.ttl_registry.account_may_have_records(account_id):
                continue
            ctx = RequestContext(
                user=UserIdentifier(account_id, SYSTEM_TASK_USER_ID), role=Role.ROOT
            )
            try:
                async for root in _roots(fs, ctx):
                    try:
                        async for uri in _owners(fs, root, ctx):
                            yield ctx, uri
                    except Exception:
                        logger.warning("Failed to list TTL owners: %s", root, exc_info=True)
            except Exception:
                logger.warning("Failed to list TTL roots: %s", account_id, exc_info=True)

    async def _scan_once(self) -> None:
        config = _cleanup_settings()
        if not config.enabled or datetime.now(timezone.utc) < self._next_scan_at:
            return
        manager = self._service._queue_manager
        queue = manager.get_queue(manager.TTL_CLEANUP)
        status = await queue.get_status()
        if status.pending or status.in_progress:
            return
        if self._candidates is None:
            self._candidates = self._iter_candidates()
        deadline = time.monotonic() + config.scan_time_budget_seconds
        for _ in range(config.batch_size):
            try:
                ctx, uri = await anext(self._candidates)
            except StopAsyncIteration:
                self._candidates = None
                self._next_scan_at = datetime.now(timezone.utc) + timedelta(
                    seconds=config.sweep_interval_seconds
                )
                break
            except Exception:
                self._candidates = None
                raise
            try:
                fields = await read_directory_fields(self._service.viking_fs, uri, ctx=ctx)
                if hidden_by_ttl(fields.get("expires_at")):
                    kind, _ = ttl_object_for_uri(uri)
                    record = TTLRecord(object_uri=uri, object_type=kind, account_id=ctx.account_id)
                    await queue.enqueue(_ttl_cleanup_message(record=record))
            except Exception:
                # One corrupt owner or failed enqueue must not starve its siblings.
                # Metadata remains discoverable after either failure or restart.
                logger.warning("Failed to inspect or enqueue TTL owner: %s", uri, exc_info=True)
            if time.monotonic() >= deadline:
                break


class _TTLCleanupProcessor(DequeueHandlerBase):
    def __init__(self, cleanup_service: TTLCleanupService) -> None:
        self._cleanup_service = cleanup_service

    async def on_dequeue(self, data: Optional[dict[str, Any]]) -> ProcessResult:
        if not data:
            return ProcessResult.success()
        try:
            payload = data.get("data", data)
            if isinstance(payload, str):
                payload = json.loads(payload)
        except (KeyError, TypeError, ValueError) as exc:
            return ProcessResult.failed(str(exc))
        return await self._cleanup_service._process(payload)


async def setup_ttl_cleanup(*, service: Any) -> Optional[TTLCleanupService]:
    if service.viking_fs is None or service._queue_manager is None:
        return None
    cleanup = TTLCleanupService(service=service)
    await cleanup.initialize()
    service._ttl_cleanup_service = cleanup
    return cleanup
