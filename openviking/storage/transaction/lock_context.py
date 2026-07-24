# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LockContext — async context manager for acquiring/releasing path locks."""

import asyncio
from typing import Any, Optional

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.lock_lease import OwnedLockLease
from openviking.storage.transaction.lock_manager import LOCK_TIMEOUT_DEFAULT, LockManager


class LockContext:
    """``async with LockContext(manager, paths, mode) as handle: ...``

    Acquires locks on entry, releases them on exit. No undo / journal / commit
    semantics — just a lock scope.
    """

    def __init__(
        self,
        lock_manager: LockManager,
        paths: list[str],
        lock_mode: str = "exact",
        mv_dst_path: Optional[str] = None,
        src_is_dir: bool = True,
        handle: Optional[LockHandle] = None,
        timeout: Any = LOCK_TIMEOUT_DEFAULT,
    ):
        self._manager = lock_manager
        self._paths = paths
        self._lock_mode = lock_mode
        self._mv_dst_path = mv_dst_path
        self._src_is_dir = src_is_dir
        self._handle: Optional[LockHandle] = handle
        self._timeout = timeout
        self._owns_handle = handle is None
        self._locks_before: list[str] = []
        self._acquired_lock_paths: list[str] = []
        self._owned_lease: Optional[OwnedLockLease] = None

    async def __aenter__(self) -> LockHandle:
        if self._handle is None:
            self._handle = self._manager.create_handle()
        self._locks_before = list(self._handle.locks)
        success = False

        if self._lock_mode == "tree":
            for path in self._paths:
                success = await self._await_acquisition(
                    self._manager.acquire_tree(self._handle, path, timeout=self._timeout)
                )
                if not success:
                    break
        elif self._lock_mode == "exact":
            success = await self._await_acquisition(
                self._manager.acquire_exact_path_batch(
                    self._handle, self._paths, timeout=self._timeout
                )
            )
        elif self._lock_mode == "mv":
            if self._mv_dst_path is None:
                raise LockAcquisitionError("mv lock mode requires mv_dst_path")
            acquire_src = (
                self._manager.acquire_tree if self._src_is_dir else self._manager.acquire_exact_path
            )
            success = await self._await_acquisition(
                acquire_src(self._handle, self._paths[0], timeout=self._timeout)
            )
            if success:
                success = await self._await_acquisition(
                    self._manager.acquire_exact_path(
                        self._handle, self._mv_dst_path, timeout=self._timeout
                    )
                )
        else:
            raise LockAcquisitionError(f"Unsupported lock mode: {self._lock_mode}")

        self._acquired_lock_paths = [
            lock_path for lock_path in self._handle.locks if lock_path not in self._locks_before
        ]

        if not success:
            await self._release_acquired(self._acquired_lock_paths)
            raise LockAcquisitionError(
                f"Failed to acquire {self._lock_mode} lock for {self._paths}"
            )
        if self._owns_handle and self._handle.locks:
            self._owned_lease = OwnedLockLease.from_handle(self._manager, self._handle)
        return self._handle

    async def _await_acquisition(self, acquisition: Any) -> bool:
        try:
            return await acquisition
        except BaseException:
            acquired = [path for path in self._handle.locks if path not in self._locks_before]
            await self._release_acquired(acquired)
            raise

    async def _release_acquired(self, acquired: list[str]) -> None:
        cleanup = asyncio.create_task(
            self._manager.release(self._handle)
            if self._owns_handle
            else self._manager.release_selected(self._handle, acquired)
        )
        cancelled = False
        try:
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
        except Exception:
            pass
        if cancelled:
            raise asyncio.CancelledError

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._handle:
            if self._owns_handle:
                if self._owned_lease:
                    await self._owned_lease.close()
                else:
                    await self._manager.release(self._handle)
            else:
                await self._manager.release_selected(self._handle, self._acquired_lock_paths)
        return False
