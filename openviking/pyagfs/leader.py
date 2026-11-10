# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Leadership lifetimes managed by AGFS."""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Literal, Optional, Protocol

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

LeaderMode = Literal["distributed", "local"]
_MIN_TTL_SECS = 0.001
_MAX_TTL_SECS = (2**63 - 1) / 1000
_LEADER_COMPONENT_CHARS = frozenset("._-:")


def _validate_component(name: str, value: str) -> None:
    if not value or not all(
        char.isascii() and (char.isalnum() or char in _LEADER_COMPONENT_CHARS)
        for char in value
    ):
        raise ValueError(
            f"{name} must contain only ASCII letters, digits, '.', '_', '-' or ':'"
        )


def _validate_timing(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("leader timing values must be finite positive numbers")
    if name == "ttl_secs" and value < _MIN_TTL_SECS:
        raise ValueError("ttl_secs must be at least 0.001 seconds")
    if name == "ttl_secs" and value > _MAX_TTL_SECS:
        raise ValueError("ttl_secs must not exceed i64::MAX milliseconds")


class _LeaderBackend(Protocol):
    async def acquire(self, key: str, holder_id: str, ttl_secs: float) -> Optional[str]: ...

    async def renew(self, lease_ref: str, ttl_secs: float) -> bool: ...

    async def release(self, lease_ref: str) -> None: ...


class _NativeLeaderClient(Protocol):
    def leader_validate(self) -> None: ...

    def leader_try_acquire_async(
        self, key: str, holder_id: str, ttl_secs: float
    ) -> Awaitable[str | None]: ...

    def leader_renew_async(self, lease_ref: str, ttl_secs: float) -> Awaitable[bool]: ...

    def leader_release_async(self, lease_ref: str) -> Awaitable[None]: ...


class _NativeLeaderBackend:
    def __init__(self, client: _NativeLeaderClient) -> None:
        for method_name in (
            "leader_validate",
            "leader_try_acquire_async",
            "leader_renew_async",
            "leader_release_async",
        ):
            if not callable(getattr(client, method_name, None)):
                raise RuntimeError(
                    f"AGFS native binding does not support distributed leadership: "
                    f"missing {method_name}"
                )
        client.leader_validate()
        self._client = client

    async def acquire(self, key: str, holder_id: str, ttl_secs: float) -> Optional[str]:
        future = asyncio.ensure_future(
            self._client.leader_try_acquire_async(key, holder_id, ttl_secs)
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.done():
                await self._release_acquired_after_cancel(future)
            raise

    async def _release_acquired_after_cancel(
        self, future: asyncio.Future[str | None]
    ) -> None:
        try:
            lease_ref = future.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Leader lease acquisition cleanup failed")
            return
        if lease_ref is None:
            return
        try:
            await self.release(lease_ref)
        except Exception:
            logger.exception("Failed to release leader lease after cancellation")

    async def renew(self, lease_ref: str, ttl_secs: float) -> bool:
        return bool(await self._client.leader_renew_async(lease_ref, ttl_secs))

    async def release(self, lease_ref: str) -> None:
        await self._client.leader_release_async(lease_ref)


@dataclass
class _LocalLease:
    key: str
    expires_at: float


class _LocalLeaderBackend:
    """One-process fallback for file-backed deployments."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[str, str] = {}
        self._leases: dict[str, _LocalLease] = {}

    async def acquire(self, key: str, holder_id: str, ttl_secs: float) -> Optional[str]:
        del holder_id
        with self._lock:
            now = time.monotonic()
            current_ref = self._owners.get(key)
            if current_ref is not None:
                current = self._leases.get(current_ref)
                if current is not None and current.expires_at > now:
                    return None
                self._owners.pop(key, None)
                self._leases.pop(current_ref, None)
            lease_ref = str(uuid.uuid4())
            self._owners[key] = lease_ref
            self._leases[lease_ref] = _LocalLease(key=key, expires_at=now + ttl_secs)
            return lease_ref

    async def renew(self, lease_ref: str, ttl_secs: float) -> bool:
        with self._lock:
            lease = self._leases.get(lease_ref)
            now = time.monotonic()
            if (
                lease is None
                or lease.expires_at <= now
                or self._owners.get(lease.key) != lease_ref
            ):
                self._leases.pop(lease_ref, None)
                if lease is not None and self._owners.get(lease.key) == lease_ref:
                    self._owners.pop(lease.key, None)
                return False
            lease.expires_at = now + ttl_secs
            return True

    async def release(self, lease_ref: str) -> None:
        with self._lock:
            lease = self._leases.pop(lease_ref, None)
            if lease is not None and self._owners.get(lease.key) == lease_ref:
                self._owners.pop(lease.key, None)


_LOCAL_LEADER_BACKEND = _LocalLeaderBackend()


@dataclass(frozen=True)
class Leadership:
    """One held leadership term."""

    holder_id: str
    stop_event: asyncio.Event


class LeaderCandidate(AsyncIterator[Leadership]):
    """Acquire leadership terms and signal each term when ownership is lost."""

    def __init__(
        self,
        backend: _LeaderBackend,
        *,
        key: str,
        holder_id: str,
        ttl_secs: float,
        renew_interval_secs: float,
        retry_interval_secs: float,
    ) -> None:
        _validate_component("leader key", key)
        _validate_component("holder_id", holder_id)
        _validate_timing("ttl_secs", ttl_secs)
        _validate_timing("renew_interval_secs", renew_interval_secs)
        _validate_timing("retry_interval_secs", retry_interval_secs)
        if renew_interval_secs >= ttl_secs:
            raise ValueError("renew_interval_secs must be less than ttl_secs")
        self._backend = backend
        self._key = key
        self._holder_id = holder_id
        self._ttl_secs = ttl_secs
        self._renew_interval_secs = renew_interval_secs
        self._retry_interval_secs = retry_interval_secs
        self._active = False
        self._generation = 0
        self._lease_ref: Optional[str] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._renew_task: Optional[asyncio.Task[None]] = None

    async def __aenter__(self) -> "LeaderCandidate":
        if self._active:
            raise RuntimeError("leader candidate is already active")
        self._generation += 1
        self._active = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def __aiter__(self) -> "LeaderCandidate":
        return self

    async def __anext__(self) -> Leadership:
        await self._finish_term()
        if not self._active:
            raise StopAsyncIteration
        generation = self._generation
        loop = asyncio.get_running_loop()
        while True:
            if not self._active or self._generation != generation:
                raise StopAsyncIteration
            acquire_started = loop.time()
            try:
                lease_ref = await self._backend.acquire(
                    self._key,
                    self._holder_id,
                    self._ttl_secs,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Leader lease acquisition failed key=%s", self._key)
                lease_ref = None
            if lease_ref is not None:
                deadline = acquire_started + self._ttl_secs
                accepted = (
                    self._active
                    and self._generation == generation
                    and loop.time() < deadline
                )
                if accepted:
                    stop_event = asyncio.Event()
                    self._lease_ref = lease_ref
                    self._stop_event = stop_event
                    self._renew_task = asyncio.create_task(
                        self._renew(lease_ref, stop_event, deadline),
                        name=f"leader-renew:{self._key}",
                    )
                if accepted:
                    return Leadership(holder_id=self._holder_id, stop_event=stop_event)
                try:
                    await self._backend.release(lease_ref)
                except Exception:
                    logger.exception("Failed to release rejected leader lease key=%s", self._key)
                if not self._active or self._generation != generation:
                    raise StopAsyncIteration
            await asyncio.sleep(self._retry_interval_secs)

    async def aclose(self) -> None:
        self._active = False
        self._generation += 1
        await self._finish_term()

    async def _finish_term(self) -> None:
        stop_event, self._stop_event = self._stop_event, None
        renew_task, self._renew_task = self._renew_task, None
        lease_ref, self._lease_ref = self._lease_ref, None
        if stop_event is not None:
            stop_event.set()
        if renew_task is not None:
            renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renew_task
        if lease_ref is not None:
            try:
                await self._backend.release(lease_ref)
            except Exception:
                logger.exception("Failed to release leader lease key=%s", self._key)

    async def _renew(
        self,
        lease_ref: str,
        stop_event: asyncio.Event,
        deadline: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                await asyncio.sleep(min(self._renew_interval_secs, remaining))
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                renew_started = loop.time()
                renewed = await asyncio.wait_for(
                    self._backend.renew(lease_ref, self._ttl_secs),
                    timeout=remaining,
                )
                if not renewed:
                    return
                deadline = renew_started + self._ttl_secs
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Leader lease renewal failed key=%s", self._key)
        finally:
            stop_event.set()


def leader_candidate(
    client: _NativeLeaderClient,
    local_backend: _LocalLeaderBackend,
    *,
    key: str,
    holder_id: str,
    ttl_secs: float,
    renew_interval_secs: float,
    retry_interval_secs: float,
    mode: LeaderMode,
) -> LeaderCandidate:
    """Create a candidate over the requested AGFS leadership backend."""
    backend: _LeaderBackend
    if mode == "distributed":
        backend = _NativeLeaderBackend(client)
    elif mode == "local":
        backend = local_backend
    else:
        raise ValueError("leader mode must be 'distributed' or 'local'")
    return LeaderCandidate(
        backend,
        key=key,
        holder_id=holder_id,
        ttl_secs=ttl_secs,
        renew_interval_secs=renew_interval_secs,
        retry_interval_secs=retry_interval_secs,
    )
