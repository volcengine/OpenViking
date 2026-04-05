# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LockContext — async context manager for acquiring/releasing path locks."""

from typing import Optional

from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.lock_manager import LockManager


class LockContext:
    """``async with LockContext(manager, paths, mode) as handle: ...``

    Acquires locks on entry, releases them on exit. No undo / journal / commit
    semantics — just a lock scope.
    """

    def __init__(
        self,
        lock_manager: LockManager,
        paths: list[str],
        lock_mode: str = "point",
        mv_dst_parent_path: Optional[str] = None,
        src_is_dir: bool = True,
        handle: Optional[LockHandle] = None,
    ):
        self._manager = lock_manager
        self._paths = paths
        self._lock_mode = lock_mode
        self._mv_dst_parent_path = mv_dst_parent_path
        self._src_is_dir = src_is_dir
        self._handle: Optional[LockHandle] = handle
        self._owns_handle = handle is None
        self._locks_before: list[str] = []
        self._acquired_lock_paths: list[str] = []

    async def __aenter__(self) -> LockHandle:
        if self._handle is None:
            self._handle = self._manager.create_handle()
        self._locks_before = list(self._handle.locks)
        success = False

        if self._lock_mode == "subtree":
            for path in self._paths:
                success = await self._manager.acquire_subtree(self._handle, path)
                if not success:
                    break
        elif self._lock_mode == "mv":
            if self._mv_dst_parent_path is None:
                raise LockAcquisitionError("mv lock mode requires mv_dst_parent_path")
            success = await self._manager.acquire_mv(
                self._handle,
                self._paths[0],
                self._mv_dst_parent_path,
                src_is_dir=self._src_is_dir,
            )
        else:  # "point"
            for path in self._paths:
                success = await self._manager.acquire_point(self._handle, path)
                if not success:
                    break

        self._acquired_lock_paths = [
            lock_path for lock_path in self._handle.locks if lock_path not in self._locks_before
        ]

        if not success:
            if self._owns_handle:
                await self._manager.release(self._handle)
            else:
                await self._manager.release_selected(self._handle, self._acquired_lock_paths)
            raise LockAcquisitionError(
                f"Failed to acquire {self._lock_mode} lock for {self._paths}"
            )
        return self._handle

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._handle:
            if self._owns_handle:
                await self._manager.release(self._handle)
            else:
                await self._manager.release_selected(self._handle, self._acquired_lock_paths)
        return False
