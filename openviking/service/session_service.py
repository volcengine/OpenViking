# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Service for OpenViking.

Provides session management operations: session, sessions, add_message, commit, delete.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional

from openviking.core.namespace import canonical_session_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session, ToolSkillCandidateMemory
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
        try:
            from openviking.metrics.datasources.session import SessionLifecycleDataSource

            SessionLifecycleDataSource.record_lifecycle(action="create", status="attempt")
        except Exception:
            pass
        try:
            if session_id:
                existing = self.session(ctx, session_id)
                if await existing.exists():
                    raise AlreadyExistsError(f"Session '{session_id}' already exists")
            session = self.session(ctx, session_id)
            await session.ensure_exists()
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_lifecycle(action="create", status="ok")
            except Exception:
                pass
            return session
        except Exception:
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_lifecycle(action="create", status="error")
            except Exception:
                pass
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
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_lifecycle(action="get", status="ok")
            except Exception:
                pass
            return session
        except Exception:
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_lifecycle(action="get", status="error")
            except Exception:
                pass
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
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_lifecycle(action="delete", status="ok")
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_lifecycle(action="delete", status="error")
            except Exception:
                pass
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
        result = await session.commit_async()
        try:
            from openviking.metrics.datasources.session import SessionLifecycleDataSource

            SessionLifecycleDataSource.record_lifecycle(
                action="commit", status="ok" if result.get("status") else "error"
            )
            SessionLifecycleDataSource.record_archive(
                status="ok" if result.get("archived") else "skip"
            )
        except Exception:
            pass
        return result

    async def get_commit_task(self, task_id: str, ctx: RequestContext) -> Optional[Dict[str, Any]]:
        """Query background commit task status by task_id for the calling owner."""
        task = get_task_tracker().get(
            task_id,
            owner_account_id=ctx.account_id,
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

        memories = await self._session_compressor.extract_long_term_memories(
            messages=session.messages,
            user=ctx.user,
            session_id=session_id,
            ctx=ctx,
        )
        try:
            from openviking.metrics.datasources.session import SessionLifecycleDataSource

            SessionLifecycleDataSource.record_lifecycle(action="extract", status="ok")
        except Exception:
            pass
        return memories

    @staticmethod
    def _serialize_preview_candidate(candidate: Any) -> Dict[str, Any]:
        """Convert a candidate-memory dataclass into a stable preview payload."""
        category = getattr(candidate, "category", "")
        category_value = getattr(category, "value", category) or ""

        payload = {
            "category": str(category_value),
            "abstract": getattr(candidate, "abstract", "") or "",
            "overview": getattr(candidate, "overview", "") or "",
            "content": getattr(candidate, "content", "") or "",
            "language": getattr(candidate, "language", "") or "",
        }

        if isinstance(candidate, ToolSkillCandidateMemory):
            payload.update(
                {
                    "tool_name": candidate.tool_name,
                    "skill_name": candidate.skill_name,
                    "call_time": candidate.call_time,
                    "success_time": candidate.success_time,
                    "duration_ms": candidate.duration_ms,
                    "prompt_tokens": candidate.prompt_tokens,
                    "completion_tokens": candidate.completion_tokens,
                    "best_for": candidate.best_for,
                    "optimal_params": candidate.optimal_params,
                    "recommended_flow": candidate.recommended_flow,
                    "key_dependencies": candidate.key_dependencies,
                    "common_failures": candidate.common_failures,
                    "recommendation": candidate.recommendation,
                }
            )

        return payload

    async def preview_extract(self, session_id: str, ctx: RequestContext) -> Dict[str, Any]:
        """Preview memory extraction results without persisting any memories."""
        self._ensure_initialized()
        if not self._session_compressor:
            raise NotInitializedError("SessionCompressor")

        session = await self.get(session_id, ctx)
        messages = list(session.messages)
        latest_archive_overview = await session._get_latest_completed_archive_overview()
        archive_summary_preview = ""
        if messages:
            archive_summary_preview = await session._generate_archive_summary_async(
                messages,
                latest_archive_overview=latest_archive_overview,
            )

        candidates = await self._session_compressor.preview_long_term_memories(
            messages=messages,
            user=ctx.user,
            session_id=session_id,
            latest_archive_overview=latest_archive_overview,
        )
        serialized_candidates = [
            self._serialize_preview_candidate(candidate) for candidate in candidates
        ]

        counts = defaultdict(int)
        for candidate in serialized_candidates:
            category = candidate.get("category", "") or "unknown"
            counts[category] += 1
        counts["total"] = len(serialized_candidates)

        return {
            "session_id": session_id,
            "message_count": len(messages),
            "estimated_message_tokens": sum(msg.estimated_tokens for msg in messages),
            "latest_archive_overview": latest_archive_overview,
            "archive_summary_preview": archive_summary_preview,
            "counts_by_category": dict(counts),
            "candidates": serialized_candidates,
        }
