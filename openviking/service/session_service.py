# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Service for OpenViking.

Provides session management operations: session, sessions, add_message, commit, delete.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openviking.core.namespace import canonical_session_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
from openviking.session.archive_finalize_tasks import (
    STATE_COMPLETED,
    STATE_PREPARING,
    STATE_RUNNING,
    STATE_TERMINAL_FAILED,
    ArchiveFinalizeTask,
    ArchiveFinalizeTaskStore,
    get_archive_finalize_task_store,
)
from openviking.session.compressor import SessionCompressor
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    AlreadyExistsError,
    InvalidArgumentError,
    NotFoundError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class SessionService:
    """Session management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        session_compressor: Optional[SessionCompressor] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._session_compressor = session_compressor
        self._archive_task_store: Optional[ArchiveFinalizeTaskStore] = None
        self._archive_worker_task: Optional[asyncio.Task] = None
        self._archive_worker_stop: Optional[asyncio.Event] = None

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        session_compressor: SessionCompressor,
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._session_compressor = session_compressor
        self._archive_task_store = get_archive_finalize_task_store()
        self._start_archive_finalize_worker()

    async def close(self) -> None:
        """Stop background session workers."""
        if self._archive_worker_stop:
            self._archive_worker_stop.set()
        if self._archive_worker_task:
            self._archive_worker_task.cancel()
            try:
                await self._archive_worker_task
            except asyncio.CancelledError:
                pass
        self._archive_worker_task = None
        self._archive_worker_stop = None

    def _start_archive_finalize_worker(self) -> None:
        if self._archive_worker_task and not self._archive_worker_task.done():
            return
        self._archive_worker_stop = asyncio.Event()
        self._archive_worker_task = asyncio.create_task(self._archive_finalize_worker_loop())

    async def _archive_finalize_worker_loop(self) -> None:
        owner = f"session-archive-worker-{uuid4()}"
        while self._archive_worker_stop is not None and not self._archive_worker_stop.is_set():
            try:
                store = self._archive_task_store or get_archive_finalize_task_store()
                task = store.claim_next(owner)
                if task is None:
                    await asyncio.sleep(0.2)
                    continue
                await self._process_archive_finalize_task(store, task)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Archive finalize worker loop failed")
                await asyncio.sleep(0.5)

    async def _process_archive_finalize_task(
        self,
        store: ArchiveFinalizeTaskStore,
        task: ArchiveFinalizeTask,
    ) -> None:
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        ctx = task.request_context()
        session = self.session(ctx, task.session_id)
        try:
            await session.load()
        except Exception:
            logger.debug("Failed to load session %s before archive finalize", task.session_id)
        if task.claimed_from_state == STATE_PREPARING:
            messages_uri = f"{task.archive_uri}/messages.jsonl"
            if not await self._viking_fs.exists(messages_uri, ctx=ctx):
                store.delete(ctx, task.session_id, task.archive_id)
                get_task_tracker().fail(
                    task.task_tracker_id,
                    "archive_prepare_abandoned: messages.jsonl missing",
                    account_id=task.account_id,
                    user_id=task.user_id,
                )
                return
        try:
            await session.finalize_archive_from_task(
                task.task_tracker_id,
                task.archive_uri,
                task.usage_records,
            )
            store.complete(task)
        except asyncio.CancelledError:
            store.release(task)
            raise
        except Exception as exc:
            error = str(exc)
            state = store.fail(task, error)
            await session._write_failed_marker(
                task.archive_uri,
                stage="archive_finalize",
                error=error,
            )
            if state == STATE_TERMINAL_FAILED:
                tracker = get_task_tracker()
                tracker.fail(
                    task.task_tracker_id,
                    error,
                    account_id=task.account_id,
                    user_id=task.user_id,
                )
                logger.warning(
                    "Archive finalize terminal failed session=%s archive=%s error=%s",
                    task.session_id,
                    task.archive_id,
                    error,
                )

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

    @staticmethod
    def _record_lifecycle_metric(action: str, status: str) -> None:
        """Best-effort session lifecycle metrics should never break the main flow."""
        try:
            from openviking.metrics.datasources.session import SessionLifecycleDataSource

            SessionLifecycleDataSource.record_lifecycle(action=action, status=status)
        except Exception:
            logger.debug(
                "Failed to record session lifecycle metric action=%s status=%s",
                action,
                status,
                exc_info=True,
            )

    @staticmethod
    def _record_archive_metric(status: str) -> None:
        """Best-effort archive metrics should never break the main flow."""
        try:
            from openviking.metrics.datasources.session import SessionLifecycleDataSource

            SessionLifecycleDataSource.record_archive(status=status)
        except Exception:
            logger.debug(
                "Failed to record session archive metric status=%s",
                status,
                exc_info=True,
            )

    def session(self, ctx: RequestContext, session_id: Optional[str] = None) -> Session:
        """Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session (auto-generated ID) if None

        Returns:
            Session instance
        """
        self._ensure_initialized()
        return Session(
            viking_fs=self._viking_fs,
            vikingdb_manager=self._vikingdb,
            session_compressor=self._session_compressor,
            user=ctx.user,
            ctx=ctx,
            session_id=session_id,
        )

    async def create(self, ctx: RequestContext, session_id: Optional[str] = None) -> Session:
        """Create a session and persist its root path.

        Args:
            ctx: Request context
            session_id: Optional session ID. If provided, creates a session with the given ID.
                       If None, creates a new session with auto-generated ID.

        Raises:
            AlreadyExistsError: If a session with the given ID already exists
        """
        self._record_lifecycle_metric("create", "attempt")
        try:
            if session_id:
                existing = self.session(ctx, session_id)
                if await existing.exists():
                    raise AlreadyExistsError(f"Session '{session_id}' already exists")
            session = self.session(ctx, session_id)
            await session.ensure_exists()
            self._record_lifecycle_metric("create", "ok")
            return session
        except Exception:
            self._record_lifecycle_metric("create", "error")
            raise

    async def get(
        self, session_id: str, ctx: RequestContext, *, auto_create: bool = False
    ) -> Session:
        """Get an existing session.

        Args:
            session_id: Session ID
            ctx: Request context
            auto_create: If True, create the session when it does not exist.
                         Default is False (raise NotFoundError).
        """
        try:
            session = self.session(ctx, session_id)
            if not await session.exists():
                if not auto_create:
                    raise NotFoundError(session_id, "session")
                await session.ensure_exists()
            await session.load()
            self._record_lifecycle_metric("get", "ok")
            return session
        except Exception:
            self._record_lifecycle_metric("get", "error")
            raise

    async def sessions(self, ctx: RequestContext) -> List[Dict[str, Any]]:
        """Get all sessions for the current user.

        Returns:
            List of session info dicts
        """
        self._ensure_initialized()
        session_base_uri = canonical_session_uri()

        try:
            entries = await self._viking_fs.ls(session_base_uri, ctx=ctx)
            sessions = []
            for entry in entries:
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                sessions.append(
                    {
                        "session_id": name,
                        "uri": f"{session_base_uri}/{name}",
                        "is_dir": entry.get("isDir", False),
                    }
                )
            return sessions
        except Exception:
            logger.debug("Failed to list sessions", exc_info=True)
            return []

    async def delete(self, session_id: str, ctx: RequestContext) -> bool:
        """Delete a session.

        Args:
            session_id: Session ID to delete

        Returns:
            True if deleted successfully
        """
        self._ensure_initialized()
        if ctx.role not in {Role.ADMIN, Role.ROOT}:
            from openviking_cli.exceptions import PermissionDeniedError

            raise PermissionDeniedError("Deleting shared sessions requires ADMIN or ROOT role")

        session_uri = canonical_session_uri(session_id)

        try:
            await self._viking_fs.rm(session_uri, recursive=True, ctx=ctx)
            logger.info(f"Deleted session: {session_id}")
            self._record_lifecycle_metric("delete", "ok")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            self._record_lifecycle_metric("delete", "error")
            raise NotFoundError(session_id, "session")

    async def commit(
        self,
        session_id: str,
        ctx: RequestContext,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        """Commit a session (archive messages and extract memories).

        Delegates to commit_async() for true non-blocking behavior.

        Args:
            session_id: Session ID to commit
            keep_recent_count: See :meth:`commit_async`.

        Returns:
            Commit result
        """
        return await self.commit_async(session_id, ctx, keep_recent_count=keep_recent_count)

    async def commit_async(
        self,
        session_id: str,
        ctx: RequestContext,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        """Async commit a session.

        Archive payload writing runs inline. Archive finalization runs through
        the persistent SQLite task log and returns a task_id for polling.

        Args:
            session_id: Session ID to commit
            keep_recent_count: Number of most-recent messages to keep in the
                live session after commit. ``0`` archives everything.

        Returns:
            Commit result with keys: session_id, status, task_id,
            archive_uri, archived
        """
        self._ensure_initialized()
        session = await self.get(session_id, ctx)
        result = await session.commit_async(keep_recent_count=keep_recent_count)
        self._record_lifecycle_metric("commit", "ok" if result.get("status") else "error")
        self._record_archive_metric("ok" if result.get("archived") else "skip")
        return result

    async def get_commit_task(self, task_id: str, ctx: RequestContext) -> Optional[Dict[str, Any]]:
        """Query background commit task status by task_id for the calling owner."""
        task = get_task_tracker().get(
            task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        return task.to_dict() if task else None

    async def retry_archive_finalize(
        self,
        session_id: str,
        archive_id: str,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Retry a terminal failed archive finalize task."""
        self._ensure_initialized()
        store = self._archive_task_store or get_archive_finalize_task_store()
        task = store.get(ctx, session_id, archive_id)
        archive_uri = f"{canonical_session_uri(session_id)}/history/{archive_id}"

        if task is None:
            try:
                await self._viking_fs.read_file(f"{archive_uri}/messages.jsonl", ctx=ctx)
            except Exception as exc:
                raise NotFoundError(archive_id, "session archive") from exc
            tracker_task = get_task_tracker().create(
                "session_commit",
                resource_id=session_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            store.create_preparing(
                ctx=ctx,
                session_id=session_id,
                archive_id=archive_id,
                archive_uri=archive_uri,
                task_tracker_id=tracker_task.task_id,
                usage_records=[],
            )
            store.mark_pending(ctx, session_id, archive_id)
            task = store.get(ctx, session_id, archive_id)
        elif task.state == STATE_COMPLETED:
            return {"status": "already_completed", "task": task.to_dict()}
        elif task.state == STATE_RUNNING and task.lease_until > time.time():
            return {"status": STATE_RUNNING, "task": task.to_dict()}
        elif task.state != STATE_TERMINAL_FAILED:
            raise InvalidArgumentError(
                f"Archive {archive_id} is not terminal failed (state={task.state})"
            )
        else:
            tracker_task = get_task_tracker().create(
                "session_commit",
                resource_id=session_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
            task = store.reset_for_retry(task, task_tracker_id=tracker_task.task_id)

        return {"status": "accepted", "task": task.to_dict() if task else None}

    async def extract(self, session_id: str, ctx: RequestContext) -> List[Any]:
        """Extract memories from a session.

        Args:
            session_id: Session ID to extract from

        Returns:
            List of extracted memories
        """
        self._ensure_initialized()
        if not self._session_compressor:
            raise NotInitializedError("SessionCompressor")

        session = await self.get(session_id, ctx)
        session_uri = canonical_session_uri(session_id)
        archive_uri = f"{session_uri}/manual_extract"

        memories = await self._session_compressor.extract_long_term_memories(
            messages=session.messages,
            user=ctx.user,
            session_id=session_id,
            ctx=ctx,
            archive_uri=archive_uri,
        )
        self._record_lifecycle_metric("extract", "ok")
        return memories
