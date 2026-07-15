# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LockManager — global singleton managing path-lock lifecycle."""

import asyncio
import time
from typing import Any, Dict, List, Optional, Protocol

from openviking.pyagfs import AGFSSyncClientProtocol
from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.path_lock import PathLockEngine
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_HANDLE_CLEANUP_INTERVAL_SECONDS = 60.0
LOCK_TIMEOUT_DEFAULT = object()


class _LockManagerLike(Protocol):
    def get_handle(self, handle_id: str) -> Optional[LockHandle]: ...


async def get_lock_handle_async(
    lock_manager: _LockManagerLike, handle_id: str
) -> Optional[LockHandle]:
    getter = getattr(lock_manager, "get_handle_async", None)
    if getter is not None:
        return await getter(handle_id)
    return await asyncio.to_thread(lock_manager.get_handle, handle_id)


class LockManager:
    """Global singleton. Manages lock lifecycle and stale cleanup."""

    def __init__(
        self,
        agfs: AGFSSyncClientProtocol,
        lock_timeout: float = 0.0,
        lock_expire: float = 300.0,
    ):
        self._path_lock = PathLockEngine(agfs, lock_expire=lock_expire)
        self._lock_timeout = lock_timeout
        self._handles: Dict[str, LockHandle] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    def _resolve_timeout(self, timeout: Any) -> Optional[float]:
        return self._lock_timeout if timeout is LOCK_TIMEOUT_DEFAULT else timeout

    def _mark_handle_active(self, handle: LockHandle) -> None:
        handle.last_active_at = time.time()

    def get_active_handles(self) -> Dict[str, LockHandle]:
        active_handles: Dict[str, LockHandle] = {}
        for handle in list(self._handles.values()):
            current = self._reconcile_handle(handle)
            if current and current.locks:
                active_handles[current.id] = current
        return active_handles

    async def get_active_handles_async(self) -> Dict[str, LockHandle]:
        active_handles: Dict[str, LockHandle] = {}
        for handle in list(self._handles.values()):
            current = await self._reconcile_handle_async(handle)
            if current and current.locks:
                active_handles[current.id] = current
        return active_handles

    async def start(self) -> None:
        """Start background cleanup."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._stale_cleanup_loop())

    async def stop(self) -> None:
        """Stop cleanup and release all active locks."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                if self._cleanup_task.get_loop() is asyncio.get_running_loop():
                    await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        for handle in list(self._handles.values()):
            await self._path_lock.release(handle)
        self._handles.clear()

    def create_handle(self) -> LockHandle:
        handle = LockHandle()
        self._handles[handle.id] = handle
        return handle

    async def acquire_exact_path(
        self,
        handle: LockHandle,
        path: str,
        timeout: Any = LOCK_TIMEOUT_DEFAULT,
    ) -> bool:
        acquired = await self._path_lock.acquire_exact_path(
            path, handle, timeout=self._resolve_timeout(timeout)
        )
        if acquired:
            self._mark_handle_active(handle)
        return acquired

    async def acquire_tree(
        self,
        handle: LockHandle,
        path: str,
        timeout: Any = LOCK_TIMEOUT_DEFAULT,
    ) -> bool:
        acquired = await self._path_lock.acquire_tree(
            path, handle, timeout=self._resolve_timeout(timeout)
        )
        if acquired:
            self._mark_handle_active(handle)
        return acquired

    async def acquire_tree_batch(
        self,
        handle: LockHandle,
        paths: List[str],
        timeout: Any = LOCK_TIMEOUT_DEFAULT,
    ) -> bool:
        """
        一次性对多个路径进行树锁加锁，使用有序加锁法防止死锁

        核心思想：
        1. 对路径按照固定的顺序进行排序，确保所有进程获取锁的顺序一致
        2. 防止循环等待条件，从而避免死锁

        排序规则：
        1. 路径长度升序
        2. 长度相同的路径按照字典序升序

        Args:
            handle: 锁句柄
            paths: 需要加锁的路径列表
            timeout: 超时时间，None表示无限等待

        Returns:
            是否成功获取所有锁
        """
        if not paths:
            self._mark_handle_active(handle)
            return True

        sorted_paths = sorted(paths, key=lambda x: (len(x), x))
        acquired_lock_paths: List[str] = []

        try:
            for path in sorted_paths:
                locks_before = set(handle.locks)
                success = await self._path_lock.acquire_tree(
                    path,
                    handle,
                    timeout=self._resolve_timeout(timeout),
                )
                if not success:
                    await self._path_lock.release_selected(handle, acquired_lock_paths)
                    return False
                newly = [lp for lp in handle.locks if lp not in locks_before]
                acquired_lock_paths.extend(newly)

            self._mark_handle_active(handle)
            return True

        except Exception as e:
            logger.error(f"Failed to acquire tree batch lock: {e}")
            await self._path_lock.release_selected(handle, acquired_lock_paths)
            return False

    async def acquire_exact_path_batch(
        self,
        handle: LockHandle,
        paths: List[str],
        timeout: Any = LOCK_TIMEOUT_DEFAULT,
    ) -> bool:
        if not paths:
            self._mark_handle_active(handle)
            return True

        sorted_paths = sorted(dict.fromkeys(paths), key=lambda x: (len(x), x))
        acquired_lock_paths: List[str] = []

        try:
            for path in sorted_paths:
                locks_before = set(handle.locks)
                success = await self._path_lock.acquire_exact_path(
                    path,
                    handle,
                    timeout=self._resolve_timeout(timeout),
                )
                if not success:
                    await self._path_lock.release_selected(handle, acquired_lock_paths)
                    return False
                newly = [lp for lp in handle.locks if lp not in locks_before]
                acquired_lock_paths.extend(newly)

            self._mark_handle_active(handle)
            return True

        except Exception as e:
            logger.error(f"Failed to acquire exact-path batch lock: {e}")
            await self._path_lock.release_selected(handle, acquired_lock_paths)
            return False

    async def acquire_exact_tree_batch(
        self,
        handle: LockHandle,
        exact_paths: List[str],
        tree_paths: List[str],
        timeout: Any = LOCK_TIMEOUT_DEFAULT,
    ) -> bool:
        exact_set = set(exact_paths)
        tree_set = set(tree_paths)
        exact_only = [p for p in exact_set if p not in tree_set]
        all_pairs = [(p, False) for p in exact_only] + [(p, True) for p in tree_set]

        if not all_pairs:
            self._mark_handle_active(handle)
            return True

        sorted_pairs = sorted(all_pairs, key=lambda x: (len(x[0]), x[0]))
        acquired_lock_paths: List[str] = []

        try:
            for path, is_tree in sorted_pairs:
                locks_before = set(handle.locks)
                if is_tree:
                    success = await self._path_lock.acquire_tree(
                        path,
                        handle,
                        timeout=self._resolve_timeout(timeout),
                    )
                else:
                    success = await self._path_lock.acquire_exact_path(
                        path,
                        handle,
                        timeout=self._resolve_timeout(timeout),
                    )
                if not success:
                    await self._path_lock.release_selected(handle, acquired_lock_paths)
                    return False
                newly = [lp for lp in handle.locks if lp not in locks_before]
                acquired_lock_paths.extend(newly)

            self._mark_handle_active(handle)
            return True

        except Exception as e:
            logger.error(f"Failed to acquire exact/tree batch lock: {e}")
            await self._path_lock.release_selected(handle, acquired_lock_paths)
            return False

    async def acquire_mv(
        self,
        handle: LockHandle,
        src: str,
        dst: str,
        src_is_dir: bool = True,
        timeout: Any = LOCK_TIMEOUT_DEFAULT,
    ) -> bool:
        acquired = await self._path_lock.acquire_mv(
            src,
            dst,
            handle,
            timeout=self._resolve_timeout(timeout),
            src_is_dir=src_is_dir,
        )
        if acquired:
            self._mark_handle_active(handle)
        return acquired

    def get_handle(self, handle_id: str) -> Optional[LockHandle]:
        handle = self._handles.get(handle_id)
        if handle is None:
            return None
        current = self._reconcile_handle(handle)
        if current is None or not current.locks:
            return None
        return current

    def adopt_handle(self, handle_id: str, lock_paths: List[str]) -> Optional[LockHandle]:
        handle = self.get_handle(handle_id)
        if handle is not None:
            return handle

        adopted = LockHandle(id=handle_id)
        for lock_path in dict.fromkeys(lock_paths):
            if self._path_lock.is_lock_owned_by(lock_path, handle_id):
                adopted.add_lock(lock_path)
        if not adopted.locks:
            return None

        self._handles[adopted.id] = adopted
        self._mark_handle_active(adopted)
        return adopted

    async def adopt_handle_async(
        self, handle_id: str, lock_paths: List[str]
    ) -> Optional[LockHandle]:
        handle = await self.get_handle_async(handle_id)
        if handle is not None:
            return handle

        adopted = LockHandle(id=handle_id)
        for lock_path in dict.fromkeys(lock_paths):
            if await self._path_lock.is_lock_owned_by_async(lock_path, handle_id):
                adopted.add_lock(lock_path)
        if not adopted.locks:
            return None

        self._handles[adopted.id] = adopted
        self._mark_handle_active(adopted)
        return adopted

    async def get_handle_async(self, handle_id: str) -> Optional[LockHandle]:
        handle = self._handles.get(handle_id)
        if handle is None:
            return None
        current = await self._reconcile_handle_async(handle)
        if current is None or not current.locks:
            return None
        return current

    def is_path_locked(self, path: str, ignore_stale: bool = True) -> bool:
        """Check whether *path* is currently locked.

        Semantics align with conflict detection in the acquire flow: the path
        is considered locked if it (or any ancestor) holds a lock. By default,
        stale locks are ignored because they will be reclaimed on the next
        acquire attempt.
        """
        try:
            return self._path_lock.is_locked(path, ignore_stale=ignore_stale)
        except Exception as e:
            logger.warning(f"is_path_locked failed for {path}: {e}")
            return False

    async def is_path_locked_async(self, path: str, ignore_stale: bool = True) -> bool:
        """Async variant for request/background paths."""
        try:
            return await self._path_lock.is_locked_async(path, ignore_stale=ignore_stale)
        except Exception as e:
            logger.warning(f"is_path_locked_async failed for {path}: {e}")
            return False

    async def refresh_lock(self, handle: LockHandle) -> None:
        current = await self._reconcile_handle_async(handle)
        if current is None:
            return

        result = await self._path_lock.refresh(current)
        for lock_path in result.lost_paths:
            current.remove_lock(lock_path)

        if result.refreshed_paths:
            self._mark_handle_active(current)

        await self._reconcile_handle_async(current)

    async def release(self, handle: LockHandle) -> None:
        await self._path_lock.release(handle)
        self._handles.pop(handle.id, None)

    async def release_selected(self, handle: LockHandle, lock_paths: List[str]) -> None:
        await self._path_lock.release_selected(handle, lock_paths)

    async def _stale_cleanup_loop(self) -> None:
        """Check and release leaked handles every 60 s (in-process safety net)."""
        while self._running:
            await asyncio.sleep(_HANDLE_CLEANUP_INTERVAL_SECONDS)
            now = time.time()
            stale = []
            for handle in list(self._handles.values()):
                current = await self._reconcile_handle_async(handle)
                if current and self._is_handle_stale(current, now):
                    stale.append(current)
            for handle in stale:
                logger.warning(f"Releasing stale lock handle {handle.id}")
                await self.release(handle)

    def _is_handle_stale(self, handle: LockHandle, now: Optional[float] = None) -> bool:
        """Return True when a live lock handle has stopped refreshing for too long."""
        if not handle.locks:
            return False
        if now is None:
            now = time.time()
        return now - handle.last_active_at > self._path_lock._lock_expire

    def _reconcile_handle(self, handle: LockHandle) -> Optional[LockHandle]:
        had_locks = bool(handle.locks)
        lost_paths = self._path_lock.collect_lost_owner_locks(handle)
        for lock_path in lost_paths:
            handle.remove_lock(lock_path)
        if had_locks and not handle.locks:
            self._handles.pop(handle.id, None)
            return None
        return handle

    async def _reconcile_handle_async(self, handle: LockHandle) -> Optional[LockHandle]:
        had_locks = bool(handle.locks)
        lost_paths = await self._path_lock.collect_lost_owner_locks_async(handle)
        for lock_path in lost_paths:
            handle.remove_lock(lock_path)
        if had_locks and not handle.locks:
            self._handles.pop(handle.id, None)
            return None
        return handle

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lock_manager: Optional[LockManager] = None


def init_lock_manager(
    agfs: AGFSSyncClientProtocol,
    lock_timeout: float = 0.0,
    lock_expire: float = 300.0,
) -> LockManager:
    global _lock_manager
    _lock_manager = LockManager(
        agfs=agfs,
        lock_timeout=lock_timeout,
        lock_expire=lock_expire,
    )
    return _lock_manager


def get_lock_manager() -> LockManager:
    if _lock_manager is None:
        raise RuntimeError("LockManager not initialized. Call init_lock_manager() first.")
    return _lock_manager


def reset_lock_manager() -> None:
    global _lock_manager
    _lock_manager = None


async def release_all_locks() -> None:
    """Release all active lock handles. **Test-only utility.**"""
    if _lock_manager is None:
        return
    for handle in list((await _lock_manager.get_active_handles_async()).values()):
        await _lock_manager.release(handle)
