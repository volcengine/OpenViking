# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LockManager — global singleton managing lock lifecycle and redo recovery."""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Protocol

from openviking.pyagfs import AGFSSyncClientProtocol, AsyncAGFSClient
from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.path_lock import PathLockEngine
from openviking.storage.transaction.redo_log import RedoLog
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
        redo_recovery_enabled: bool = True,
    ):
        self._agfs = agfs
        self._async_agfs = AsyncAGFSClient(agfs)
        self._path_lock = PathLockEngine(agfs, lock_expire=lock_expire)
        self._lock_timeout = lock_timeout
        self._redo_recovery_enabled = redo_recovery_enabled
        self._redo_log = RedoLog(agfs)
        self._handles: Dict[str, LockHandle] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._redo_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def redo_log(self) -> RedoLog:
        return self._redo_log

    @property
    def redo_recovery_enabled(self) -> bool:
        return self._redo_recovery_enabled

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
        """Start background cleanup and redo recovery."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._stale_cleanup_loop())
        if self._redo_recovery_enabled:
            self._redo_task = asyncio.create_task(self._recover_pending_redo())
        else:
            logger.info("Redo recovery disabled by config; skipping pending redo recovery")

    async def stop(self) -> None:
        """Stop cleanup and release all active locks."""
        self._running = False
        if self._redo_task:
            self._redo_task.cancel()
            try:
                await self._redo_task
            except asyncio.CancelledError:
                pass
            self._redo_task = None
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

    # ------------------------------------------------------------------
    # Redo recovery (session_memory only)
    # ------------------------------------------------------------------

    async def _recover_pending_redo(self) -> None:
        pending_ids = await self._redo_log.list_pending_async()
        for task_id in pending_ids:
            logger.info(f"Recovering pending redo task: {task_id}")
            try:
                info = await self._redo_log.read_async(task_id)
                if info:
                    await self._redo_session_memory(info)
                await self._redo_log.mark_done_async(task_id)
            except Exception as e:
                logger.error(f"Redo recovery failed for {task_id}: {e}", exc_info=True)

    async def _redo_session_memory(self, info: Dict[str, Any]) -> None:
        """Re-extract memories from archive.

        Lets exceptions from _enqueue_semantic propagate so the caller
        can decide whether to mark the redo task as done.
        """
        from openviking.message import Message
        from openviking.server.identity import RequestContext, Role
        from openviking.storage.viking_fs import get_viking_fs
        from openviking_cli.session.user_id import UserIdentifier

        archive_uri = info.get("archive_uri")
        session_uri = info.get("session_uri")
        account_id = info.get("account_id", "default")
        user_id = info.get("user_id", "default")
        role_str = info.get("role", "root")

        if not archive_uri or not session_uri:
            raise ValueError("Cannot redo session_memory: missing archive_uri or session_uri")

        # 1. Build request context (needed for path conversion below)
        user = UserIdentifier(account_id=account_id, user_id=user_id)
        ctx = RequestContext(user=user, role=Role(role_str))

        # 2. Read archived messages
        messages_uri = f"{archive_uri}/messages.jsonl"
        viking_fs = get_viking_fs()
        agfs_path = viking_fs._uri_to_path(messages_uri, ctx=ctx)
        messages = []
        try:
            content = await self._async_agfs.cat(agfs_path)
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            for line in content.strip().split("\n"):
                if line.strip():
                    try:
                        messages.append(Message.from_dict(json.loads(line)))
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Cannot read archive for redo: {agfs_path}: {e}")

        # 3. Re-extract memories (best-effort, only if archive was readable)
        if messages:
            session_id = session_uri.rstrip("/").rsplit("/", 1)[-1]
            try:
                from openviking.session import create_session_compressor

                compressor = create_session_compressor(vikingdb=None)
                memories = await asyncio.wait_for(
                    compressor.extract_long_term_memories(
                        messages=messages,
                        user=user,
                        session_id=session_id,
                        ctx=ctx,
                    ),
                    timeout=60.0,
                )
                logger.info(f"Redo: extracted {len(memories)} memories from {archive_uri}")
            except Exception as e:
                logger.warning(f"Redo: memory extraction failed ({e}), falling back to queue")

        # 4. Always enqueue semantic processing as fallback
        await self._enqueue_semantic(
            uri=session_uri,
            context_type="memory",
            account_id=account_id,
            user_id=user_id,
            peer_id=user_id,
            role=role_str,
        )

    async def _enqueue_semantic(self, **params: Any) -> None:
        from openviking.storage.queuefs import get_queue_manager
        from openviking.storage.queuefs.semantic_msg import SemanticMsg
        from openviking.storage.queuefs.semantic_queue import SemanticQueue

        queue_manager = get_queue_manager()
        if queue_manager is None:
            logger.debug("No queue manager available, skipping enqueue_semantic")
            return

        uri = params.get("uri")
        if not uri:
            return

        msg = SemanticMsg(
            uri=uri,
            context_type=params.get("context_type", "resource"),
            account_id=params.get("account_id", "default"),
            user_id=params.get("user_id", "default"),
            peer_id=params.get("peer_id", "default"),
            role=params.get("role", "root"),
        )
        semantic_queue: SemanticQueue = queue_manager.get_queue(queue_manager.SEMANTIC)  # type: ignore[assignment]
        await semantic_queue.enqueue(msg)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lock_manager: Optional[LockManager] = None


def init_lock_manager(
    agfs: AGFSSyncClientProtocol,
    lock_timeout: float = 0.0,
    lock_expire: float = 300.0,
    redo_recovery_enabled: bool = True,
) -> LockManager:
    global _lock_manager
    _lock_manager = LockManager(
        agfs=agfs,
        lock_timeout=lock_timeout,
        lock_expire=lock_expire,
        redo_recovery_enabled=redo_recovery_enabled,
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
