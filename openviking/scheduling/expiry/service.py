# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Private facade implementing the public ExpiryScheduler protocol."""

from __future__ import annotations

from dataclasses import dataclass

from .api import (
    SUPPORTED_ITEM_TYPES,
    ExpiryError,
    ExpiryHandler,
    ExpiryRegistration,
    ExpiryRegistrationResult,
    HandlerOptions,
    ItemIdentity,
)
from .keys import encode_item_id, shard_for
from .metrics import ExpiryMetrics
from .models import StoreOperationError, validate_identity, validate_report
from .redis_store import RedisExpiryStore
from .registry import HandlerRegistry


@dataclass
class ExpiryRuntimeState:
    storage_ready: bool = False
    accepting_calls: bool = False


class _ExpirySchedulerFacade:
    def __init__(
        self,
        store: RedisExpiryStore,
        registry: HandlerRegistry,
        state: ExpiryRuntimeState,
        *,
        shard_count: int,
        reporting_enabled: bool,
        metrics: ExpiryMetrics,
    ) -> None:
        self._store = store
        self._registry = registry
        self._state = state
        self._metrics = metrics
        self._shard_count = shard_count
        self._reporting_enabled = reporting_enabled

    async def report(self, task: ExpiryRegistration) -> ExpiryRegistrationResult:
        self._require_ready()
        if not self._reporting_enabled:
            raise ExpiryError("reporting_disabled", "Timer reporting is disabled")
        validate_report(task)
        self._ensure_supported_type(task.identity.item_type)
        try:
            item_id = encode_item_id(task.identity)
            shard_id = shard_for(item_id, self._shard_count)
            result = await self._store.upsert_resource(shard_id, item_id, task)
            binding = self._registry.get(task.identity.item_type)
            if binding is None or binding.options.metadata_enabled:
                await self._store.ensure_metadata(item_id, task)
        except StoreOperationError as exc:
            raise self._business_error(exc) from exc
        self._metrics.count("timer_report", item_type=task.identity.item_type, outcome="written")
        return result

    async def unregister(self, identity: ItemIdentity) -> None:
        self._require_ready()
        validate_identity(identity)
        self._ensure_supported_type(identity.item_type)
        item_id = encode_item_id(identity)
        try:
            binding = self._registry.get(identity.item_type)
            await self._store.unregister_resource(
                shard_for(item_id, self._shard_count),
                item_id,
                delete_metadata=binding is None or binding.options.metadata_enabled,
            )
        except StoreOperationError as exc:
            raise self._business_error(exc) from exc

    def register_handler(
        self,
        item_type: str,
        handler: ExpiryHandler,
        *,
        options: HandlerOptions | None = None,
    ) -> None:
        self._registry.register(item_type, handler, options or HandlerOptions())

    @staticmethod
    def _ensure_supported_type(item_type: str) -> None:
        if item_type not in SUPPORTED_ITEM_TYPES:
            raise ExpiryError("unregistered_type", f"Timer type is not supported: {item_type}")

    def _require_ready(self) -> None:
        if not (self._state.storage_ready and self._state.accepting_calls):
            raise ExpiryError("not_started", "Expiry scheduling is not ready")

    def _business_error(self, exc: StoreOperationError) -> ExpiryError:
        public_codes = {"invalid_identity", "invalid_report", "unregistered_type"}
        code = exc.code if exc.code in public_codes else "timer_unavailable"
        self._metrics.count("timer_store_error", outcome=exc.code)
        return ExpiryError(code, str(exc), retryable=exc.retryable or code == "timer_unavailable")
