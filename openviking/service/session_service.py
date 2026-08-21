# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Session Service for OpenViking.

Provides session management operations: session, sessions, add_message, commit, delete.
"""

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.core.namespace import canonical_session_uri
from openviking.server.agent_evolution_config import AgentEvolutionConfigProvider
from openviking.server.config import AgentEvolutionConfig, ToolOutputExternalizationConfig
from openviking.server.identity import RequestContext
from openviking.server.user_config import read_user_memory_policy
from openviking.service.session_auto_commit import (
    compute_next_check_at,
    get_idle_timeout_seconds,
    get_keep_recent_count,
    get_message_count_threshold,
    get_min_commit_interval_seconds,
    get_token_threshold,
    has_idle_uncommitted_content,
    has_uncommitted_content,
    is_next_check_due,
)
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
from openviking.session.auto_commit_policy import AutoCommitPolicy
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory_policy import MemoryPolicy
from openviking.storage.viking_fs import VikingFS
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking.utils.tags import normalize_search_tags
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import (
    AlreadyExistsError,
    NotFoundError,
    NotInitializedError,
)
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.memory_config import SessionAutoCommitConfig

logger = get_logger(__name__)

if TYPE_CHECKING:
    from openviking.session.compressor_v3 import SessionCompressorV3
    from openviking.usage_reporter import UsageReporter


class SessionService:
    """Session management service."""

    def __init__(
        self,
        vikingdb: Optional[VikingDBManager] = None,
        viking_fs: Optional[VikingFS] = None,
        session_compressor: Optional["SessionCompressorV3"] = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._session_compressor = session_compressor
        self._tool_output_externalization_config = ToolOutputExternalizationConfig()
        self._agent_evolution_enabled = AgentEvolutionConfig().enabled
        self._agent_evolution_config_provider: Optional[AgentEvolutionConfigProvider] = None
        self._agent_evolution_config_path: Optional[str] = None
        self._default_user_memory_policy: Optional[Dict[str, Any]] = None
        self._configure_agent_evolution_provider()
        self._usage_reporter: Optional["UsageReporter"] = None
        # Server-wide controls remain disabled until configured during app setup.
        self._session_auto_commit_config = SessionAutoCommitConfig()
        # Per-worker in-flight guard so bursts don't spawn duplicate commit
        # tasks. Cross-worker correctness relies on has_running(), not this set.
        self._auto_commit_inflight: set[tuple[str, str, str]] = set()
        self._auto_commit_inflight_lock = asyncio.Lock()
        # Strong refs so spawned tasks aren't GC'd mid-await.
        self._auto_commit_tasks: set[asyncio.Task] = set()

    def set_dependencies(
        self,
        vikingdb: VikingDBManager,
        viking_fs: VikingFS,
        session_compressor: "SessionCompressorV3",
    ) -> None:
        """Set dependencies (for deferred initialization)."""
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs
        self._session_compressor = session_compressor
        self._configure_agent_evolution_provider()

    def set_tool_output_externalization_config(
        self, config: ToolOutputExternalizationConfig
    ) -> None:
        """Set tool output externalization controls for newly created sessions."""
        self._tool_output_externalization_config = config.model_copy(deep=True)

    def set_default_user_memory_policy(self, memory_policy: Optional[Dict[str, Any]]) -> None:
        """Set the server fallback used when a User has no persisted policy."""
        if memory_policy is None:
            self._default_user_memory_policy = None
            return
        policy = MemoryPolicy.from_dict(memory_policy)
        policy.validate_memory_types(set(MemoryTypeRegistry().list_names(include_disabled=False)))
        self._default_user_memory_policy = policy.to_dict()

    def set_agent_evolution_config(self, config: AgentEvolutionConfig) -> None:
        """Set the default used when an account has no persisted override."""
        self._agent_evolution_enabled = config.enabled
        if self._agent_evolution_config_provider is not None:
            self._agent_evolution_config_provider.set_default_enabled(config.enabled)

    def set_agent_evolution_config_path(self, config_path: Optional[str]) -> None:
        """Enable account settings layered over the resolved ov.conf."""
        self._agent_evolution_config_path = config_path
        self._configure_agent_evolution_provider()

    def _configure_agent_evolution_provider(self) -> None:
        if self._viking_fs is None:
            self._agent_evolution_config_provider = None
            return
        self._agent_evolution_config_provider = AgentEvolutionConfigProvider(
            default_enabled=self._agent_evolution_enabled,
            viking_fs=self._viking_fs,
            config_path=self._agent_evolution_config_path,
        )

    async def get_agent_evolution_enabled(self, account_id: str) -> bool:
        """Return the effective Agent Evolution switch for one account."""
        if self._agent_evolution_config_provider is None:
            return self._agent_evolution_enabled
        return await self._agent_evolution_config_provider.is_enabled(account_id)

    def set_usage_reporter(self, usage_reporter: Optional["UsageReporter"]) -> None:
        """Set the usage reporter for newly created sessions."""
        self._usage_reporter = usage_reporter

    def set_session_auto_commit_config(self, config: SessionAutoCommitConfig) -> None:
        """Set server-wide controls for automatic session commits."""
        self._session_auto_commit_config = config.model_copy(deep=True)

    def _ensure_initialized(self) -> None:
        """Ensure all dependencies are initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")

    @property
    def viking_fs(self) -> VikingFS:
        """Expose VikingFS for the idle auto-commit scheduler's AGFS scan."""
        self._ensure_initialized()
        return self._viking_fs

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

    def session(
        self,
        ctx: RequestContext,
        session_id: Optional[str] = None,
        *,
        session_uri: Optional[str] = None,
    ) -> Session:
        """Create a new session or load an existing one.

        Args:
            session_id: Session ID, creates a new session (auto-generated ID) if None

        Returns:
            Session instance
        """
        self._ensure_initialized()
        ctx = replace(ctx, actor_peer_id=None)
        return Session(
            viking_fs=self._viking_fs,
            vikingdb_manager=self._vikingdb,
            session_compressor=self._session_compressor,
            user=ctx.user,
            ctx=ctx,
            session_id=session_id,
            session_uri=session_uri,
            tool_output_externalization_config=self._tool_output_externalization_config,
            agent_evolution_enabled=self._agent_evolution_enabled,
            agent_evolution_enabled_provider=lambda: self.get_agent_evolution_enabled(
                ctx.account_id
            ),
            memory_policy_provider=lambda: self._get_user_memory_policy(ctx),
            usage_reporter=self._usage_reporter,
        )

    async def _get_user_memory_policy(self, ctx: RequestContext) -> Optional[Dict[str, Any]]:
        """Resolve the latest User policy with the configured server fallback."""
        memory_policy = await read_user_memory_policy(self._viking_fs, ctx)
        if memory_policy is not None:
            return memory_policy
        return self._default_user_memory_policy

    async def create(
        self,
        ctx: RequestContext,
        session_id: Optional[str] = None,
        memory_policy: Optional[Dict[str, Any]] = None,
        auto_commit_policy: Optional[Dict[str, Any]] = None,
        update_auto_commit_policy: bool = False,
        event_tags: Optional[List[str]] = None,
    ) -> Session:
        """Create a session and persist its root path.

        Args:
            ctx: Request context
            session_id: Optional session ID. If provided, creates a session with the given ID.
                       If None, creates a new session with auto-generated ID.
            memory_policy: Optional default extraction policy for future commits.
            auto_commit_policy: Optional automatic-commit policy overrides. Missing
                fields fall back to the recommended defaults.
            event_tags: Optional default custom scalar tags for event memories
                (``config.memory_extraction_config.events.tags``). Normalized to
                canonical ``key=value`` form; ``None`` leaves no session default.

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
            if memory_policy is not None:
                policy = MemoryPolicy.from_dict(memory_policy)
                policy.validate_memory_types(
                    set(MemoryTypeRegistry().list_names(include_disabled=False))
                )
                session.meta.memory_policy = policy.to_dict()
            # Auto-commit is enabled when the caller supplies a policy, or when
            # the server default turns it on. Absent both, it stays disabled.
            if update_auto_commit_policy and auto_commit_policy is None:
                session.meta.auto_commit_policy = None
            elif auto_commit_policy is not None or self._session_auto_commit_config.default_enabled:
                session.meta.auto_commit_policy = AutoCommitPolicy.from_dict(
                    auto_commit_policy
                ).to_dict()
            else:
                session.meta.auto_commit_policy = None
            if event_tags is not None:
                session.meta.event_search_tags = normalize_search_tags(
                    event_tags,
                    discard_invalid=True,
                )
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
                if self._session_auto_commit_config.default_enabled:
                    session.meta.auto_commit_policy = AutoCommitPolicy.from_dict(None).to_dict()
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
        session_base_uri = canonical_session_uri(ctx)
        sessions_by_id: Dict[str, Dict[str, Any]] = {}

        try:
            entries = await self._viking_fs.ls(
                session_base_uri,
                sort_by="mtime",
                sort_order="desc",
                ctx=ctx,
            )
            for entry in entries:
                name = entry.get("name", "")
                if name in [".", ".."]:
                    continue
                sessions_by_id[name] = {
                    "session_id": name,
                    "uri": f"{session_base_uri}/{name}",
                    "is_dir": entry.get("isDir", False),
                    "mod_time": entry.get("modTime", ""),
                }
        except Exception:
            logger.debug("Failed to list sessions", exc_info=True)

        return list(sessions_by_id.values())

    async def delete(self, session_id: str, ctx: RequestContext) -> bool:
        """Delete a session.

        Args:
            session_id: Session ID to delete

        Returns:
            True if deleted successfully
        """
        self._ensure_initialized()

        session_uri = canonical_session_uri(ctx, session_id)
        session = await self.get(session_id, ctx)
        if not await session.exists():
            self._record_lifecycle_metric("delete", "error")
            raise NotFoundError(session_id, "session")

        await self._viking_fs.rm(session_uri, recursive=True, ctx=ctx)
        logger.info(f"Deleted session: {session_id}")
        self._record_lifecycle_metric("delete", "ok")
        return True

    async def commit(
        self,
        session_id: str,
        ctx: RequestContext,
        keep_recent_count: int = 0,
        *,
        retention_mode: Optional[str] = None,
        keep_recent_turn_count: Optional[int] = None,
        retained_message_token_budget: Optional[int] = None,
        min_raw_tail_steps: Optional[int] = None,
        event_tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Commit a session (archive messages and extract memories).

        Delegates to commit_async() for true non-blocking behavior.

        Args:
            session_id: Session ID to commit
            keep_recent_count: See :meth:`commit_async`.

        Returns:
            Commit result
        """
        return await self.commit_async(
            session_id,
            ctx,
            keep_recent_count=keep_recent_count,
            retention_mode=retention_mode,
            keep_recent_turn_count=keep_recent_turn_count,
            retained_message_token_budget=retained_message_token_budget,
            min_raw_tail_steps=min_raw_tail_steps,
            event_tags=event_tags,
        )

    async def commit_async(
        self,
        session_id: str,
        ctx: RequestContext,
        keep_recent_count: int = 0,
        *,
        retention_mode: Optional[str] = None,
        keep_recent_turn_count: Optional[int] = None,
        retained_message_token_budget: Optional[int] = None,
        min_raw_tail_steps: Optional[int] = None,
        event_tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Async commit a session.

        Phase 1 (archive) always runs inline.  Phase 2 (memory extraction)
        runs in a background task, returning a task_id for polling.

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
        commit_kwargs: Dict[str, Any] = {"keep_recent_count": keep_recent_count}
        optional_retention = {
            "retention_mode": retention_mode,
            "keep_recent_turn_count": keep_recent_turn_count,
            "retained_message_token_budget": retained_message_token_budget,
            "min_raw_tail_steps": min_raw_tail_steps,
        }
        commit_kwargs.update(
            {key: value for key, value in optional_retention.items() if value is not None}
        )
        if event_tags is not None:
            commit_kwargs["event_tags"] = event_tags
        result = await session.commit_async(**commit_kwargs)
        self._record_lifecycle_metric("commit", "ok" if result.get("status") else "error")
        self._record_archive_metric("ok" if result.get("archived") else "skip")
        return result

    async def get_commit_task(self, task_id: str, ctx: RequestContext) -> Optional[Dict[str, Any]]:
        """Query background commit task status by task_id for the calling owner."""
        task = await get_task_tracker().get(
            task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
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
            raise NotInitializedError("SessionCompressorV3")

        session = await self.get(session_id, ctx)
        archive_uri = f"{session.uri}/manual_extract"

        memories = await self._session_compressor.extract_long_term_memories(
            messages=session.messages,
            user=ctx.user,
            session_id=session_id,
            ctx=ctx,
            archive_uri=archive_uri,
            agent_evolution_enabled=await self.get_agent_evolution_enabled(ctx.account_id),
        )
        self._record_lifecycle_metric("extract", "ok")
        return memories

    @staticmethod
    def effective_auto_commit_policy(session: Session) -> Optional[Dict[str, Any]]:
        """Return the resolved auto-commit policy (defaults filled), or None when disabled."""
        if session.meta.auto_commit_policy is None:
            return None
        return AutoCommitPolicy.from_dict(session.meta.auto_commit_policy).to_dict()

    @staticmethod
    def effective_memory_extraction_config(session: Session) -> Dict[str, Any]:
        """Return the caller-facing memory extraction config."""
        return {
            "events": {"tags": list(session.meta.event_search_tags or [])},
        }

    async def update_config(
        self,
        session_id: str,
        ctx: RequestContext,
        *,
        event_tags: Optional[List[str]] = None,
        auto_commit_policy: Optional[Dict[str, Any]] = None,
        update_auto_commit_policy: bool = False,
    ) -> Session:
        """Update the mutable parts of a session's config.

        Only fields explicitly provided are changed. ``event_tags`` uses
        ``None`` for no change and ``[]`` to clear. ``auto_commit_policy`` is
        merged field-by-field when ``update_auto_commit_policy`` is true; a
        policy value of ``None`` disables automatic commits.
        """
        self._ensure_initialized()
        session = await self.get(session_id, ctx)
        normalized_event_tags = (
            normalize_search_tags(event_tags, discard_invalid=True)
            if event_tags is not None
            else None
        )
        if normalized_event_tags is not None or update_auto_commit_policy:
            await session.update_config(
                event_search_tags=normalized_event_tags,
                auto_commit_policy=auto_commit_policy,
                update_auto_commit_policy=update_auto_commit_policy,
            )
        return session

    async def maybe_schedule_auto_commit(
        self,
        session_id: str,
        ctx: RequestContext,
        *,
        reason_hint: str,
        session: Optional[Session] = None,
    ) -> bool:
        """Best-effort automatic commit scheduler entrypoint.

        Returns True when a commit task was spawned. Misses are acceptable: the
        next message write or the next idle scan re-triggers naturally, so there
        is no recheck/polling machinery here. Scheduling never propagates errors
        to the caller's message write or the idle scan batch.
        """
        claim = (ctx.account_id, ctx.user.user_id, session_id)
        try:
            if session is None:
                session = await self.get(session_id, ctx, auto_create=False)
            policy = session.meta.auto_commit_policy
            if not self._should_run_auto_commit(session, policy, reason_hint):
                return False

            async with self._auto_commit_inflight_lock:
                if claim in self._auto_commit_inflight:
                    return False
                if await get_task_tracker().has_running(
                    "session_commit",
                    session_id,
                    account_id=ctx.account_id,
                    user_id=ctx.user.user_id,
                ):
                    return False
                self._auto_commit_inflight.add(claim)
        except Exception:
            logger.debug(
                "Skipped auto-commit scheduling for %s", session_id, exc_info=True
            )
            return False

        task = asyncio.create_task(self.run_auto_commit(session_id, ctx, reason=reason_hint))
        self._auto_commit_tasks.add(task)
        task.add_done_callback(self._auto_commit_tasks.discard)
        return True

    async def run_auto_commit(self, session_id: str, ctx: RequestContext, *, reason: str) -> None:
        """Run one best-effort automatic commit and release the in-flight claim."""
        claim = (ctx.account_id, ctx.user.user_id, session_id)
        try:
            tracker = get_task_tracker()
            if await tracker.has_running(
                "session_commit",
                session_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            ):
                return

            # Reload so a stale in-memory session can't drive the decision;
            # commit_async re-reads again under its own path lock.
            session = await self.get(session_id, ctx, auto_create=False)
            policy = session.meta.auto_commit_policy
            if not self._should_run_auto_commit(session, policy, reason):
                return

            # Idle timeout commits the whole backlog but must not persist a
            # keep_recent_count of 0, which would wipe the stored preference.
            if reason == "idle_timeout":
                await session.commit_async(
                    keep_recent_count=0,
                    persist_keep_recent_count=False,
                    record_auto_commit_success=True,
                )
            else:
                await session.commit_async(
                    keep_recent_count=get_keep_recent_count(policy),
                    record_auto_commit_success=True,
                )
        except Exception as exc:
            logger.warning("Automatic session commit failed for %s: %s", session_id, exc)
        finally:
            async with self._auto_commit_inflight_lock:
                self._auto_commit_inflight.discard(claim)

    @staticmethod
    def _has_uncommitted_content(session: Session) -> bool:
        return has_uncommitted_content(session.meta.to_dict())

    def _within_min_commit_interval(self, session: Session, policy: Any) -> bool:
        """Return True when the throttle window has not yet elapsed."""
        interval = get_min_commit_interval_seconds(policy)
        if interval <= 0:
            return False
        last_auto_commit_at = session.meta.last_auto_commit_at
        if not last_auto_commit_at:
            return False
        try:
            last_dt = parse_iso_datetime(last_auto_commit_at)
        except (TypeError, ValueError):
            return False
        now = datetime.now()
        if last_dt.tzinfo is not None:
            if now.tzinfo is None:
                now = datetime.fromtimestamp(now.timestamp(), tz=last_dt.tzinfo)
            else:
                now = now.astimezone(last_dt.tzinfo)
        elif now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        return (now - last_dt).total_seconds() < interval

    def _should_run_auto_commit(self, session: Session, policy: Any, reason: str) -> bool:
        """Validate the current session state still satisfies the trigger reason."""
        if policy is None:
            return False
        if reason == "message_write":
            if not self._has_uncommitted_content(session):
                return False
            if self._within_min_commit_interval(session, policy):
                return False
            return self._message_write_threshold_exceeded(session, policy)

        if reason == "idle_timeout":
            if not self._session_auto_commit_config.idle_enabled:
                return False
            idle_timeout = get_idle_timeout_seconds(policy)
            if idle_timeout is None or not has_idle_uncommitted_content(session.meta.to_dict()):
                return False
            if self._within_min_commit_interval(session, policy):
                return False
            next_check_at = compute_next_check_at(session.meta.last_message_at, idle_timeout)
            if not next_check_at:
                return False
            return is_next_check_due(next_check_at, datetime.now()) is True

        return False

    @staticmethod
    def _message_write_threshold_exceeded(session: Session, policy: Any) -> bool:
        try:
            pending_tokens = int(session.meta.pending_tokens or 0)
            message_count = int(session.meta.message_count or 0)
        except (TypeError, ValueError):
            return False
        token_threshold = get_token_threshold(policy)
        if token_threshold is not None and pending_tokens > token_threshold:
            return True
        message_threshold = get_message_count_threshold(policy)
        if message_threshold is not None and message_count > message_threshold:
            return True
        return False
