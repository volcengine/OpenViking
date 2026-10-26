# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Published resource callback consumption."""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .api import CallbackRequest, CallbackStatus, ExpiryError
from .config import ExpiryConfig
from .keys import decode_item_id
from .metrics import ExpiryMetrics
from .models import (
    BucketItem,
    BucketRef,
    Claim,
    OwnerRole,
    StoreOperationError,
    decode_claim_score,
)
from .redis_store import RedisExpiryStore
from .registry import HandlerBinding
from .shard_runner import ShardExecution, ShardRunner

logger = logging.getLogger(__name__)


@dataclass
class _ConsumerState:
    reserved: int = 0
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


@dataclass(frozen=True)
class _BucketScan:
    next_poll_at_ms: int | None = None
    conflicted: bool = False


class ExpiryConsumer:
    def __init__(
        self,
        store: RedisExpiryStore,
        runner: ShardRunner,
        item_type: str,
        binding: HandlerBinding,
        config: ExpiryConfig,
        metrics: ExpiryMetrics,
        *,
        start_shard: int | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._item_type = item_type
        self._binding = binding
        self._config = config
        self._metrics = metrics
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._next_shard = (
            secrets.randbelow(config.shard_count) if start_shard is None else start_shard
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Traverse every shard from a per-consumer offset."""
        while not stop.is_set():
            start_shard = self._next_shard
            for offset in range(self._config.shard_count):
                if stop.is_set():
                    break
                shard_id = (start_shard + offset) % self._config.shard_count
                try:
                    await self._runner.run_if_acquired(
                        OwnerRole.CALLBACK,
                        shard_id,
                        self.process_shard,
                        stop,
                        item_type=self._item_type,
                    )
                except StoreOperationError as exc:
                    if exc.code == "shard_not_stopped":
                        logger.warning(
                            "expiry consumer skipped unfinished shard execution "
                            "item_type=%s shard_id=%d",
                            self._item_type,
                            shard_id,
                        )
                        self._metrics.count(
                            "timer_shard_round",
                            item_type=self._item_type,
                            outcome="orphaned",
                        )
                        continue
                    self._metrics.count(
                        "timer_shard_round",
                        item_type=self._item_type,
                        outcome="failed",
                    )
                except Exception:
                    self._metrics.count(
                        "timer_shard_round",
                        item_type=self._item_type,
                        outcome="failed",
                    )
            self._next_shard = (start_shard + 1) % self._config.shard_count
            if not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), self._config.callback_poll_seconds)
                except asyncio.TimeoutError:
                    pass

    async def process_shard(self, lease: ShardExecution, stop: asyncio.Event) -> None:
        state = _ConsumerState()
        bucket = await self._discover_bucket(lease)
        if bucket is None:
            return
        await self._process_bucket(lease, stop, bucket, state)

    async def _discover_bucket(self, lease: ShardExecution) -> BucketRef | None:
        cursor = None
        while True:
            lease.ensure_active()
            page = await self._store.read_registry_page(
                lease.owner.shard_id,
                after=cursor,
                limit=self._config.registry_page_size,
            )
            for bucket in page.buckets:
                if bucket.item_type == self._item_type:
                    return bucket
            cursor = page.next_cursor
            if cursor is None:
                return None

    async def _process_bucket(
        self,
        lease: ShardExecution,
        stop: asyncio.Event,
        bucket: BucketRef,
        state: _ConsumerState,
    ) -> None:
        while not stop.is_set():
            lease.ensure_active()
            available = self._binding.options.concurrency - state.reserved
            scan = _BucketScan()
            redis_now_ms = 0
            if available > 0:
                redis_now_ms = await self._store.read_time_ms()
                lease.ensure_active()
                scan = await self._claim_ready_items(
                    lease,
                    bucket,
                    redis_now_ms=redis_now_ms,
                    available=available,
                    state=state,
                )

            active = {task for task in state.tasks if not task.done()}
            if active:
                await lease.wait(self._config.callback_poll_seconds, stop)
                continue
            if scan.conflicted:
                continue

            lease.ensure_active()
            empty = await self._store.remove_registration_if_empty(bucket)
            lease.ensure_active()
            if empty:
                return
            if scan.next_poll_at_ms is None or scan.next_poll_at_ms <= redis_now_ms:
                continue
            await self._store.defer_registration(
                lease.owner,
                bucket,
                next_poll_at_ms=scan.next_poll_at_ms,
            )
            return

        tasks = {task for task in state.tasks if not task.done()}
        if tasks:
            await asyncio.wait(tasks)
            lease.ensure_active()

    async def _claim_ready_items(
        self,
        lease: ShardExecution,
        bucket: BucketRef,
        *,
        redis_now_ms: int,
        available: int,
        state: _ConsumerState,
    ) -> _BucketScan:
        next_poll_at_ms: int | None = None
        started_at_seconds = redis_now_ms // 1000
        for attempt in range(self._binding.options.max_attempts):
            while available > 0:
                limit = min(self._config.scan_batch_size, available)
                page = await self._store.read_bucket_page(
                    bucket,
                    attempt=attempt,
                    limit=limit,
                )
                lease.ensure_active()
                if not page.items:
                    break

                claimed_from_full_page = len(page.items) == limit
                for item in page.items:
                    ready_at_ms = self._ready_at_ms(item, attempt)
                    if ready_at_ms is not None and ready_at_ms > redis_now_ms:
                        next_poll_at_ms = (
                            ready_at_ms
                            if next_poll_at_ms is None
                            else min(next_poll_at_ms, ready_at_ms)
                        )
                        claimed_from_full_page = False
                        break

                    claim = await self._store.claim(
                        bucket,
                        item,
                        attempt=attempt,
                        started_at_seconds=started_at_seconds,
                    )
                    lease.ensure_active()
                    if claim is None:
                        return _BucketScan(next_poll_at_ms, conflicted=True)
                    self._start_callback(lease, claim, state)
                    available -= 1
                    if available == 0:
                        break

                if available == 0 or not claimed_from_full_page:
                    break
        return _BucketScan(next_poll_at_ms)

    def _ready_at_ms(self, item: BucketItem, expected_attempt: int) -> int | None:
        if item.score == 0:
            if expected_attempt != 0:
                raise StoreOperationError(
                    "namespace_corrupt", "Unclaimed expiry item is outside attempt zero"
                )
            return None
        attempt, started_at_seconds = decode_claim_score(item.score)
        if attempt != expected_attempt:
            raise StoreOperationError(
                "namespace_corrupt", "Expiry item is outside its expected attempt layer"
            )
        return (started_at_seconds + self._config.processing_timeout_seconds) * 1000

    def _start_callback(
        self,
        lease: ShardExecution,
        claim: Claim,
        state: _ConsumerState,
    ) -> None:
        task = lease.spawn(self._callback_operation(lease, claim, state))
        state.reserved += 1
        state.tasks.add(task)
        self._callback_tasks.add(task)
        task.add_done_callback(state.tasks.discard)
        task.add_done_callback(self._callback_tasks.discard)

    def _callback_operation(
        self,
        lease: ShardExecution,
        claim: Claim,
        state: _ConsumerState,
    ) -> Callable[[], Awaitable[None]]:
        async def operation() -> None:
            await self._execute(lease, claim, state)

        return operation

    async def _execute(
        self,
        lease: ShardExecution,
        claim: Claim,
        state: _ConsumerState,
    ) -> None:
        try:
            try:
                lease.ensure_active()
                request = await self._resolve_request(claim)
                lease.ensure_active()
                result = await asyncio.wait_for(
                    self._binding.handler.handle(request),
                    timeout=self._config.processing_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                self._record_callback_error(claim, exc)
                lease.ensure_active()
                await self._finish_failed_attempt(lease, claim, "callback_timeout")
                return
            except StoreOperationError as exc:
                if exc.code == "owner_lost" or exc.outcome_unknown:
                    raise
                self._record_callback_error(claim, exc)
                lease.ensure_active()
                await self._finish_failed_attempt(lease, claim, "callback_exception")
                return
            except Exception as exc:
                self._record_callback_error(claim, exc)
                lease.ensure_active()
                await self._finish_failed_attempt(lease, claim, "callback_exception")
                return

            lease.ensure_active()
            if result.status is CallbackStatus.COMPLETED:
                await self._store.ack(claim)
                lease.ensure_active()
            else:
                await self._finish_failed_attempt(lease, claim, "callback_retry")
        finally:
            state.reserved = max(0, state.reserved - 1)

    async def _finish_failed_attempt(
        self, lease: ShardExecution, claim: Claim, reason: str
    ) -> None:
        attempt, _ = decode_claim_score(claim.claim_score)
        if attempt + 1 >= self._binding.options.max_attempts:
            await self._log_exhausted_and_ack(lease, claim, reason)
            return
        lease.ensure_active()
        await self._store.advance_retry(claim)
        lease.ensure_active()

    async def _log_exhausted_and_ack(
        self, lease: ShardExecution, claim: Claim, reason: str
    ) -> None:
        lease.ensure_active()
        self._metrics.count(
            "timer_callback_exhausted",
            item_type=claim.bucket.item_type,
            outcome=reason,
        )
        try:
            self._metrics.log_exhausted(
                claim,
                max_attempts=self._binding.options.max_attempts,
                reason=reason,
            )
        except Exception as exc:
            self._record_callback_error(claim, exc)

        try:
            lease.ensure_active()
            await self._store.ack(claim)
        except StoreOperationError as exc:
            if exc.code == "owner_lost" or exc.outcome_unknown:
                raise
            self._record_callback_error(claim, exc)
        except Exception as exc:
            self._record_callback_error(claim, exc)

    async def _resolve_request(self, claim: Claim) -> CallbackRequest:
        identity = decode_item_id(claim.item_id)
        if identity.item_type != claim.bucket.item_type:
            raise ExpiryError("invalid_identity", "Bucket type differs from item ID")
        if not self._binding.options.metadata_enabled:
            return CallbackRequest(identity)
        metadata = await self._store.read_metadata(claim.item_id)
        if metadata is not None and metadata.identity != identity:
            raise ExpiryError("invalid_identity", "Metadata differs from item ID")
        self._metrics.count(
            "timer_metadata_lookup",
            item_type=identity.item_type,
            outcome="miss" if metadata is None else "hit",
        )
        return CallbackRequest(identity)

    async def drain(self, timeout_seconds: float) -> None:
        tasks = {task for task in self._callback_tasks if not task.done()}
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout_seconds))
        for task in pending:
            task.cancel()
        if pending:
            raise StoreOperationError("shard_not_stopped", "Callbacks did not drain within budget")

    def _record_callback_error(self, claim: Claim, exc: Exception) -> None:
        self._metrics.count(
            "timer_callback_error",
            item_type=claim.bucket.item_type,
            outcome=type(exc).__name__,
        )
