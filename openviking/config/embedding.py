# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account embedding resources with call-scoped borrowing and safe retirement."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import dataclass, field

from openviking.concurrency import AsyncSemaphore
from openviking.config.scope import ScopeKind
from openviking.config.vector import AccountVectorConfigResolver, VectorRuntimeSettings
from openviking.models.embedder.base import EmbedderBase
from openviking.models.vlm.token_usage import TokenUsageTracker
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool
from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_INPUT_TOO_LARGE,
    classify_api_error,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

@dataclass(frozen=True)
class EmbeddingResourceStatus:
    """Read-only Account embedding state."""

    fingerprint: str
    dimension: int
    borrowers: int
    retired: bool
    closed: bool


@dataclass(eq=False)
class _EmbeddingResource:
    fingerprint: str
    settings: VectorRuntimeSettings
    embedder: EmbedderBase
    breaker: CircuitBreaker
    borrowers: int = 0
    retired: bool = False
    closed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def try_borrow(self) -> bool:
        with self.lock:
            if self.retired or self.closed:
                return False
            self.borrowers += 1
            return True

    def release(self) -> None:
        close = False
        with self.lock:
            self.borrowers -= 1
            if self.retired and not self.borrowers and not self.closed:
                self.closed = True
                close = True
        if close:
            self._close_embedder()

    def retire(self) -> None:
        close = False
        with self.lock:
            self.retired = True
            if not self.borrowers and not self.closed:
                self.closed = True
                close = True
        if close:
            self._close_embedder()

    def _close_embedder(self) -> None:
        try:
            self.embedder.close()
        except Exception:
            # Retirement must remain terminal even if a provider's best-effort
            # close path fails; otherwise AccountEmbeddingProvider.close() can
            # wait forever for this resource to leave _retired.
            logger.warning(
                "Failed to close retired embedding resource %s",
                self.fingerprint,
                exc_info=True,
            )

    def status(self) -> EmbeddingResourceStatus:
        with self.lock:
            return EmbeddingResourceStatus(
                fingerprint=self.fingerprint,
                dimension=self.settings.vectordb.dimension,
                borrowers=self.borrowers,
                retired=self.retired,
                closed=self.closed,
            )


def _bind_account_state(
    embedder: EmbedderBase,
    semaphore: AsyncSemaphore,
    tracker: TokenUsageTracker,
    account_id: str,
) -> None:
    """Share isolation state only among clients owned by this Account."""
    embedder._account_semaphore = semaphore
    embedder._token_tracker = tracker
    embedder._account_id = account_id
    children = list(getattr(embedder, "_embedders", []))
    for name in ("dense_embedder", "sparse_embedder"):
        child = getattr(embedder, name, None)
        if child is not None:
            children.append(child)
    for child in children:
        _bind_account_state(child, semaphore, tracker, account_id)


class AccountEmbeddingProvider:
    """Resolve, cache, execute and retire Account embedding resources."""

    def __init__(self, resolver: AccountVectorConfigResolver, config_events):
        self._resolver = resolver
        self._cache: dict[str, _EmbeddingResource] = {}
        self._retired: set[_EmbeddingResource] = set()
        self._account_locks = KeyedAsyncLockPool[str]()
        self._usage: dict[str, TokenUsageTracker] = {}
        self._lock = threading.RLock()
        self._closed = False
        config_events.add_update_consumer(
            scope=ScopeKind.ACCOUNT,
            sections={"embedding"},
            consumer=self._on_change,
        )

    async def _on_change(self, event) -> None:
        if event.scope.key:
            await self.invalidate(event.scope.key)

    async def invalidate(self, account_id: str) -> None:
        async with self._account_locks.acquire(account_id):
            with self._lock:
                resource = self._cache.pop(account_id, None)
            if resource is not None:
                self._retire(resource)

    def _retire(self, resource: _EmbeddingResource) -> None:
        with self._lock:
            self._retired.add(resource)
        resource.retire()
        if resource.status().closed:
            with self._lock:
                self._retired.discard(resource)

    async def get_status(self, account_id: str) -> EmbeddingResourceStatus:
        return (await self._resource_for(account_id)).status()

    async def _resource_for(self, account_id: str) -> _EmbeddingResource:
        if not account_id:
            raise ValueError("account_id is required")
        async with self._account_locks.acquire(account_id):
            with self._lock:
                if self._closed:
                    raise RuntimeError("Account embedding provider is closed")

            settings = await self._resolver.resolve(account_id)
            fingerprint = hashlib.sha256(
                settings.embedding.model_dump_json().encode()
            ).hexdigest()
            with self._lock:
                if self._closed:
                    raise RuntimeError("Account embedding provider is closed")
                cached = self._cache.get(account_id)
                if cached is not None and cached.fingerprint == fingerprint:
                    return cached

            config = settings.embedding
            embedder = config.get_embedder()
            with self._lock:
                tracker = self._usage.setdefault(account_id, TokenUsageTracker())
            _bind_account_state(
                embedder,
                AsyncSemaphore(config.max_concurrent),
                tracker,
                account_id,
            )
            resource = _EmbeddingResource(
                fingerprint=fingerprint,
                settings=settings,
                embedder=embedder,
                breaker=CircuitBreaker(**config.circuit_breaker.model_dump()),
            )
            with self._lock:
                if self._closed:
                    resource.retire()
                    raise RuntimeError("Account embedding provider is closed")
                previous = self._cache.get(account_id)
                self._cache[account_id] = resource
            if previous is not None:
                self._retire(previous)
            return resource

    async def _borrow(self, account_id: str) -> _EmbeddingResource:
        while True:
            resource = await self._resource_for(account_id)
            if resource.try_borrow():
                return resource

    def bind(self, account_id: str) -> AccountBoundEmbedder:
        if not account_id:
            raise ValueError("account_id is required")
        return AccountBoundEmbedder(self, account_id)

    async def query_cache_key(self, account_id: str, content) -> tuple[str, str, str]:
        """Describe a query input without borrowing the account resource."""
        resource = await self._resource_for(account_id)
        prepared = resource.embedder.prepare_embedding_input(content)
        return account_id, resource.fingerprint, repr(prepared)

    async def embed(self, account_id: str, content, *, is_query: bool = False):
        resource = await self._borrow(account_id)
        try:
            prepared = resource.embedder.prepare_embedding_input(content)
            return await self._execute(resource, prepared, is_query)
        finally:
            resource.release()
            self._discard_retired(resource)

    def _discard_retired(self, resource: _EmbeddingResource) -> None:
        if resource.status().closed:
            with self._lock:
                self._retired.discard(resource)

    async def _execute(self, resource, content, is_query):
        from openviking.telemetry import bind_telemetry_stage

        try:
            resource.breaker.check()
        except CircuitBreakerOpen as exc:
            exc.retry_after = resource.breaker.retry_after
            raise
        try:
            with bind_telemetry_stage("embed_query" if is_query else "embed_resource"):
                result = await resource.embedder.embed_async(content, is_query=is_query)
            if result.dense_vector is not None:
                expected = resource.settings.vectordb.dimension
                if len(result.dense_vector) != expected:
                    raise ValueError(
                        f"Dense vector dimension mismatch: expected {expected}, "
                        f"got {len(result.dense_vector)}"
                    )
        except Exception as exc:
            if classify_api_error(exc) not in {ERROR_CLASS_AUTH, ERROR_CLASS_INPUT_TOO_LARGE}:
                resource.breaker.record_failure(exc)
            raise
        resource.breaker.record_success()
        return result

    def get_token_usage(self, account_id: str):
        with self._lock:
            tracker = self._usage.get(account_id, TokenUsageTracker())
        return tracker.to_dict()

    def get_total_token_usage(self):
        with self._lock:
            trackers = tuple(self._usage.values())
        return TokenUsageTracker.merge(*trackers).to_dict()

    async def close(self) -> None:
        with self._lock:
            self._closed = True
            resources = set(self._cache.values())
            self._cache.clear()
        for resource in resources:
            self._retire(resource)
        while True:
            with self._lock:
                if not self._retired:
                    return
            await asyncio.sleep(0.01)


class AccountBoundEmbedder:
    """Long-lived Account proxy that resolves the current resource per call."""

    def __init__(self, provider: AccountEmbeddingProvider, account_id: str):
        self.provider = provider
        self.account_id = account_id

    async def embed_compatible(self, content, *, is_query=False):
        return await self.provider.embed(self.account_id, content, is_query=is_query)

    async def query_embedding_cache_key(self, content):
        return await self.provider.query_cache_key(self.account_id, content)

    async def embed_async(self, content, is_query=False):
        return await self.embed_compatible(content, is_query=is_query)

    def get_token_usage(self):
        return self.provider.get_token_usage(self.account_id)
