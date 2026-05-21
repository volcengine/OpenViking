# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Typed lock ownership helpers for path-lock users."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.lock_manager import LockManager, get_lock_manager
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class LockHandoffRef:
    """Serializable reference used to hand lock ownership through a queue."""

    handle_id: str
    lock_paths: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> Optional["LockHandoffRef"]:
        if value is None:
            return None
        if isinstance(value, LockHandoffRef):
            return value
        if not isinstance(value, dict):
            raise ValueError("lock_handoff must be an object")

        handle_id = value.get("handle_id")
        if not handle_id:
            return None
        lock_paths = tuple(str(path) for path in value.get("lock_paths", []) if path)
        return cls(handle_id=str(handle_id), lock_paths=lock_paths)

    def to_dict(self) -> dict[str, Any]:
        return {"handle_id": self.handle_id, "lock_paths": list(self.lock_paths)}


class LockLease:
    """Base lock lease interface."""

    @property
    def handle(self) -> Optional[LockHandle]:
        return None

    @property
    def handle_id(self) -> str:
        handle = self.handle
        return handle.id if handle else ""

    @property
    def active(self) -> bool:
        handle = self.handle
        return bool(handle and handle.locks)

    def as_borrowed(self) -> "LockLease":
        return self

    def to_handoff(self) -> Optional[LockHandoffRef]:
        return None

    async def close(self) -> None:
        return

    async def handoff(self) -> None:
        return


class _NoLockLease(LockLease):
    pass


NO_LOCK: LockLease = _NoLockLease()


@dataclass
class BorrowedLockLease(LockLease):
    """A lock lease borrowed from an outer owner."""

    manager: LockManager
    _handle: LockHandle

    @classmethod
    def from_handle(cls, manager: LockManager, handle: LockHandle) -> "BorrowedLockLease":
        return cls(manager=manager, _handle=handle)

    @property
    def handle(self) -> Optional[LockHandle]:
        return self.manager.get_handle(self._handle.id)

    def as_borrowed(self) -> "LockLease":
        return self


class OwnedLockLease(LockLease):
    """An owned lock lease that refreshes and releases its handle."""

    def __init__(
        self,
        manager: LockManager,
        handle: LockHandle,
        *,
        start_refresh: bool = True,
    ):
        self._manager = manager
        self._handle: Optional[LockHandle] = handle
        self._refresh_task: Optional[asyncio.Task] = None
        if start_refresh and handle.locks:
            self._start_refresh()

    @classmethod
    def from_handle(cls, manager: LockManager, handle: LockHandle) -> "OwnedLockLease":
        return cls(manager, handle)

    @classmethod
    async def from_handoff(
        cls,
        ref: LockHandoffRef,
        manager: Optional[LockManager] = None,
    ) -> "OwnedLockLease":
        manager = manager or get_lock_manager()
        handle = manager.get_handle(ref.handle_id)
        if handle is None:
            handle = manager.adopt_handle(ref.handle_id, ref.lock_paths)
        if handle is None:
            raise LockAcquisitionError(f"Lock handle is no longer active: {ref.handle_id}")
        return cls(manager, handle)

    @classmethod
    async def acquire_tree(
        cls,
        manager: LockManager,
        path: str,
        *,
        timeout: Optional[float] = None,
    ) -> "OwnedLockLease":
        handle = manager.create_handle()
        if await manager.acquire_tree(handle, path, timeout=timeout):
            return cls(manager, handle)
        await manager.release(handle)
        raise LockAcquisitionError(f"Failed to acquire tree lock for {path}")

    @classmethod
    async def acquire_exact_paths(
        cls,
        manager: LockManager,
        paths: Iterable[str],
        *,
        timeout: Optional[float] = None,
    ) -> "OwnedLockLease":
        handle = manager.create_handle()
        path_list = list(paths)
        if await manager.acquire_exact_path_batch(handle, path_list, timeout=timeout):
            return cls(manager, handle)
        await manager.release(handle)
        raise LockAcquisitionError(f"Failed to acquire exact lock for {path_list}")

    @property
    def handle(self) -> Optional[LockHandle]:
        if self._handle is None:
            return None
        return self._manager.get_handle(self._handle.id)

    def as_borrowed(self) -> LockLease:
        handle = self.handle
        if handle is None:
            return NO_LOCK
        return BorrowedLockLease.from_handle(self._manager, handle)

    def to_handoff(self) -> Optional[LockHandoffRef]:
        handle = self.handle
        if handle is None:
            return None
        return LockHandoffRef(handle_id=handle.id, lock_paths=tuple(handle.locks))

    async def close(self) -> None:
        await self._stop_refresh()
        handle = self.handle or self._handle
        self._handle = None
        if handle is not None:
            await self._manager.release(handle)

    async def handoff(self) -> None:
        """Stop managing this lease after another worker has received its handle."""
        await self._stop_refresh()
        self._handle = None

    def _start_refresh(self) -> None:
        if self._refresh_task is not None:
            return
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        try:
            expire = self._manager._path_lock._lock_expire
            interval = max(0.1, expire / 2)
        except Exception:
            interval = 150.0

        while True:
            try:
                await asyncio.sleep(interval)
                handle = self.handle
                if handle is None:
                    return
                await self._manager.refresh_lock(handle)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Failed to refresh lock handle %s: %s", self.handle_id, exc)

    async def _stop_refresh(self) -> None:
        if self._refresh_task is None:
            return
        self._refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._refresh_task
        self._refresh_task = None
