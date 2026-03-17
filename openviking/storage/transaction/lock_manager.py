# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""LockManager — global singleton managing lock lifecycle and redo recovery."""

import asyncio
import json
import time
from typing import Any, Dict, Optional

from openviking.pyagfs import AGFSClient
from openviking.storage.transaction.lock_handle import LockHandle
from openviking.storage.transaction.path_lock import PathLock
from openviking.storage.transaction.redo_log import RedoLog
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class LockManager:
    """Global singleton. Manages lock lifecycle and stale cleanup."""

    def __init__(
        self,
        agfs: AGFSClient,
        lock_timeout: float = 0.0,
        lock_expire: float = 300.0,
    ):
        self._agfs = agfs
        self._path_lock = PathLock(agfs, lock_expire=lock_expire)
        self._lock_timeout = lock_timeout
        self._redo_log = RedoLog(agfs)
        self._handles: Dict[str, LockHandle] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def redo_log(self) -> RedoLog:
        return self._redo_log

    def get_active_handles(self) -> Dict[str, LockHandle]:
        return dict(self._handles)

    async def start(self) -> None:
        """Start background cleanup and redo recovery."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._stale_cleanup_loop())
        await self._recover_pending_redo()

    async def stop(self) -> None:
        """Stop cleanup and release all active locks."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        for handle in list(self._handles.values()):
            await self._path_lock.release(handle)
        self._handles.clear()

    def create_handle(self) -> LockHandle:
        handle = LockHandle()
        self._handles[handle.id] = handle
        return handle

    async def acquire_point(
        self, handle: LockHandle, path: str, timeout: Optional[float] = None
    ) -> bool:
        return await self._path_lock.acquire_point(
            path, handle, timeout=timeout if timeout is not None else self._lock_timeout
        )

    async def acquire_subtree(
        self, handle: LockHandle, path: str, timeout: Optional[float] = None
    ) -> bool:
        return await self._path_lock.acquire_subtree(
            path, handle, timeout=timeout if timeout is not None else self._lock_timeout
        )

    async def acquire_mv(
        self,
        handle: LockHandle,
        src: str,
        dst: str,
        src_is_dir: bool = True,
        timeout: Optional[float] = None,
    ) -> bool:
        return await self._path_lock.acquire_mv(
            src,
            dst,
            handle,
            timeout=timeout if timeout is not None else self._lock_timeout,
            src_is_dir=src_is_dir,
        )

    async def release(self, handle: LockHandle) -> None:
        await self._path_lock.release(handle)
        self._handles.pop(handle.id, None)

    async def _stale_cleanup_loop(self) -> None:
        """Check and release leaked handles every 60 s (in-process safety net)."""
        while self._running:
            await asyncio.sleep(60)
            now = time.time()
            stale = [h for h in self._handles.values() if now - h.created_at > 3600]
            for handle in stale:
                logger.warning(f"Releasing stale lock handle {handle.id}")
                await self.release(handle)

    # ------------------------------------------------------------------
    # Redo recovery (session_memory only)
    # ------------------------------------------------------------------

    async def _recover_pending_redo(self) -> None:
        pending_ids = self._redo_log.list_pending()
        for task_id in pending_ids:
            logger.info(f"Recovering pending redo task: {task_id}")
            try:
                info = self._redo_log.read(task_id)
                if info:
                    await self._redo_session_memory(info)
                self._redo_log.mark_done(task_id)
            except Exception as e:
                logger.error(f"Redo recovery failed for {task_id}: {e}", exc_info=True)

    async def _redo_session_memory(self, info: Dict[str, Any]) -> None:
        """Re-extract memories from archive."""
        from openviking.message import Message
        from openviking.server.identity import RequestContext, Role
        from openviking.session.compressor import SessionCompressor
        from openviking_cli.session.user_id import UserIdentifier

        archive_uri = info.get("archive_uri")
        session_uri = info.get("session_uri")
        account_id = info.get("account_id", "default")
        user_id = info.get("user_id", "default")
        agent_id = info.get("agent_id", "default")
        role_str = info.get("role", "root")

        if not archive_uri or not session_uri:
            logger.warning("Cannot redo session_memory: missing archive_uri or session_uri")
            return

        # 1. Read archived messages
        messages_path = f"{archive_uri}/messages.jsonl"
        try:
            agfs_path = messages_path.replace("viking://", "")
            content = self._agfs.cat(agfs_path)
            if isinstance(content, bytes):
                content = content.decode("utf-8")
        except Exception as e:
            logger.warning(f"Cannot read archive for redo: {messages_path}: {e}")
            return

        messages = []
        for line in content.strip().split("\n"):
            if line.strip():
                try:
                    messages.append(Message.from_dict(json.loads(line)))
                except Exception:
                    pass

        if not messages:
            logger.warning(f"No messages found in archive for redo: {archive_uri}")
            return

        # 2. Build request context
        user = UserIdentifier(account_id=account_id, user_id=user_id, agent_id=agent_id)
        ctx = RequestContext(user=user, role=Role(role_str))

        # 3. Re-extract memories (best-effort: skip if compressor not available)
        session_id = session_uri.rstrip("/").rsplit("/", 1)[-1]
        try:
            compressor = SessionCompressor(vikingdb=None)
            memories = await compressor.extract_long_term_memories(
                messages=messages,
                user=user,
                session_id=session_id,
                ctx=ctx,
            )
            logger.info(f"Redo: extracted {len(memories)} memories from {archive_uri}")
        except Exception as e:
            logger.warning(f"Redo: memory extraction skipped ({e}), will retry via queue")

        # 4. Enqueue semantic processing
        await self._enqueue_semantic(
            uri=session_uri,
            context_type="memory",
            account_id=account_id,
            user_id=user_id,
            agent_id=agent_id,
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
            agent_id=params.get("agent_id", "default"),
            role=params.get("role", "root"),
        )
        semantic_queue: SemanticQueue = queue_manager.get_queue(queue_manager.SEMANTIC)  # type: ignore[assignment]
        await semantic_queue.enqueue(msg)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_lock_manager: Optional[LockManager] = None


def init_lock_manager(
    agfs: AGFSClient,
    lock_timeout: float = 0.0,
    lock_expire: float = 300.0,
) -> LockManager:
    global _lock_manager
    _lock_manager = LockManager(agfs=agfs, lock_timeout=lock_timeout, lock_expire=lock_expire)
    return _lock_manager


def get_lock_manager() -> LockManager:
    if _lock_manager is None:
        raise RuntimeError("LockManager not initialized. Call init_lock_manager() first.")
    return _lock_manager


def reset_lock_manager() -> None:
    global _lock_manager
    _lock_manager = None
