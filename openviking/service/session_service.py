# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Service for OpenViking.

Provides session management operations: session, sessions, add_message, commit, delete.
"""

from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
from openviking.session.compressor import SessionCompressor
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import AlreadyExistsError, NotFoundError, NotInitializedError
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

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

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
        if session_id:
            existing = self.session(ctx, session_id)
            if await existing.exists():
                raise AlreadyExistsError(f"Session '{session_id}' already exists")
        session = self.session(ctx, session_id)
        await session.ensure_exists()
        return session

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
        session = self.session(ctx, session_id)
        if not await session.exists():
            if not auto_create:
                raise NotFoundError(session_id, "session")
            await session.ensure_exists()
        await session.load()
        return session

    async def sessions(self, ctx: RequestContext) -> List[Dict[str, Any]]:
        """Get all sessions for the current user.

        Returns:
            List of session info dicts
        """
        self._ensure_initialized()
        session_base_uri = f"viking://session/{ctx.user.user_space_name()}"

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
            return []

    async def delete(self, session_id: str, ctx: RequestContext) -> bool:
        """Delete a session.

        Args:
            session_id: Session ID to delete

        Returns:
            True if deleted successfully
        """
        self._ensure_initialized()
        session_uri = f"viking://session/{ctx.user.user_space_name()}/{session_id}"

        try:
            await self._viking_fs.rm(session_uri, recursive=True, ctx=ctx)
            logger.info(f"Deleted session: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            raise NotFoundError(session_id, "session")

    async def commit(self, session_id: str, ctx: RequestContext) -> Dict[str, Any]:
        """Commit a session (archive messages and extract memories).

        Delegates to commit_async() for true non-blocking behavior.

        Args:
            session_id: Session ID to commit

        Returns:
            Commit result
        """
        return await self.commit_async(session_id, ctx)

    async def commit_async(self, session_id: str, ctx: RequestContext) -> Dict[str, Any]:
        """Async commit a session.

        Phase 1 (archive) always runs inline.  Phase 2 (memory extraction)
        runs in a background task, returning a task_id for polling.

        Args:
            session_id: Session ID to commit

        Returns:
            Commit result with keys: session_id, status, task_id,
            archive_uri, archived
        """
        self._ensure_initialized()
        session = await self.get(session_id, ctx)
        return await session.commit_async()

    async def get_commit_task(self, task_id: str, ctx: RequestContext) -> Optional[Dict[str, Any]]:
        """Query background commit task status by task_id for the calling owner."""
        task = get_task_tracker().get(
            task_id,
            owner_account_id=ctx.account_id,
            owner_user_id=ctx.user.user_id,
        )
        return task.to_dict() if task else None

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

        return await self._session_compressor.extract_long_term_memories(
            messages=session.messages,
            user=ctx.user,
            session_id=session_id,
            ctx=ctx,
        )
