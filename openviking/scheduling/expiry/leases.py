# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Redis token operations for temporary per-shard ownership."""

from __future__ import annotations

import asyncio
import time
import uuid

from .config import ExpiryConfig
from .metrics import ExpiryMetrics
from .models import OwnerRole, ShardLease, StoreOperationError, task_is_cancelling
from .redis_store import RedisExpiryStore


class ShardLeaseCoordinator:
    def __init__(
        self,
        store: RedisExpiryStore,
        config: ExpiryConfig,
        metrics: ExpiryMetrics,
    ) -> None:
        self._store = store
        self._config = config
        self._metrics = metrics

    async def try_acquire(
        self, role: OwnerRole, shard_id: int, *, item_type: str | None = None
    ) -> ShardLease | None:
        if role is OwnerRole.CALLBACK and item_type is None:
            raise ValueError("Callback lease requires an item type")
        if role is OwnerRole.SCHEDULER and item_type is not None:
            raise ValueError("Scheduler lease must not include an item type")
        started = time.monotonic()
        token = uuid.uuid4().hex
        owner = await self._store.try_acquire_owner(
            role,
            shard_id,
            token,
            self._config.owner_ttl_seconds,
            item_type=item_type,
        )
        if owner is None:
            return None
        lease = ShardLease(owner, asyncio.Event(), started + self._config.owner_ttl_seconds)
        lease.ensure_active()
        return lease

    async def renew(self, lease: ShardLease) -> bool:
        """Never revive lost ownership, including a successful but late response."""
        try:
            lease.ensure_active()
        except StoreOperationError:
            return False
        started = time.monotonic()
        previous_deadline = lease.valid_until_monotonic
        try:
            renewed = await self._store.renew_owner(lease.owner, self._config.owner_ttl_seconds)
        except asyncio.CancelledError:
            # Cancelling a request does not prove Redis did not execute it.
            lease.lost.set()
            raise
        except Exception:
            lease.lost.set()
            return False
        task = asyncio.current_task()
        if (
            renewed is None
            or lease.lost.is_set()
            or task_is_cancelling(task)
            or time.monotonic() >= previous_deadline
        ):
            lease.lost.set()
            return False
        if lease.revoked.is_set():
            return False
        lease.owner = renewed
        lease.valid_until_monotonic = started + self._config.owner_ttl_seconds
        return True

    async def release(self, lease: ShardLease) -> None:
        """Compare the token only after local work/renewal have terminated.

        Local revocation is expected during cleanup and is not ownership loss.
        Failed or unknown release leaves the owner to expire; it never deletes a
        successor's token.
        """
        if time.monotonic() >= lease.valid_until_monotonic:
            lease.lost.set()
        if lease.lost.is_set():
            return
        try:
            await self._store.release_owner(lease.owner)
        except asyncio.CancelledError:
            lease.lost.set()
            raise
        except Exception as exc:
            if isinstance(exc, StoreOperationError) and exc.outcome_unknown:
                lease.lost.set()
            self._metrics.count("timer_owner_release", outcome="failed")
