# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Expiry publisher: source buckets to published callback buckets."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Sequence

from .api import SUPPORTED_ITEM_TYPES, ExpiryError
from .config import ExpiryConfig
from .keys import decode_item_id
from .metrics import ExpiryMetrics
from .models import (
    MINUTE_MS,
    BucketRef,
    OwnerRole,
    PublishResult,
    StoreOperationError,
    bucket_minute_ms,
)
from .redis_store import RedisExpiryStore
from .shard_runner import ShardExecution, ShardRunner


async def _wait_until_next_round(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), delay)
    except asyncio.TimeoutError:
        pass


class ExpiryPublisher:
    def __init__(
        self,
        store: RedisExpiryStore,
        runner: ShardRunner,
        config: ExpiryConfig,
        metrics: ExpiryMetrics,
    ) -> None:
        self._store = store
        self._runner = runner
        self._config = config
        self._metrics = metrics

    async def run(self, stop: asyncio.Event) -> None:
        """Own shard traversal and scheduling cadence, not lease lifecycle."""
        while not stop.is_set():
            for shard_id in range(self._config.shard_count):
                if stop.is_set():
                    break
                try:
                    await self._runner.run_if_acquired(
                        OwnerRole.SCHEDULER, shard_id, self.process_shard, stop
                    )
                except StoreOperationError as exc:
                    if exc.code == "shard_not_stopped":
                        raise
                    self._metrics.count("timer_shard_round", outcome="failed")
                except Exception:
                    self._metrics.count("timer_shard_round", outcome="failed")
            if not stop.is_set():
                delay = max(0.05, 60.0 - time.time() % 60.0)
                await _wait_until_next_round(stop, delay)

    async def process_shard(self, lease: ShardExecution, stop: asyncio.Event) -> None:
        while not stop.is_set():
            lease.ensure_active()
            window = await self._store.read_scan_window(lease.owner.shard_id)
            progress = window.progress_ms
            lease.ensure_active()
            await self._cleanup_source(lease, stop, progress)
            if window.upper_ms <= progress:
                return
            after_ms = progress
            published_any = False
            while after_ms < window.upper_ms and not stop.is_set():
                lease.ensure_active()
                page = await self._store.read_source_page(
                    lease.owner.shard_id,
                    lower_exclusive_ms=after_ms,
                    upper_inclusive_ms=window.upper_ms,
                    after=None,
                    limit=1,
                )
                if not page.items:
                    break
                minute = bucket_minute_ms(page.items[0].due_at_ms)
                result = await self._process_minute(
                    lease,
                    stop,
                    progress_ms=progress,
                    minute_ms=minute,
                )
                if result is not None:
                    progress = result
                    published_any = True
                after_ms = minute
            if not published_any:
                return

    async def _process_minute(
        self,
        lease: ShardExecution,
        stop: asyncio.Event,
        *,
        progress_ms: int,
        minute_ms: int,
    ) -> int | None:
        lease.ensure_active()
        await self._store.reset_unpublished_minute(
            lease.owner,
            minute_ms=minute_ms,
            expected_progress_ms=progress_ms,
            item_types=tuple(sorted(SUPPORTED_ITEM_TYPES)),
        )
        cursor = None
        types_with_data: set[str] = set()
        while True:
            lease.ensure_active()
            page = await self._store.read_source_page(
                lease.owner.shard_id,
                lower_exclusive_ms=max(progress_ms, minute_ms - MINUTE_MS),
                upper_inclusive_ms=minute_ms,
                after=cursor,
                limit=self._config.scan_batch_size,
            )
            by_type: dict[str, list[str]] = defaultdict(list)
            invalid_counts: dict[str, int] = defaultdict(int)
            for item in page.items:
                try:
                    identity = decode_item_id(item.item_id)
                except ExpiryError:
                    invalid_counts["invalid_identity"] += 1
                    continue
                if identity.item_type not in SUPPORTED_ITEM_TYPES:
                    invalid_counts["unsupported_type"] += 1
                    continue
                by_type[identity.item_type].append(item.item_id)
            for reason, count in sorted(invalid_counts.items()):
                self._metrics.count(
                    "timer_source_invalid",
                    value=count,
                    outcome=reason,
                )
                self._metrics.log_invalid_source_items(
                    shard_id=lease.owner.shard_id,
                    minute_ms=minute_ms,
                    reason=reason,
                    count=count,
                )
            for item_type, item_ids in by_type.items():
                lease.ensure_active()
                ref = BucketRef(item_type, lease.owner.shard_id, minute_ms)
                await self._store.stage_items(lease.owner, ref, item_ids)
                types_with_data.add(item_type)
            cursor = page.next_cursor
            if cursor is None:
                break
        if not types_with_data:
            # Invalid members can be removed without advancing the published watermark.
            # Their metadata lives in other cluster slots and expires by TTL.
            await self._cleanup_source(lease, stop, minute_ms)
            return None
        refs = tuple(
            BucketRef(item_type, lease.owner.shard_id, minute_ms)
            for item_type in sorted(types_with_data)
        )
        result = await self._publish_completed_minute(
            lease,
            expected_progress_ms=progress_ms,
            minute_ms=minute_ms,
            buckets=refs,
        )
        lease.ensure_active()
        # This also removes skipped invalid members; their metadata expires by TTL.
        await self._cleanup_source(lease, stop, result.progress_ms)
        return result.progress_ms

    async def _cleanup_source(
        self, lease: ShardExecution, stop: asyncio.Event, through_ms: int
    ) -> None:
        limit = self._config.cleanup_batch_size
        while not stop.is_set():
            lease.ensure_active()
            removed = await self._store.cleanup_source_page(
                lease.owner.shard_id,
                through_ms,
                limit=limit,
            )
            if removed < limit:
                return

    async def _publish_completed_minute(
        self,
        lease: ShardExecution,
        *,
        expected_progress_ms: int,
        minute_ms: int,
        buckets: Sequence[BucketRef],
    ) -> PublishResult:
        while True:
            lease.ensure_active()
            try:
                return await self._store.publish_minute(
                    lease.owner,
                    expected_progress_ms=expected_progress_ms,
                    minute_ms=minute_ms,
                    buckets=buckets,
                )
            except StoreOperationError as exc:
                if not exc.outcome_unknown:
                    raise
                lease.ensure_active()
                progress = await self._store.read_progress(lease.owner.shard_id)
                if progress >= minute_ms:
                    return PublishResult("already_published", progress)
                if progress != expected_progress_ms:
                    raise StoreOperationError(
                        "progress_conflict", "Timer progress changed after uncertain publish"
                    ) from exc
