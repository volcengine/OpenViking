# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session management for OpenViking.

Session as Context: Sessions integrated into L0/L1/L2 system.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from openviking.core.namespace import canonical_session_uri
from openviking.message import Message, Part
from openviking.message.part import ContextPart, TextPart, ToolPart
from openviking.server.identity import RequestContext, Role
from openviking.telemetry import get_current_telemetry, tracer
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger, run_async
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.session.compressor import SessionCompressor
    from openviking.storage import VikingDBManager
    from openviking.storage.viking_fs import VikingFS

logger = get_logger(__name__)

_ARCHIVE_WAIT_POLL_SECONDS = 0.1
_PHASE2_QUEUE_WAIT_TIMEOUT_SECONDS = 1800.0


def _wm_debug(msg: str) -> None:
    """Log a WM v2 debug message via the standard logger."""
    logger.debug("wm_v2: %s", msg)


# =====================================================================
# Working Memory v2
# ---------------------------------------------------------------------
# Phase 2 of a commit generates / updates a structured 7-section Working
# Memory document stored at archive_NNN/.overview.md.
#
# First commit: call `compression.ov_wm_v2` with a plain completion.
# Subsequent commits: call `compression.ov_wm_v2_update` with the
# `update_working_memory` tool to get a per-section decision, then let
# the server do section-level merge against the previous WM.
# =====================================================================

WM_SEVEN_SECTIONS: List[str] = [
    "Session Title",
    "Current State",
    "Task & Goals",
    "Key Facts & Decisions",
    "Files & Context",
    "Errors & Corrections",
    "Open Issues",
]

_WM_SECTION_OP_SCHEMA: Dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "required": ["op"],
            "additionalProperties": False,
            "properties": {"op": {"type": "string", "enum": ["KEEP"]}},
        },
        {
            "type": "object",
            "required": ["op", "content"],
            "additionalProperties": False,
            "properties": {
                "op": {"type": "string", "enum": ["UPDATE"]},
                "content": {
                    "type": "string",
                    "description": (
                        "FULL replacement content for this section, markdown, "
                        "WITHOUT the '## <section>' header line."
                    ),
                },
            },
        },
        {
            "type": "object",
            "required": ["op", "items"],
            "additionalProperties": False,
            "properties": {
                "op": {"type": "string", "enum": ["APPEND"]},
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "New bullet-style items to append under the existing "
                        "section body. Omit heading / bullet markers; the "
                        "server renders each item as '- <item>'."
                    ),
                },
            },
        },
    ]
}

WM_UPDATE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "update_working_memory",
        "description": (
            "Emit a per-section decision (KEEP / UPDATE / APPEND) for the "
            "7-section Working Memory document."
        ),
        "parameters": {
            "type": "object",
            "required": ["sections"],
            "additionalProperties": False,
            "properties": {
                "sections": {
                    "type": "object",
                    "required": list(WM_SEVEN_SECTIONS),
                    "additionalProperties": False,
                    "properties": {
                        name: _WM_SECTION_OP_SCHEMA for name in WM_SEVEN_SECTIONS
                    },
                }
            },
        },
    },
}


@dataclass
class SessionCompression:
    """Session compression information."""

    summary: str = ""
    original_count: int = 0
    compressed_count: int = 0
    compression_index: int = 0


@dataclass
class SessionStats:
    """Session statistics information."""

    total_turns: int = 0
    total_tokens: int = 0
    compression_count: int = 0
    contexts_used: int = 0
    skills_used: int = 0
    memories_extracted: int = 0


@dataclass
class SessionMeta:
    """Session metadata persisted in .meta.json."""

    session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    created_by_user_id: str = ""
    participant_user_ids: List[str] = field(default_factory=list)
    participant_agent_ids: List[str] = field(default_factory=list)
    message_count: int = 0
    total_message_count: Optional[int] = 0
    commit_count: int = 0
    memories_extracted: Dict[str, int] = field(
        default_factory=lambda: {
            "profile": 0,
            "preferences": 0,
            "entities": 0,
            "events": 0,
            "cases": 0,
            "patterns": 0,
            "tools": 0,
            "skills": 0,
            "total": 0,
        }
    )
    last_commit_at: str = ""
    llm_token_usage: Dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    )
    embedding_token_usage: Dict[str, int] = field(
        default_factory=lambda: {
            "total_tokens": 0,
        }
    )
    # Working-Memory v2: token accounting for sliding window + keep window.
    # pending_tokens is the cumulative estimated_tokens of messages that fall
    # OUTSIDE the recent-keep window and will be archived on the next commit.
    # Maintained O(1) inside add_message(); rebuilt from messages on load.
    pending_tokens: int = 0
    # keep_recent_count is the last value passed from the plugin through
    # POST /sessions/{id}/commit body. It is remembered so subsequent
    # add_message calls can maintain pending_tokens consistently across
    # process restarts.
    keep_recent_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by_user_id": self.created_by_user_id,
            "participant_user_ids": list(self.participant_user_ids),
            "participant_agent_ids": list(self.participant_agent_ids),
            "message_count": self.message_count,
            "commit_count": self.commit_count,
            "memories_extracted": dict(self.memories_extracted),
            "last_commit_at": self.last_commit_at,
            "llm_token_usage": dict(self.llm_token_usage),
            "embedding_token_usage": dict(self.embedding_token_usage),
            "pending_tokens": self.pending_tokens,
            "keep_recent_count": self.keep_recent_count,
        }
        if self.total_message_count is not None:
            data["total_message_count"] = self.total_message_count
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMeta":
        llm_token_usage = data.get("llm_token_usage", {})
        embedding_token_usage = data.get("embedding_token_usage", {})
        memories = data.get("memories_extracted", {})

        return cls(
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            created_by_user_id=data.get("created_by_user_id", ""),
            participant_user_ids=list(data.get("participant_user_ids", [])),
            participant_agent_ids=list(data.get("participant_agent_ids", [])),
            message_count=data.get("message_count", 0),
            total_message_count=data.get("total_message_count"),
            commit_count=data.get("commit_count", 0),
            memories_extracted={
                "profile": memories.get("profile", 0),
                "preferences": memories.get("preferences", 0),
                "entities": memories.get("entities", 0),
                "events": memories.get("events", 0),
                "cases": memories.get("cases", 0),
                "patterns": memories.get("patterns", 0),
                "tools": memories.get("tools", 0),
                "skills": memories.get("skills", 0),
                "total": memories.get("total", 0),
            },
            last_commit_at=data.get("last_commit_at", ""),
            llm_token_usage={
                "prompt_tokens": llm_token_usage.get("prompt_tokens", 0),
                "completion_tokens": llm_token_usage.get("completion_tokens", 0),
                "total_tokens": llm_token_usage.get("total_tokens", 0),
            },
            embedding_token_usage={
                "total_tokens": embedding_token_usage.get("total_tokens", 0),
            },
            pending_tokens=max(0, int(data.get("pending_tokens", 0) or 0)),
            keep_recent_count=max(0, int(data.get("keep_recent_count", 0) or 0)),
        )


@dataclass
class Usage:
    """Usage record."""

    uri: str
    type: str  # "context" | "skill"
    contribution: float = 0.0
    input: str = ""
    output: str = ""
    success: bool = True
    timestamp: str = field(default_factory=get_current_timestamp)


class Session:
    """Session management class - Message = role + parts."""

    def __init__(
        self,
        viking_fs: "VikingFS",
        vikingdb_manager: Optional["VikingDBManager"] = None,
        session_compressor: Optional["SessionCompressor"] = None,
        user: Optional["UserIdentifier"] = None,
        ctx: Optional[RequestContext] = None,
        session_id: Optional[str] = None,
        auto_commit_threshold: int = 8000,
    ):
        self._viking_fs = viking_fs
        self._vikingdb_manager = vikingdb_manager
        self._session_compressor = session_compressor
        self.user = user or UserIdentifier.the_default_user()
        self.ctx = ctx or RequestContext(user=self.user, role=Role.ROOT)
        self.session_id = session_id or str(uuid4())
        self.created_at = int(datetime.now(timezone.utc).timestamp() * 1000)
        self._auto_commit_threshold = auto_commit_threshold
        self._session_uri = canonical_session_uri(self.session_id)

        self._messages: List[Message] = []
        self._usage_records: List[Usage] = []
        self._compression: SessionCompression = SessionCompression()
        self._stats: SessionStats = SessionStats()
        self._meta = SessionMeta(
            session_id=self.session_id,
            created_at=get_current_timestamp(),
            created_by_user_id=self.ctx.user.user_id,
            participant_user_ids=[self.ctx.user.user_id],
        )
        self._loaded = False

        logger.info(f"Session created: {self.session_id} for user {self.user}")

    async def load(self):
        """Load session data from storage."""
        if self._loaded:
            return

        try:
            content = await self._viking_fs.read_file(
                f"{self._session_uri}/messages.jsonl", ctx=self.ctx
            )
            self._messages = [
                Message.from_dict(json.loads(line))
                for line in content.strip().split("\n")
                if line.strip()
            ]
            logger.info(f"Session loaded: {self.session_id} ({len(self._messages)} messages)")
        except (FileNotFoundError, Exception):
            logger.debug(f"Session {self.session_id} not found, starting fresh")

        # Restore compression_index (scan history directory)
        try:
            history_items = await self._viking_fs.ls(f"{self._session_uri}/history", ctx=self.ctx)
            archives = [
                item["name"] for item in history_items if item["name"].startswith("archive_")
            ]
            if archives:
                max_index = max(int(a.split("_")[1]) for a in archives)
                self._compression.compression_index = max_index
                self._stats.compression_count = len(archives)
                logger.debug(f"Restored compression_index: {max_index}")
        except Exception:
            pass

        # Load .meta.json
        try:
            meta_content = await self._viking_fs.read_file(
                f"{self._session_uri}/.meta.json", ctx=self.ctx
            )
            self._meta = SessionMeta.from_dict(json.loads(meta_content))
        except Exception:
            # Old session without meta — derive from existing data
            self._meta.message_count = len(self._messages)
            self._meta.commit_count = self._compression.compression_index
            self._meta.total_message_count = None

        if not self._meta.created_by_user_id:
            self._meta.created_by_user_id = self.ctx.user.user_id
        if not self._meta.participant_user_ids:
            self._meta.participant_user_ids = [self._meta.created_by_user_id]
        for message in self._messages:
            self._record_participant(message)

        # WM v2: always rebuild pending_tokens from current messages so the
        # counter stays consistent across restarts and is also backfilled for
        # legacy sessions whose .meta.json predates these fields. O(n) once,
        # subsequent add_message() maintains it in O(1).
        self._rebuild_pending_tokens()

        self._loaded = True

    def _rebuild_pending_tokens(self) -> None:
        """Recompute ``pending_tokens`` from the current message list.

        Used on load and as a safety net after rollbacks. Respects the
        currently remembered ``keep_recent_count`` from meta.
        """
        keep = max(0, int(self._meta.keep_recent_count or 0))
        total = len(self._messages)
        if keep <= 0:
            self._meta.pending_tokens = sum(
                int(m.estimated_tokens or 0) for m in self._messages
            )
        elif total > keep:
            self._meta.pending_tokens = sum(
                int(m.estimated_tokens or 0) for m in self._messages[: total - keep]
            )
        else:
            self._meta.pending_tokens = 0
        self._meta.pending_tokens = max(0, self._meta.pending_tokens)

    async def exists(self) -> bool:
        """Check whether this session already exists in storage."""
        try:
            await self._viking_fs.stat(self._session_uri, ctx=self.ctx)
            return True
        except Exception:
            return False

    async def ensure_exists(self) -> None:
        """Materialize session root and messages file if missing."""
        if await self.exists():
            return
        await self._viking_fs.mkdir(self._session_uri, exist_ok=True, ctx=self.ctx)
        await self._viking_fs.write_file(f"{self._session_uri}/messages.jsonl", "", ctx=self.ctx)
        await self._save_meta()

    async def _save_meta(self) -> None:
        """Persist .meta.json to storage."""
        if not self._viking_fs:
            return
        self._meta.updated_at = get_current_timestamp()
        await self._viking_fs.write_file(
            uri=f"{self._session_uri}/.meta.json",
            content=json.dumps(self._meta.to_dict(), ensure_ascii=False),
            ctx=self.ctx,
        )

    def _save_meta_sync(self) -> None:
        """Sync wrapper for _save_meta()."""
        if not self._viking_fs:
            return
        run_async(self._save_meta())

    @property
    def messages(self) -> List[Message]:
        """Get message list."""
        return self._messages

    @property
    def meta(self) -> SessionMeta:
        """Get session metadata."""
        return self._meta

    # ============= Core methods =============

    def used(
        self,
        contexts: Optional[List[str]] = None,
        skill: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record actually used contexts and skills."""
        if contexts:
            for uri in contexts:
                usage = Usage(uri=uri, type="context")
                self._usage_records.append(usage)
                self._stats.contexts_used += 1
                logger.debug(f"Tracked context usage: {uri}")
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_contexts_used(
                    action="context", delta=len(contexts)
                )
            except Exception:
                pass

        if skill:
            usage = Usage(
                uri=skill.get("uri", ""),
                type="skill",
                input=skill.get("input", ""),
                output=skill.get("output", ""),
                success=skill.get("success", True),
            )
            self._usage_records.append(usage)
            self._stats.skills_used += 1
            logger.debug(f"Tracked skill usage: {skill.get('uri')}")
            try:
                from openviking.metrics.datasources.session import SessionLifecycleDataSource

                SessionLifecycleDataSource.record_contexts_used(action="skill", delta=1)
            except Exception:
                pass

    def add_message(
        self,
        role: str,
        parts: List[Part],
        role_id: Optional[str] = None,
        created_at: str = None,
    ) -> Message:
        """Add a message."""
        msg = Message(
            id=f"msg_{uuid4().hex}",
            role=role,
            parts=parts,
            role_id=role_id,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
        )
        self._messages.append(msg)
        self._record_participant(msg)

        # Update statistics
        if role == "user":
            self._stats.total_turns += 1
        msg_tokens = int(msg.estimated_tokens or 0)
        self._stats.total_tokens += msg_tokens

        # WM v2: maintain pending_tokens via sliding window.
        # keep_recent_count == 0 (never-committed or compact path that chose 0):
        #   every new message contributes to pending.
        # keep_recent_count > 0:
        #   only the message pushed OUT of the tail-keep window contributes.
        keep = int(self._meta.keep_recent_count or 0)
        if keep <= 0:
            self._meta.pending_tokens += msg_tokens
        elif len(self._messages) > keep:
            pushed_out = self._messages[-(keep + 1)]
            self._meta.pending_tokens += int(pushed_out.estimated_tokens or 0)

        self._append_to_jsonl(msg)

        self._meta.message_count = len(self._messages)
        if self._meta.total_message_count is not None:
            self._meta.total_message_count += 1
        self._save_meta_sync()
        return msg

    def _record_participant(self, msg: Message) -> None:
        if msg.role == "user" and msg.role_id:
            if msg.role_id not in self._meta.participant_user_ids:
                self._meta.participant_user_ids.append(msg.role_id)
        if msg.role == "assistant" and msg.role_id:
            if msg.role_id not in self._meta.participant_agent_ids:
                self._meta.participant_agent_ids.append(msg.role_id)

    def update_tool_part(
        self,
        message_id: str,
        tool_id: str,
        output: str,
        status: str = "completed",
    ) -> None:
        """Update tool status."""
        msg = next((m for m in self._messages if m.id == message_id), None)
        if not msg:
            return

        tool_part = msg.find_tool_part(tool_id)
        if not tool_part:
            return

        tool_part.tool_output = output
        tool_part.tool_status = status

        self._save_tool_result(tool_id, msg, output, status)
        self._update_message_in_jsonl()
        self._rebuild_pending_tokens()

    def commit(self, keep_recent_count: int = 0) -> Dict[str, Any]:
        """Sync wrapper for commit_async()."""
        return run_async(self.commit_async(keep_recent_count=keep_recent_count))

    @tracer("session.commit")
    async def commit_async(self, keep_recent_count: int = 0) -> Dict[str, Any]:
        """Async commit session: archive immediately, extract memories in background.

        Phase 1 (Archive prep, PathLock-protected): Split messages into
        archive/retain parts, write the retained tail back to messages.jsonl,
        then persist the archive. Uses a distributed filesystem lock (PathLock)
        so this works across workers and processes.
        Phase 2 (Memory extraction): Always runs in background via
        asyncio.create_task().

        Args:
            keep_recent_count: Number of most-recent messages to keep in the
                live session after commit. ``0`` (default) preserves the old
                behavior of archiving everything. The plugin's afterTurn path
                typically passes its configured value (default 10); the compact
                path passes ``0``.

        Returns a task_id for tracking Phase 2 progress.
        """
        from openviking.service.task_tracker import get_task_tracker
        from openviking.storage.transaction import LockContext, get_lock_manager
        from openviking_cli.exceptions import FailedPreconditionError

        trace_id = tracer.get_trace_id()
        keep_recent_count = max(0, int(keep_recent_count or 0))
        logger.info(
            f"[TRACER] session_commit started, trace_id={trace_id}, "
            f"keep_recent_count={keep_recent_count}"
        )

        # ===== Phase 1: Snapshot + clear (PathLock-protected) =====
        # Fast pre-check: skip lock entirely if no messages (common case avoids
        # unnecessary filesystem lock acquisition).
        if not self._messages:
            self._meta.pending_tokens = 0
            self._meta.keep_recent_count = keep_recent_count
            await self._save_meta()
            get_current_telemetry().set("memory.extracted", 0)
            return {
                "session_id": self.session_id,
                "status": "accepted",
                "task_id": None,
                "archive_uri": None,
                "archived": False,
                "trace_id": trace_id,
            }

        blocking_archive = await self._get_blocking_failed_archive_ref()
        if blocking_archive:
            raise FailedPreconditionError(
                f"Session {self.session_id} has unresolved failed archive "
                f"{blocking_archive['archive_id']}; fix it before committing again.",
                details={"archive_id": blocking_archive["archive_id"]},
            )

        # Use filesystem-based distributed lock so this works across workers/processes.
        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        async with LockContext(get_lock_manager(), [session_path], lock_mode="point"):
            # Authoritative check under lock: handles the race where two concurrent
            # callers both passed the pre-check but only the first should archive.
            if not self._messages:
                self._meta.pending_tokens = 0
                self._meta.keep_recent_count = keep_recent_count
                await self._save_meta()
                get_current_telemetry().set("memory.extracted", 0)
                return {
                    "session_id": self.session_id,
                    "status": "accepted",
                    "task_id": None,
                    "archive_uri": None,
                    "archived": False,
                    "trace_id": trace_id,
                }

            # WM v2 boundary: if all live messages already fit inside the keep
            # window, there is nothing to archive — just remember the new
            # keep_recent_count and reset pending_tokens so the next
            # add_message uses the sliding-window path.
            total = len(self._messages)
            if keep_recent_count > 0 and total <= keep_recent_count:
                self._meta.pending_tokens = 0
                self._meta.keep_recent_count = keep_recent_count
                self._meta.message_count = total
                await self._save_meta()
                get_current_telemetry().set("memory.extracted", 0)
                return {
                    "session_id": self.session_id,
                    "status": "accepted",
                    "task_id": None,
                    "archive_uri": None,
                    "archived": False,
                    "reason": "all_within_keep_window",
                    "trace_id": trace_id,
                }

            self._compression.compression_index += 1
            if keep_recent_count > 0:
                split_idx = total - keep_recent_count
                messages_to_archive = self._messages[:split_idx]
                self._messages = self._messages[split_idx:]
            else:
                messages_to_archive = self._messages.copy()
                self._messages = []

            try:
                # Persist the retained tail (may be empty when keep=0). This
                # replaces messages.jsonl under the lock, consistent with the
                # previous behavior of writing empty messages on full clear.
                await self._write_to_agfs_async(messages=self._messages)
            except Exception as e:
                # Rollback: restore messages so they aren't lost
                logger.error(f"[commit] Failed to write messages.jsonl: {e}")
                self._messages = list(messages_to_archive) + list(self._messages)
                self._compression.compression_index -= 1
                raise
        # Lock released — live session now contains only the retained tail.

        # ===== Phase 1 continued: Write raw archive (no LLM calls, no lock needed) =====
        archive_uri = (
            f"{self._session_uri}/history/archive_{self._compression.compression_index:03d}"
        )
        if self._viking_fs:
            lines = [m.to_jsonl() for m in messages_to_archive]
            await self._viking_fs.write_file(
                uri=f"{archive_uri}/messages.jsonl",
                content="\n".join(lines) + "\n",
                ctx=self.ctx,
            )

        # WM v2: live session is now the retained tail; pending_tokens resets
        # because anything that was pending has been archived.
        self._meta.message_count = len(self._messages)
        self._meta.pending_tokens = 0
        self._meta.keep_recent_count = keep_recent_count
        await self._save_meta()

        self._compression.original_count += len(messages_to_archive)
        logger.info(
            f"Archived: {len(messages_to_archive)} messages → "
            f"history/archive_{self._compression.compression_index:03d}/"
        )

        # Snapshot mutable state for Phase 2
        usage_snapshot = self._usage_records.copy()

        # Create TaskRecord for tracking Phase 2
        tracker = get_task_tracker()
        task = tracker.create(
            "session_commit",
            resource_id=self.session_id,
            owner_account_id=self.ctx.account_id,
            owner_user_id=self.ctx.user.user_id,
        )

        asyncio.create_task(
            self._run_memory_extraction(
                task_id=task.task_id,
                archive_uri=archive_uri,
                messages=messages_to_archive,
                usage_records=usage_snapshot,
                first_message_id=messages_to_archive[0].id if messages_to_archive else "",
                last_message_id=messages_to_archive[-1].id if messages_to_archive else "",
            )
        )

        return {
            "session_id": self.session_id,
            "status": "accepted",
            "task_id": task.task_id,
            "archive_uri": archive_uri,
            "archived": True,
            "trace_id": trace_id,
        }

    async def _run_memory_extraction(
        self,
        task_id: str,
        archive_uri: str,
        messages: List[Message],
        usage_records: List["Usage"],
        first_message_id: str,
        last_message_id: str,
    ) -> None:
        """Phase 2: Extract memories, write relations, enqueue — runs in background."""
        import uuid

        from openviking.service.task_tracker import get_task_tracker
        from openviking.storage.transaction import get_lock_manager
        from openviking.telemetry import OperationTelemetry, bind_telemetry
        from openviking.telemetry.registry import register_telemetry, unregister_telemetry

        tracker = get_task_tracker()
        request_wait_tracker = get_request_wait_tracker()

        memories_extracted: Dict[str, int] = {}
        active_count_updated = 0
        telemetry = OperationTelemetry(operation="session_commit_phase2", enabled=True)
        archive_index = self._archive_index_from_uri(archive_uri)
        redo_task_id: Optional[str] = None

        try:
            if not await self._wait_for_previous_archive_done(archive_index):
                await self._write_failed_marker(
                    archive_uri,
                    stage="waiting_previous_done",
                    error=(
                        f"Previous archive archive_{archive_index - 1:03d} failed; "
                        "this archive cannot proceed"
                    ),
                    blocked_by=f"archive_{archive_index - 1:03d}",
                )
                tracker.fail(
                    task_id,
                    f"Previous archive archive_{archive_index - 1:03d} failed; "
                    "cannot continue session commit",
                )
                return

            tracker.start(task_id)
            request_wait_tracker.register_request(telemetry.telemetry_id)
            register_telemetry(telemetry)
            try:
                with bind_telemetry(telemetry):
                    # redo-log protection
                    redo_task_id = str(uuid.uuid4())
                    redo_log = get_lock_manager().redo_log
                    redo_log.write_pending(
                        redo_task_id,
                        {
                            "archive_uri": archive_uri,
                            "session_uri": self._session_uri,
                            "account_id": self.ctx.account_id,
                            "user_id": self.ctx.user.user_id,
                            "agent_id": self.ctx.user.agent_id,
                            "role": self.ctx.role.value,
                        },
                    )

                    latest_archive_overview = await self._get_latest_completed_archive_overview(
                        exclude_archive_uri=archive_uri
                    )

                    # Generate summary and write L0/L1 to archive
                    summary = await self._generate_archive_summary_async(
                        messages,
                        latest_archive_overview=latest_archive_overview,
                    )
                    if self._viking_fs and summary:
                        abstract = self._extract_abstract_from_summary(summary)
                        await self._viking_fs.write_file(
                            uri=f"{archive_uri}/.abstract.md",
                            content=abstract,
                            ctx=self.ctx,
                        )
                        await self._viking_fs.write_file(
                            uri=f"{archive_uri}/.overview.md",
                            content=summary,
                            ctx=self.ctx,
                        )
                        await self._viking_fs.write_file(
                            uri=f"{archive_uri}/.meta.json",
                            content=json.dumps(
                                {
                                    "overview_tokens": -(-len(summary) // 4),
                                    "abstract_tokens": -(-len(abstract) // 4),
                                }
                            ),
                            ctx=self.ctx,
                        )

                    # Memory extraction
                    if self._session_compressor:
                        ov_config = get_openviking_config()
                        if not ov_config.memory.extraction_enabled:
                            logger.info(
                                "Memory extraction is disabled by config (memory.extraction_enabled=false)"
                            )
                        else:
                            logger.info(
                                f"Starting memory extraction from {len(messages)} archived messages"
                            )
                            extracted = await self._session_compressor.extract_long_term_memories(
                                messages=messages,
                                user=self.user,
                                session_id=self.session_id,
                                ctx=self.ctx,
                                latest_archive_overview=latest_archive_overview,
                                archive_uri=archive_uri,
                            )
                            logger.info(f"Extracted {len(extracted)} memories")
                            for ctx_item in extracted:
                                cat = getattr(ctx_item, "category", "") or "unknown"
                                memories_extracted[cat] = memories_extracted.get(cat, 0) + 1
                            self._stats.memories_extracted += len(extracted)
                            get_current_telemetry().set("memory.extracted", len(extracted))

                    # Write relations (using snapshot, not self._usage_records)
                    if self._viking_fs:
                        for usage in usage_records:
                            try:
                                await self._viking_fs.link(self._session_uri, usage.uri, ctx=self.ctx)
                            except Exception as e:
                                logger.warning(f"Failed to create relation to {usage.uri}: {e}")

                    redo_log.mark_done(redo_task_id)

                    # Update active_count (using snapshot, not self._usage_records)
                    if self._vikingdb_manager:
                        uris = [u.uri for u in usage_records if u.uri]
                        try:
                            active_count_updated = await self._vikingdb_manager.increment_active_count(
                                self.ctx, uris
                            )
                        except Exception as e:
                            logger.debug(f"Could not update active_count for usage URIs: {e}")
                        if active_count_updated > 0:
                            logger.info(
                                f"Updated active_count for {active_count_updated} contexts/skills"
                            )

                try:
                    await request_wait_tracker.wait_for_request(
                        telemetry.telemetry_id,
                        timeout=_PHASE2_QUEUE_WAIT_TIMEOUT_SECONDS,
                    )
                except TimeoutError as exc:
                    telemetry.set_error(
                        "session.commit.phase2.wait_for_request",
                        "DEADLINE_EXCEEDED",
                        str(exc),
                    )
                    logger.warning(
                        "Timed out waiting for request-scoped queues for "
                        "telemetry_id=%s after %.1fs; continuing phase2 completion",
                        telemetry.telemetry_id,
                        _PHASE2_QUEUE_WAIT_TIMEOUT_SECONDS,
                    )
            finally:
                request_wait_tracker.cleanup(telemetry.telemetry_id)
                unregister_telemetry(telemetry.telemetry_id)

            # Phase 2 complete — update meta with telemetry and commit info
            snapshot = telemetry.finish("ok")
            await self._merge_and_save_commit_meta(
                archive_index=archive_index,
                memories_extracted=memories_extracted,
                telemetry_snapshot=snapshot,
            )

            # Write .done file last — signals that all state is finalized
            await self._write_done_file(archive_uri, first_message_id, last_message_id)

            tracker.complete(
                task_id,
                {
                    "session_id": self.session_id,
                    "archive_uri": archive_uri,
                    "memories_extracted": memories_extracted,
                    "active_count_updated": active_count_updated,
                    "token_usage": {
                        "llm": dict(self._meta.llm_token_usage),
                        "embedding": dict(self._meta.embedding_token_usage),
                        "total": {
                            "total_tokens": self._meta.llm_token_usage["total_tokens"]
                            + self._meta.embedding_token_usage["total_tokens"]
                        },
                    },
                },
            )
            logger.info(f"Session {self.session_id} memory extraction completed")
        except Exception as e:
            if redo_task_id:
                get_lock_manager().redo_log.mark_done(redo_task_id)
            await self._write_failed_marker(
                archive_uri,
                stage="memory_extraction",
                error=str(e),
            )
            tracker.fail(task_id, str(e))
            logger.exception(f"Memory extraction failed for session {self.session_id}")

    async def _write_done_file(
        self,
        archive_uri: str,
        first_message_id: str,
        last_message_id: str,
    ) -> None:
        """Write .done marker file to the archive directory."""
        if not self._viking_fs:
            return
        content = json.dumps(
            {
                "starting_message_id": first_message_id,
                "ending_message_id": last_message_id,
            },
            ensure_ascii=False,
        )
        await self._viking_fs.write_file(
            uri=f"{archive_uri}/.done",
            content=content,
            ctx=self.ctx,
        )

    async def _write_failed_marker(
        self,
        archive_uri: str,
        stage: str,
        error: str,
        blocked_by: str = "",
    ) -> None:
        """Persist a terminal failure marker for the archive."""
        if not self._viking_fs:
            return
        payload = {
            "stage": stage,
            "error": error,
            "failed_at": get_current_timestamp(),
        }
        if blocked_by:
            payload["blocked_by"] = blocked_by
        await self._viking_fs.write_file(
            uri=f"{archive_uri}/.failed.json",
            content=json.dumps(payload, ensure_ascii=False),
            ctx=self.ctx,
        )

    def _update_active_counts(self) -> int:
        """Update active_count for used contexts/skills."""
        if not self._vikingdb_manager:
            return 0

        uris = [usage.uri for usage in self._usage_records if usage.uri]
        try:
            updated = run_async(self._vikingdb_manager.increment_active_count(self.ctx, uris))
        except Exception as e:
            logger.debug(f"Could not update active_count for usage URIs: {e}")
            updated = 0

        if updated > 0:
            logger.info(f"Updated active_count for {updated} contexts/skills")
        return updated

    async def _update_active_counts_async(self) -> int:
        """Async update active_count for used contexts/skills."""
        if not self._vikingdb_manager:
            return 0

        uris = [usage.uri for usage in self._usage_records if usage.uri]
        try:
            updated = await self._vikingdb_manager.increment_active_count(self.ctx, uris)
        except Exception as e:
            logger.debug(f"Could not update active_count for usage URIs: {e}")
            updated = 0

        if updated > 0:
            logger.info(f"Updated active_count for {updated} contexts/skills")
        return updated

    async def get_session_context(self, token_budget: int = 128_000) -> Dict[str, Any]:
        """Get assembled session context with the latest summary archive and merged messages."""
        if token_budget < 0:
            raise ValueError("token_budget must be greater than or equal to 0")

        context = await self._collect_session_context_components()
        merged_messages = context["messages"]

        message_tokens = sum(m.estimated_tokens for m in merged_messages)

        # 精简日志：只打印关键信息
        logger.info(
            f"[get_session_context] session_id={self.session_id}, "
            f"messages={len(merged_messages)}, tokens={message_tokens}"
        )

        remaining_budget = max(0, token_budget - message_tokens)

        latest_archive = context["latest_archive"]
        include_latest_overview = bool(
            latest_archive and latest_archive["overview_tokens"] <= remaining_budget
        )
        latest_archive_tokens = latest_archive["overview_tokens"] if include_latest_overview else 0
        if include_latest_overview:
            remaining_budget -= latest_archive_tokens

        # pre_archive_abstracts: 保留字段返回空数组，保持 API 向下兼容
        included_pre_archive_abstracts: List[Dict[str, str]] = []
        pre_archive_tokens = 0

        archive_tokens = latest_archive_tokens + pre_archive_tokens
        included_archives = len(included_pre_archive_abstracts)
        dropped_archives = max(
            0, context["total_archives"] - context["failed_archives"] - included_archives
        )

        return {
            "latest_archive_overview": (
                latest_archive["overview"] if include_latest_overview else ""
            ),
            "pre_archive_abstracts": [],  # 保持 API 向后兼容，返回空数组
            "messages": [m.to_dict() for m in merged_messages],
            "estimatedTokens": message_tokens + archive_tokens,
            "stats": {
                "totalArchives": context["total_archives"],
                "includedArchives": included_archives,
                "droppedArchives": dropped_archives,
                "failedArchives": context["failed_archives"],
                "activeTokens": message_tokens,
                "archiveTokens": archive_tokens,
            },
        }

    async def get_context_for_search(self, query: str, max_messages: int = 20) -> Dict[str, Any]:
        """Get session context for intent analysis."""
        del query  # Current query no longer affects historical archive selection.

        context = await self._collect_session_context_components()
        current_messages = context["messages"]
        if max_messages > 0:
            current_messages = current_messages[-max_messages:]
        else:
            current_messages = []

        return {
            "latest_archive_overview": (
                context["latest_archive"]["overview"] if context["latest_archive"] else ""
            ),
            "current_messages": current_messages,
        }

    async def get_context_for_assemble(self, token_budget: int = 128_000) -> Dict[str, Any]:
        """Backward-compatible alias for the assembled session context."""
        return await self.get_session_context(token_budget=token_budget)

    async def get_session_archive(self, archive_id: str) -> Dict[str, Any]:
        """Get one completed archive by archive ID."""
        from openviking_cli.exceptions import NotFoundError

        for archive in await self._get_completed_archive_refs():
            if archive["archive_id"] != archive_id:
                continue

            overview = await self._read_archive_overview(archive["archive_uri"])
            if not overview:
                break

            abstract = await self._read_archive_abstract(archive["archive_uri"], overview)
            return {
                "archive_id": archive_id,
                "abstract": abstract,
                "overview": overview,
                "messages": [
                    m.to_dict() for m in await self._read_archive_messages(archive["archive_uri"])
                ],
            }

        raise NotFoundError(archive_id, "session archive")

    # ============= Internal methods =============

    async def _collect_session_context_components(self) -> Dict[str, Any]:
        """Collect the latest summary archive and merged pending/live messages."""
        completed_archives = await self._get_completed_archive_refs()
        latest_archive = None
        pre_archive_abstracts: List[Dict[str, Any]] = []
        failed_archives = 0

        for archive in completed_archives:
            if latest_archive is None:
                overview = await self._read_archive_overview(archive["archive_uri"])
                if not overview:
                    failed_archives += 1
                    continue

                latest_archive = {
                    "archive_id": archive["archive_id"],
                    "archive_uri": archive["archive_uri"],
                    "overview": overview,
                    "overview_tokens": await self._read_archive_overview_tokens(
                        archive["archive_uri"], overview
                    ),
                }
            abstract = await self._read_archive_abstract(archive["archive_uri"])
            if abstract:
                pre_archive_abstracts.append(
                    {
                        "archive_id": archive["archive_id"],
                        "abstract": abstract,
                        "tokens": -(-len(abstract) // 4),
                    }
                )
            else:
                failed_archives += 1

        return {
            "latest_archive": latest_archive,
            "pre_archive_abstracts": pre_archive_abstracts,
            "total_archives": len(completed_archives),
            "failed_archives": failed_archives,
            "messages": await self._get_pending_archive_messages() + list(self._messages),
        }

    async def _list_archive_refs(self) -> List[Dict[str, Any]]:
        """List archive refs sorted by archive index descending."""
        if not self._viking_fs or self.compression.compression_index <= 0:
            return []

        try:
            history_items = await self._viking_fs.ls(f"{self._session_uri}/history", ctx=self.ctx)
        except Exception:
            return []

        refs: List[Dict[str, Any]] = []
        for item in history_items:
            name = item.get("name") if isinstance(item, dict) else item
            if not name or not name.startswith("archive_"):
                continue
            try:
                index = int(name.split("_")[1])
            except Exception:
                continue

            refs.append(
                {
                    "archive_id": name,
                    "archive_uri": f"{self._session_uri}/history/{name}",
                    "index": index,
                }
            )

        return sorted(refs, key=lambda item: item["index"], reverse=True)

    async def _get_completed_archive_refs(
        self,
        exclude_archive_uri: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return completed archive refs sorted by archive index descending."""
        completed: List[Dict[str, Any]] = []
        exclude = exclude_archive_uri.rstrip("/") if exclude_archive_uri else None

        for archive in await self._list_archive_refs():
            if exclude and archive["archive_uri"] == exclude:
                continue
            try:
                await self._viking_fs.read_file(f"{archive['archive_uri']}/.done", ctx=self.ctx)
            except Exception:
                continue
            completed.append(archive)

        return completed

    async def _get_blocking_failed_archive_ref(self) -> Optional[Dict[str, Any]]:
        """Return the earliest unresolved failed archive, if any."""
        for archive in sorted(await self._list_archive_refs(), key=lambda item: item["index"]):
            try:
                await self._viking_fs.read_file(f"{archive['archive_uri']}/.done", ctx=self.ctx)
                continue
            except Exception:
                pass
            try:
                await self._viking_fs.read_file(
                    f"{archive['archive_uri']}/.failed.json",
                    ctx=self.ctx,
                )
            except Exception:
                continue
            return archive
        return None

    async def _read_archive_overview(self, archive_uri: str) -> str:
        """Read archive overview text."""
        try:
            overview = await self._viking_fs.read_file(f"{archive_uri}/.overview.md", ctx=self.ctx)
        except Exception:
            return ""
        return overview or ""

    async def _read_archive_abstract(self, archive_uri: str, overview: str = "") -> str:
        """Read archive abstract text, falling back to summary extraction."""
        try:
            abstract = await self._viking_fs.read_file(f"{archive_uri}/.abstract.md", ctx=self.ctx)
        except Exception:
            abstract = ""

        if abstract:
            return abstract

        if not overview:
            overview = await self._read_archive_overview(archive_uri)
        return self._extract_abstract_from_summary(overview)

    async def _read_archive_overview_tokens(self, archive_uri: str, overview: str) -> int:
        """Read overview token estimate from archive metadata."""
        overview_tokens = -(-len(overview) // 4)
        try:
            meta_content = await self._viking_fs.read_file(
                f"{archive_uri}/.meta.json", ctx=self.ctx
            )
            overview_tokens = json.loads(meta_content).get("overview_tokens", overview_tokens)
        except Exception:
            pass
        return overview_tokens

    async def _read_archive_messages(self, archive_uri: str) -> List[Message]:
        """Read archived messages from one archive."""
        try:
            content = await self._viking_fs.read_file(f"{archive_uri}/messages.jsonl", ctx=self.ctx)
        except Exception:
            return []

        messages: List[Message] = []
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                messages.append(Message.from_dict(json.loads(line)))
            except Exception:
                continue

        return messages

    async def _get_latest_completed_archive_summary(
        self,
        exclude_archive_uri: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the newest readable completed archive summary."""
        for archive in await self._get_completed_archive_refs(exclude_archive_uri):
            overview = await self._read_archive_overview(archive["archive_uri"])
            if not overview:
                continue

            return {
                "archive_id": archive["archive_id"],
                "archive_uri": archive["archive_uri"],
                "overview": overview,
                "abstract": await self._read_archive_abstract(archive["archive_uri"], overview),
                "overview_tokens": await self._read_archive_overview_tokens(
                    archive["archive_uri"], overview
                ),
            }

        return None

    async def _get_latest_completed_archive_overview(
        self,
        exclude_archive_uri: Optional[str] = None,
    ) -> str:
        """Return the newest completed archive overview, skipping incomplete archives."""
        summary = await self._get_latest_completed_archive_summary(exclude_archive_uri)
        return summary["overview"] if summary else ""

    async def _get_pending_archive_messages(self) -> List[Message]:
        """Return messages from incomplete archives newer than the latest completed archive."""
        latest_completed_index = 0
        incomplete_archives: List[Dict[str, Any]] = []
        for archive in sorted(await self._list_archive_refs(), key=lambda item: item["index"]):
            try:
                await self._viking_fs.read_file(f"{archive['archive_uri']}/.done", ctx=self.ctx)
                latest_completed_index = archive["index"]
            except Exception:
                incomplete_archives.append(archive)

        pending_messages: List[Message] = []
        for archive in incomplete_archives:
            if archive["index"] <= latest_completed_index:
                continue
            pending_messages.extend(await self._read_archive_messages(archive["archive_uri"]))

        return pending_messages

    @staticmethod
    def _archive_index_from_uri(archive_uri: str) -> int:
        """Parse archive_NNN suffix into an integer index."""
        match = re.search(r"archive_(\d+)$", archive_uri.rstrip("/"))
        if not match:
            raise ValueError(f"Invalid archive URI: {archive_uri}")
        return int(match.group(1))

    async def _wait_for_previous_archive_done(self, archive_index: int) -> bool:
        """Wait until the previous archive is done, or report dependency failure."""
        if archive_index <= 1 or not self._viking_fs:
            return True

        previous_archive_uri = f"{self._session_uri}/history/archive_{archive_index - 1:03d}"
        while True:
            try:
                await self._viking_fs.read_file(f"{previous_archive_uri}/.done", ctx=self.ctx)
                return True
            except Exception:
                pass

            try:
                await self._viking_fs.read_file(
                    f"{previous_archive_uri}/.failed.json",
                    ctx=self.ctx,
                )
                return False
            except Exception:
                pass

            await asyncio.sleep(_ARCHIVE_WAIT_POLL_SECONDS)

    async def _merge_and_save_commit_meta(
        self,
        archive_index: int,
        memories_extracted: Dict[str, int],
        telemetry_snapshot: Any,
    ) -> None:
        """Reload and merge latest meta state before persisting commit results."""
        latest_meta = self._meta
        try:
            meta_content = await self._viking_fs.read_file(
                f"{self._session_uri}/.meta.json",
                ctx=self.ctx,
            )
            latest_meta = SessionMeta.from_dict(json.loads(meta_content))
        except Exception:
            latest_meta = self._meta

        if telemetry_snapshot:
            llm = telemetry_snapshot.summary.get("tokens", {}).get("llm", {})
            latest_meta.llm_token_usage["prompt_tokens"] += llm.get("input", 0)
            latest_meta.llm_token_usage["completion_tokens"] += llm.get("output", 0)
            latest_meta.llm_token_usage["total_tokens"] += llm.get("total", 0)
            embedding = telemetry_snapshot.summary.get("tokens", {}).get("embedding", {})
            latest_meta.embedding_token_usage["total_tokens"] += embedding.get("total", 0)

        latest_meta.commit_count = max(latest_meta.commit_count, archive_index)
        for cat, count in memories_extracted.items():
            latest_meta.memories_extracted[cat] = latest_meta.memories_extracted.get(cat, 0) + count
            latest_meta.memories_extracted["total"] = (
                latest_meta.memories_extracted.get("total", 0) + count
            )
        latest_meta.last_commit_at = get_current_timestamp()
        latest_meta.message_count = await self._read_live_message_count()
        self._meta = latest_meta
        await self._save_meta()

    async def _read_live_message_count(self) -> int:
        """Count current live session messages from persisted storage."""
        if not self._viking_fs:
            return len(self._messages)
        try:
            content = await self._viking_fs.read_file(
                f"{self._session_uri}/messages.jsonl",
                ctx=self.ctx,
            )
        except Exception:
            return len(self._messages)
        return len([line for line in content.strip().split("\n") if line.strip()])

    def _extract_abstract_from_summary(self, summary: str) -> str:
        """Extract one-sentence overview from structured summary."""
        if not summary:
            return ""

        match = re.search(r"^\*\*[^*]+\*\*:\s*(.+)$", summary, re.MULTILINE)
        if match:
            return match.group(1).strip()

        first_line = summary.split("\n")[0].strip()
        return first_line if first_line else ""

    @staticmethod
    def _format_message_for_wm(m: Message) -> str:
        """Format a single message for WM generation, including all parts.

        Includes TextPart, ToolPart (name + status + full output), and
        ContextPart so the WM LLM sees the complete conversation.
        """
        lines: List[str] = []
        for p in m.parts:
            if isinstance(p, TextPart) and p.text.strip():
                lines.append(p.text)
            elif isinstance(p, ToolPart) and p.tool_name:
                status = p.tool_status or "completed"
                output = p.tool_output or ""
                lines.append(f"[tool:{p.tool_name} ({status})] {output}")
            elif isinstance(p, ContextPart) and p.abstract:
                lines.append(f"[context] {p.abstract}")
        body = "\n".join(lines) if lines else "(no content)"
        return f"[{m.role}]: {body}"

    def _generate_archive_summary(
        self,
        messages: List[Message],
        latest_archive_overview: str = "",
    ) -> str:
        """Generate structured summary for archive."""
        if not messages:
            return ""

        formatted = "\n".join(self._format_message_for_wm(m) for m in messages)

        vlm = get_openviking_config().vlm
        if vlm and vlm.is_available():
            try:
                from openviking.prompts import render_prompt

                prompt = render_prompt(
                    "compression.structured_summary",
                    {
                        "messages": formatted,
                        "latest_archive_overview": latest_archive_overview,
                    },
                )
                return run_async(vlm.get_completion_async(prompt))
            except Exception as e:
                logger.warning(f"LLM summary failed: {e}")

        turn_count = len([m for m in messages if m.role == "user"])
        return f"# Session Summary\n\n**Overview**: {turn_count} turns, {len(messages)} messages"

    async def _generate_archive_summary_async(
        self,
        messages: List[Message],
        latest_archive_overview: str = "",
    ) -> str:
        """Generate Working Memory document for the current archive (async).

        Two paths:

        * No prior WM -> call ``compression.ov_wm_v2`` with a plain completion
          and return the full 7-section markdown.
        * Has prior WM -> call ``compression.ov_wm_v2_update`` with the
          ``update_working_memory`` tool forced on; parse per-section
          decisions and merge them against the previous WM. On any
          tool_call / JSON / schema anomaly, fall back to the creation
          prompt so we never persist malformed output as WM.
        """
        _wm_debug(f"_generate_archive_summary_async called "
                  f"messages={len(messages)} prior_wm={len(latest_archive_overview)}B")
        if not messages:
            return ""

        formatted = "\n".join(self._format_message_for_wm(m) for m in messages)

        vlm = get_openviking_config().vlm
        if not (vlm and vlm.is_available()):
            turn_count = len([m for m in messages if m.role == "user"])
            return (
                f"# Session Summary\n\n"
                f"**Overview**: {turn_count} turns, {len(messages)} messages"
            )

        try:
            from openviking.prompts import render_prompt
        except Exception as e:
            logger.warning(f"Prompt module unavailable: {e}")
            turn_count = len([m for m in messages if m.role == "user"])
            return (
                f"# Session Summary\n\n"
                f"**Overview**: {turn_count} turns, {len(messages)} messages"
            )

        # -------- Detect WM v2 format --------
        _is_wm_v2 = latest_archive_overview and any(
            f"## {s}" in latest_archive_overview for s in WM_SEVEN_SECTIONS
        )

        # -------- Branch 1: no prior WM (or legacy format) -> full creation --------
        if not latest_archive_overview or not _is_wm_v2:
            _wm_debug(
                f"branch=CREATE (prior={'legacy' if latest_archive_overview else 'none'} "
                f"{len(latest_archive_overview or '')}B)"
            )
            try:
                prompt = render_prompt(
                    "compression.ov_wm_v2",
                    {"messages": formatted, "latest_archive_overview": latest_archive_overview or ""},
                )
                return await vlm.get_completion_async(prompt)
            except Exception as e:
                _wm_debug(f"creation failed: {e}")
                logger.warning(f"WM creation failed: {e}")
                turn_count = len([m for m in messages if m.role == "user"])
                return (
                    f"# Session Summary\n\n"
                    f"**Overview**: {turn_count} turns, {len(messages)} messages"
                )

        # -------- Branch 2: has prior WM v2 -> tool_call incremental update --------
        _wm_debug(f"branch=UPDATE (prior WM={len(latest_archive_overview)}B)")
        try:
            reminders = Session._build_wm_section_reminders(latest_archive_overview)
            if reminders:
                _wm_debug(f"section_reminders injected ({len(reminders)}B)")
            update_prompt = render_prompt(
                "compression.ov_wm_v2_update",
                {
                    "messages": formatted,
                    "latest_archive_overview": latest_archive_overview,
                    "wm_section_reminders": reminders,
                },
            )
            resp = await vlm.get_completion_async(
                prompt=update_prompt,
                tools=[WM_UPDATE_TOOL],
                tool_choice={
                    "type": "function",
                    "function": {"name": "update_working_memory"},
                },
            )
        except Exception as e:
            import traceback as _tb
            _wm_debug(
                f"tool_call raised: {type(e).__name__}: {e} "
                f"tb={_tb.format_exc()[-400:]}"
            )
            logger.warning(
                "WM update tool_call failed (%s); falling back to creation prompt", e
            )
            return await self._fallback_generate_wm_creation(formatted, messages, latest_archive_overview)

        has_tc = bool(getattr(resp, "has_tool_calls", False) and getattr(resp, "tool_calls", None))
        _preview = (str(resp)[:200]).replace(chr(10), " ")
        _finish = getattr(resp, "finish_reason", "n/a")
        _usage = getattr(resp, "usage", {}) or {}
        _wm_debug(
            f"resp type={type(resp).__name__} has_tool_calls={has_tc} "
            f"finish_reason={_finish!r} usage={_usage} preview={_preview!r}"
        )

        if not has_tc:
            logger.warning(
                "WM update: LLM returned no tool_call; falling back to creation prompt"
            )
            return await self._fallback_generate_wm_creation(formatted, messages, latest_archive_overview)

        try:
            raw_args = resp.tool_calls[0].arguments
            _wm_debug(
                f"raw_args type={type(raw_args).__name__} "
                f"preview={str(raw_args)[:400]!r}"
            )
            args = raw_args
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                raise ValueError(f"tool_call arguments is not a dict: {type(args).__name__}")

            # OV's VLM backend wraps unparseable JSON strings as {"raw": "..."}.
            # Try a best-effort recovery: json.loads the raw string; if that
            # still fails, attempt a tolerant parse (add a closing brace if the
            # string looks truncated, extract up to the last valid JSON object).
            if list(args.keys()) == ["raw"] and isinstance(args["raw"], str):
                raw_str = args["raw"]
                _wm_debug(f"args has only 'raw' key; attempting recovery len={len(raw_str)}")
                recovered = None
                try:
                    recovered = json.loads(raw_str)
                except Exception:
                    # Try to close a truncated JSON by appending closing braces
                    # for every unmatched opener.
                    try:
                        opens = raw_str.count("{") - raw_str.count("}")
                        if opens > 0:
                            patched = raw_str.rstrip().rstrip(",") + ("}" * opens)
                            recovered = json.loads(patched)
                            _wm_debug(
                                f"recovered by closing {opens} brace(s); "
                                f"patched_len={len(patched)}"
                            )
                    except Exception as e2:
                        _wm_debug(f"brace-close recovery failed: {e2}")
                if isinstance(recovered, dict):
                    args = recovered
                    _wm_debug(f"recovered args keys={list(args.keys())}")

            _wm_debug(f"args keys={list(args.keys())}")
            # Tolerant: if LLM returned {"Session Title": {...}, ...} without
            # the outer "sections" wrapper, treat the top-level as ops.
            if "sections" in args and isinstance(args["sections"], dict):
                ops = args["sections"]
            elif all(k in args for k in WM_SEVEN_SECTIONS):
                _wm_debug("args has section keys directly; accepting as ops")
                ops = args
            else:
                raise ValueError(
                    f"tool_call arguments.sections missing; keys={list(args.keys())}"
                )
            if not isinstance(ops, dict):
                raise ValueError("ops is not a dict")
        except Exception as e:
            _wm_debug(
                f"args parse failed: {type(e).__name__}: {e}; "
                f"attempting regex recovery from raw"
            )
            # Regex salvage: when the LLM emits slightly-broken JSON (curly
            # quote, unescaped newline, truncated string), OV's VLM backend
            # wraps it as {"raw": "..."} and all structural parsing fails. We
            # still try to pull each section's op directly via regex before
            # falling back to the creation prompt. Missing sections default
            # to KEEP in _merge_wm_sections so old content is preserved.
            raw_for_recovery = ""
            if isinstance(raw_args, str):
                raw_for_recovery = raw_args
            elif isinstance(raw_args, dict):
                if isinstance(raw_args.get("raw"), str):
                    raw_for_recovery = raw_args["raw"]
                else:
                    try:
                        raw_for_recovery = json.dumps(raw_args, ensure_ascii=False)
                    except Exception:
                        raw_for_recovery = str(raw_args)
            salvaged = Session._wm_recover_ops_from_raw(raw_for_recovery)
            if salvaged:
                _wm_debug(
                    f"regex recovery salvaged {len(salvaged)}/"
                    f"{len(WM_SEVEN_SECTIONS)} sections: "
                    f"{[(k, v.get('op')) for k, v in salvaged.items()]}"
                )
                logger.info(
                    "WM update: regex recovery salvaged %d/%d sections; "
                    "proceeding with partial ops",
                    len(salvaged),
                    len(WM_SEVEN_SECTIONS),
                )
                return self._merge_wm_sections(latest_archive_overview, salvaged)
            _wm_debug(
                "regex recovery salvaged 0 sections; falling back to creation prompt"
            )
            logger.warning(
                "WM update: tool_call arguments parse failed (%s); "
                "regex recovery found nothing; falling back to creation prompt",
                e,
            )
            return await self._fallback_generate_wm_creation(formatted, messages, latest_archive_overview)

        _wm_debug(
            f"ops keys={list(ops.keys())[:7]} "
            f"ops_summary={[(k, v.get('op') if isinstance(v, dict) else type(v).__name__) for k, v in ops.items()][:7]}"
        )
        return self._merge_wm_sections(latest_archive_overview, ops)

    async def _fallback_generate_wm_creation(
        self,
        formatted_messages: str,
        messages: List[Message],
        prior_overview: str = "",
    ) -> str:
        """Re-run WM creation prompt when the update tool_call path fails.

        Passes ``prior_overview`` so the creation prompt can incorporate
        accumulated context instead of generating from scratch.
        """
        _wm_debug(
            f"fallback creation prompt: prior_overview={len(prior_overview)}B "
            f"messages={len(messages)}"
        )
        try:
            from openviking.prompts import render_prompt

            prompt = render_prompt(
                "compression.ov_wm_v2",
                {"messages": formatted_messages, "latest_archive_overview": prior_overview},
            )
            return await get_openviking_config().vlm.get_completion_async(prompt)
        except Exception as e:
            logger.warning(f"WM creation fallback failed: {e}")
            turn_count = len([m for m in messages if m.role == "user"])
            return (
                f"# Session Summary\n\n"
                f"**Overview**: {turn_count} turns, {len(messages)} messages"
            )

    @staticmethod
    def _parse_wm_sections(text: str) -> Dict[str, str]:
        """Parse an existing WM markdown into {header_line: body_text}.

        Header comparison is case-sensitive on purpose: the update path only
        uses this output to look up bodies by our own canonical headers.
        """
        sections: Dict[str, str] = {}
        current: Optional[str] = None
        buf: List[str] = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                if current is not None:
                    sections[current] = "\n".join(buf).strip()
                current = stripped
                buf = []
            elif current is not None:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf).strip()
        return sections

    _WM_SECTION_BULLET_THRESHOLD = 25
    _WM_SECTION_TOKEN_THRESHOLD = 1500
    _WM_OVERSIZED_APPEND_CAP = 5
    _WM_CONSOLIDATION_SENTINEL = (
        "[⚠ CONSOLIDATION REQUIRED: Key Facts exceeds size limit. "
        "You MUST use UPDATE to merge and compress existing bullets "
        "before adding new facts.]"
    )

    @staticmethod
    def _build_wm_section_reminders(overview: str) -> str:
        """Compute dynamic section-size warnings for the WM update prompt.

        Scans the current overview, counts bullets and estimates tokens for
        each section.  Returns an XML block that the prompt template can
        inject verbatim so the LLM knows which sections need consolidation.
        """
        if not overview:
            return ""
        sections = Session._parse_wm_sections(overview)
        warnings: List[str] = []
        for header, body in sections.items():
            name = header.lstrip("#").strip()
            if name in Session._WM_APPEND_ONLY_SECTIONS:
                continue
            items = Session._wm_extract_bullet_items(body)
            est_tokens = len(body) // 4
            if (
                len(items) > Session._WM_SECTION_BULLET_THRESHOLD
                or est_tokens > Session._WM_SECTION_TOKEN_THRESHOLD
            ):
                warnings.append(
                    f'WARNING: "{name}" has {len(items)} bullets '
                    f"(~{est_tokens} tokens).\n"
                    f"This section MUST be consolidated via UPDATE. Group "
                    f"related facts by topic into category summaries. "
                    f"Preserve names, dates, and exact values but merge "
                    f"repetitive events into patterns.\n"
                    f"Target: <={Session._WM_SECTION_BULLET_THRESHOLD} "
                    f"bullets, <={Session._WM_SECTION_TOKEN_THRESHOLD} tokens."
                )
        if not warnings:
            return ""
        return "<section_size_warnings>\n" + "\n\n".join(warnings) + "\n</section_size_warnings>"

    # Sections where server enforces APPEND-only regardless of what the LLM emits.
    _WM_APPEND_ONLY_SECTIONS = frozenset({
        "Errors & Corrections",
    })

    # Very loose path-like token regex used to detect file paths that existed
    # in prior Files & Context and MUST NOT silently disappear after UPDATE.
    _WM_PATH_LIKE_RE = re.compile(
        r"(?:[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|yaml|yml|json|sh|ps1|cmd|bat|toml|ini|cfg|rs|go))"
        r"|(?:[a-zA-Z_][\w\-]*(?:/[a-zA-Z_][\w\-]*){1,})",
        re.IGNORECASE,
    )

    _WM_TITLE_STOPWORDS = frozenset({
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
        "with", "by", "at", "from", "session", "title", "working", "memory",
        "plan", "plans", "notes", "note",
    })

    @staticmethod
    def _wm_recover_ops_from_raw(raw_str: str) -> Dict[str, Any]:
        """Best-effort regex recovery of per-section ops from a malformed
        tool_call arguments string.

        Used when OV's VLM backend wraps non-JSON tool-call args as
        ``{"raw": "..."}`` (typical when the LLM emits unescaped characters
        inside a string value, uses curly quotes, or emits a truncated JSON).
        Scans the raw text for each of the 7 fixed section names and their
        ``{"op": "KEEP|UPDATE|APPEND", ...}`` markers. Partial UPDATE
        content / APPEND items are tolerated; sections that cannot be found
        at all are simply omitted (the merge step will then default them to
        KEEP and preserve the prior content).

        Returns a partial ops dict (possibly fewer than 7 sections).
        """
        if not raw_str:
            return {}

        ops: Dict[str, Any] = {}
        names_alt = "|".join(re.escape(n) for n in WM_SEVEN_SECTIONS)

        # --- KEEP: "Name": {"op": "KEEP"} ---
        keep_re = re.compile(
            rf'"({names_alt})"\s*:\s*\{{\s*"op"\s*:\s*"KEEP"\s*\}}'
        )
        for m in keep_re.finditer(raw_str):
            ops.setdefault(m.group(1), {"op": "KEEP"})

        # --- UPDATE: "Name": {"op": "UPDATE", "content": "..."} ---
        # Capture content non-greedily up to either:
        #   (a) a closing '"}' that ends the section, or
        #   (b) the start of the next section key (meaning content string was truncated).
        # DOTALL so newlines inside content don't end the match.
        update_re = re.compile(
            rf'"({names_alt})"\s*:\s*\{{\s*"op"\s*:\s*"UPDATE"\s*,\s*"content"\s*:\s*"'
            rf'((?:[^"\\]|\\.)*?)'
            rf'(?:"\s*\}}|(?="\s*,\s*"(?:' + names_alt + r')"))',
            re.DOTALL,
        )
        for m in update_re.finditer(raw_str):
            header = m.group(1)
            if header in ops:
                continue
            captured = m.group(2)
            try:
                content = json.loads('"' + captured + '"')
            except Exception:
                content = captured
            ops[header] = {"op": "UPDATE", "content": content}

        # --- APPEND: "Name": {"op": "APPEND", "items": [...]} ---
        # Tolerate truncated array (no closing ']').
        append_re = re.compile(
            rf'"({names_alt})"\s*:\s*\{{\s*"op"\s*:\s*"APPEND"\s*,\s*"items"\s*:\s*\['
            rf'([\s\S]*?)(?:\]|$)',
        )
        item_re = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
        for m in append_re.finditer(raw_str):
            header = m.group(1)
            if header in ops:
                continue
            items_raw = m.group(2)
            items: List[str] = []
            for im in item_re.finditer(items_raw):
                captured = im.group(1)
                try:
                    items.append(json.loads('"' + captured + '"'))
                except Exception:
                    items.append(captured)
            ops[header] = {"op": "APPEND", "items": items}

        return ops

    @staticmethod
    def _wm_extract_bullet_items(text: str) -> List[str]:
        """Extract bullet-like items from a markdown section body.

        Recognizes ``- ...``, ``* ...``, ``1. ...``, ``2) ...`` lines, as well
        as plain non-bullet lines (treated as single items).
        """
        items: List[str] = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = re.match(r"^(?:[-*]|\d+[\.)])\s+(.*)$", stripped)
            if m:
                item = m.group(1).strip()
            else:
                item = stripped
            if item:
                items.append(item)
        return items

    @staticmethod
    def _wm_enforce_append_only(
        header: str, op: Any, old_content: str
    ) -> Dict[str, Any]:
        """Guard: force KEEP/APPEND semantics on APPEND-only sections.

        - KEEP and APPEND pass through.
        - UPDATE is demoted: its content is parsed for bullet items; any items
          that are not already present in the old body are re-emitted as APPEND
          items, so nothing from the LLM's rewrite is lost but nothing from
          the old body is dropped either.
        - None/unknown op -> KEEP.
        """
        if not isinstance(op, dict):
            return {"op": "KEEP"}
        op_name = (op.get("op") or "").upper()
        if op_name in ("KEEP", "APPEND"):
            return op
        if op_name != "UPDATE":
            return {"op": "KEEP"}

        new_content = (op.get("content") or "").strip()
        new_items = Session._wm_extract_bullet_items(new_content)
        old_lower = (old_content or "").lower()
        fresh_items = []
        for it in new_items:
            key = it.strip("_* `").lower()
            if key and key not in old_lower:
                fresh_items.append(it)
        _wm_debug(
            f"guard: section {header!r} UPDATE -> forced APPEND "
            f"(llm_items={len(new_items)}, fresh_after_dedup={len(fresh_items)})"
        )
        if not fresh_items:
            return {"op": "KEEP"}
        return {"op": "APPEND", "items": fresh_items}

    _WM_KEY_FACTS_MIN_BULLET_RATIO = 0.15
    _WM_KEY_FACTS_MIN_ANCHOR_COVERAGE = 0.70

    _WM_ANCHOR_DATE_RE = re.compile(
        r"\b\d{4}-\d{2}-\d{2}\b"
        r"|\b\d{1,2}\s+(?:January|February|March|April|May|June"
        r"|July|August|September|October|November|December)\s+\d{4}\b",
        re.IGNORECASE,
    )
    _WM_ANCHOR_NUMBER_RE = re.compile(
        r"\b\d+\s+(?:years?|months?|weeks?|days?|kids?|children"
        r"|hours?|miles?|times?|sessions?|rounds?|visits?"
        r"|dollars?|euros?|pounds?|bedrooms?|paintings?"
        r"|people|persons?)\b"
        r"|\$\d[\d,]*"
        r"|\b\d+\s+(?:AM|PM)\b",
        re.IGNORECASE,
    )
    _WM_ANCHOR_DECISION_RE = re.compile(
        r"\b(?:because|decided|chose|committed|agreed|resolved)\b",
        re.IGNORECASE,
    )
    _WM_ANCHOR_STOPWORDS = frozenset({
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
        "with", "by", "at", "from", "is", "are", "was", "were", "has",
        "have", "had", "been", "be", "will", "would", "could", "should",
        "may", "might", "shall", "this", "that", "these", "those",
        "not", "no", "but", "if", "then", "so", "as", "it", "its",
        "they", "their", "them", "she", "her", "he", "him", "his",
        "we", "our", "us", "you", "your", "who", "which", "what",
        "when", "where", "how", "why", "all", "each", "every",
        "both", "few", "more", "most", "other", "some", "such",
        "than", "too", "very", "also", "just", "about", "after",
        "before", "between", "into", "through", "during", "again",
        "further", "once", "here", "there", "over", "under", "out",
        "up", "down", "off", "own", "same", "only", "new", "old",
        "key", "facts", "decisions", "session", "working", "memory",
    })

    @staticmethod
    def _extract_lexical_anchors(text: str) -> set:
        """Extract fact-preserving anchors: dates, numbers, proper nouns,
        decision markers."""
        anchors: set = set()
        for m in Session._WM_ANCHOR_DATE_RE.finditer(text):
            anchors.add(m.group().lower().strip())
        for m in Session._WM_ANCHOR_NUMBER_RE.finditer(text):
            anchors.add(m.group().lower().strip())
        for m in Session._WM_ANCHOR_DECISION_RE.finditer(text):
            anchors.add(m.group().lower().strip())
        for token in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text):
            if token.lower() not in Session._WM_ANCHOR_STOPWORDS:
                anchors.add(token.lower())
        return anchors

    @staticmethod
    def _salvage_new_items_from_rejected_update(
        new_content: str, old_content: str
    ) -> Dict[str, Any]:
        """When a consolidation UPDATE is rejected, salvage genuinely new
        items from the update content and APPEND them so we don't lose
        facts from the current round."""
        new_items = Session._wm_extract_bullet_items(new_content)
        old_lower = (old_content or "").lower()
        fresh_items = []
        for it in new_items:
            key = it.strip("_* `").lower()
            if key and key not in old_lower:
                fresh_items.append(it)
        if fresh_items:
            _wm_debug(
                f"guard: salvaged {len(fresh_items)} new items from rejected UPDATE"
            )
            return {"op": "APPEND", "items": fresh_items}
        return {"op": "KEEP"}

    @staticmethod
    def _wm_enforce_key_facts_consolidation(
        op: Any, old_content: str
    ) -> Dict[str, Any]:
        """Guard: allow controlled consolidation for Key Facts & Decisions.

        Layer 1 — reject trivially small UPDATEs (< 15% bullet count).
        Layer 2 — require >= 70% lexical anchor coverage.
        Rejection salvages genuinely new items from the rejected UPDATE
        via APPEND, so current-round facts are not silently lost.

        Anti-bloat: when Key Facts is already oversized (bullets or tokens
        exceed threshold), APPEND is throttled:
        - 1x~2x threshold → accept only genuinely new items (deduped),
          capped at _WM_OVERSIZED_APPEND_CAP
        - >2x threshold (emergency) → reject all normal facts, insert a
          single idempotent consolidation sentinel; on subsequent rounds
          the sentinel is already present so nothing is added (hard stop)
        """
        if not isinstance(op, dict):
            return {"op": "KEEP"}
        op_name = (op.get("op") or "").upper()
        if op_name == "KEEP":
            return op
        if op_name == "APPEND":
            old_items = Session._wm_extract_bullet_items(old_content or "")
            est_tokens = len(old_content or "") // 4
            bullet_over = len(old_items) > Session._WM_SECTION_BULLET_THRESHOLD
            token_over = est_tokens > Session._WM_SECTION_TOKEN_THRESHOLD
            if not bullet_over and not token_over:
                return op
            append_items = op.get("items") or []
            if not append_items:
                raw = (op.get("content") or "").strip()
                append_items = Session._wm_extract_bullet_items(raw)
            append_items = [str(it) for it in append_items if it]
            old_lower = (old_content or "").lower()
            fresh = [
                it for it in append_items
                if it.strip("_* `").lower() not in old_lower
            ]
            emergency = (
                len(old_items) > Session._WM_SECTION_BULLET_THRESHOLD * 2
                or est_tokens > Session._WM_SECTION_TOKEN_THRESHOLD * 2
            )
            if emergency:
                sentinel = Session._WM_CONSOLIDATION_SENTINEL
                if sentinel.lower() in old_lower:
                    _wm_debug(
                        f"guard: Key Facts APPEND blocked (emergency, "
                        f"sentinel already present): "
                        f"bullets={len(old_items)} est_tok={est_tokens} — "
                        f"dropped {len(fresh)} new item(s)"
                    )
                    return {"op": "KEEP"}
                _wm_debug(
                    f"guard: Key Facts APPEND blocked (emergency, "
                    f"inserting sentinel): "
                    f"bullets={len(old_items)} est_tok={est_tokens} — "
                    f"dropped {len(fresh)} new item(s)"
                )
                return {"op": "APPEND", "items": [sentinel]}
            cap = Session._WM_OVERSIZED_APPEND_CAP
            accepted = fresh[:cap]
            _wm_debug(
                f"guard: Key Facts APPEND throttled (oversized): "
                f"bullets={len(old_items)} est_tok={est_tokens} — "
                f"input={len(append_items)} deduped={len(fresh)} "
                f"accepted={len(accepted)} (cap={cap})"
            )
            if not accepted:
                return {"op": "KEEP"}
            return {"op": "APPEND", "items": accepted}
        if op_name != "UPDATE":
            return {"op": "KEEP"}

        new_content = (op.get("content") or "").strip()
        old_items = Session._wm_extract_bullet_items(old_content or "")
        new_items = Session._wm_extract_bullet_items(new_content)

        if not old_items:
            return op

        est_tokens = len(old_content or "") // 4
        is_emergency = (
            len(old_items) > Session._WM_SECTION_BULLET_THRESHOLD * 2
            or est_tokens > Session._WM_SECTION_TOKEN_THRESHOLD * 2
        )

        # Layer 1: reject trivially small consolidation
        ratio = len(new_items) / len(old_items) if old_items else 1.0
        if ratio < Session._WM_KEY_FACTS_MIN_BULLET_RATIO:
            _wm_debug(
                f"guard: Key Facts consolidation REJECTED (layer1): "
                f"new={len(new_items)} / old={len(old_items)} = "
                f"{ratio:.2%} < {Session._WM_KEY_FACTS_MIN_BULLET_RATIO:.0%}"
            )
            salvaged = Session._salvage_new_items_from_rejected_update(
                new_content, old_content
            )
            if is_emergency and salvaged.get("op") == "APPEND":
                _wm_debug(
                    "guard: suppressing salvage APPEND (emergency level)"
                )
                return {"op": "KEEP"}
            return salvaged

        # Layer 2: lexical anchor coverage
        old_anchors = Session._extract_lexical_anchors(old_content or "")
        if old_anchors:
            new_anchors = Session._extract_lexical_anchors(new_content)
            covered = len(old_anchors & new_anchors)
            coverage = covered / len(old_anchors)
            if coverage < Session._WM_KEY_FACTS_MIN_ANCHOR_COVERAGE:
                _wm_debug(
                    f"guard: Key Facts consolidation REJECTED (layer2): "
                    f"anchor coverage={coverage:.2%} "
                    f"({covered}/{len(old_anchors)}) < "
                    f"{Session._WM_KEY_FACTS_MIN_ANCHOR_COVERAGE:.0%}"
                )
                salvaged = Session._salvage_new_items_from_rejected_update(
                    new_content, old_content
                )
                if is_emergency and salvaged.get("op") == "APPEND":
                    _wm_debug(
                        "guard: suppressing salvage APPEND (emergency level)"
                    )
                    return {"op": "KEEP"}
                return salvaged
            _wm_debug(
                f"guard: Key Facts consolidation ACCEPTED: "
                f"bullets {len(old_items)}->{len(new_items)} "
                f"({ratio:.1%}), "
                f"anchors={coverage:.1%} ({covered}/{len(old_anchors)})"
            )
        else:
            _wm_debug(
                f"guard: Key Facts consolidation ACCEPTED (no old anchors): "
                f"bullets {len(old_items)}->{len(new_items)}"
            )

        return op

    @staticmethod
    def _wm_enforce_files_no_regression(op: Any, old_content: str) -> Dict[str, Any]:
        """Guard: don't let a 'Files & Context' UPDATE drop file paths.

        If the LLM returns UPDATE whose content is missing one or more file
        paths that existed in the old content, reject the UPDATE. If the LLM
        introduced any new paths, surface them as an APPEND; otherwise KEEP.
        """
        if not isinstance(op, dict):
            return {"op": "KEEP"}
        op_name = (op.get("op") or "").upper()
        if op_name != "UPDATE":
            return op

        new_content = (op.get("content") or "").strip()
        old_paths = set(Session._WM_PATH_LIKE_RE.findall(old_content or ""))
        new_paths = set(Session._WM_PATH_LIKE_RE.findall(new_content))
        missing = {p for p in old_paths if p not in new_paths}
        if not missing:
            return op

        added_paths = new_paths - old_paths
        _wm_debug(
            f"guard: 'Files & Context' UPDATE drops {len(missing)} paths "
            f"{sorted(list(missing))[:5]}; forcing KEEP (+ APPEND new paths="
            f"{len(added_paths)})"
        )
        if added_paths:
            # Preserve the old body as-is, then append the genuinely-new items
            # the LLM added (with a short rationale line if we can find one).
            new_items: List[str] = []
            for path in sorted(added_paths):
                # Try to pull the LLM's own phrasing for that path from new_content
                for line in new_content.splitlines():
                    if path in line:
                        new_items.append(line.strip().lstrip("-*").strip())
                        break
                else:
                    new_items.append(f"{path} (newly referenced)")
            return {"op": "APPEND", "items": new_items}
        return {"op": "KEEP"}

    @staticmethod
    def _wm_enforce_title_stability(op: Any, old_content: str) -> Dict[str, Any]:
        """Guard: reject Session Title UPDATE when it drifts too far.

        Heuristic: if the meaningful-word overlap between the old title and
        the proposed new title is 0, treat it as drift and fall back to KEEP.
        This catches the common failure where the LLM rewrites the title each
        round based on the latest delta instead of the overall session scope.
        """
        if not isinstance(op, dict):
            return {"op": "KEEP"}
        op_name = (op.get("op") or "").upper()
        if op_name != "UPDATE":
            return op

        new_content = (op.get("content") or "").strip()

        def meaningful_words(text: str) -> set:
            tokens = re.findall(r"[A-Za-z][A-Za-z0-9\.]{2,}|[\d\.]+", text or "")
            return {
                t.lower() for t in tokens
                if t.lower() not in Session._WM_TITLE_STOPWORDS
            }

        old_w = meaningful_words(old_content)
        new_w = meaningful_words(new_content)

        # If the previous title was empty we have nothing to compare against.
        if not old_w:
            return op
        # If overlap >= 1 meaningful word, accept the rewording.
        if len(old_w & new_w) >= 1:
            return op
        _wm_debug(
            f"guard: Session Title drift rejected "
            f"(old={old_content[:80]!r}, new={new_content[:80]!r}); KEEP"
        )
        return {"op": "KEEP"}

    @staticmethod
    def _wm_enforce_open_issues_resolved(op: Any, old_content: str) -> Dict[str, Any]:
        """Guard: don't let an Open Issues UPDATE silently drop items.

        Any bullet from the old body whose first 40 lowercase chars do not
        appear anywhere in the new content is considered silently dropped.
        We append those items back with a ``[silently dropped, restored]``
        marker so the caller can see the LLM's intent but no information is
        lost.
        """
        if not isinstance(op, dict):
            return op
        op_name = (op.get("op") or "").upper()
        if op_name != "UPDATE":
            return op

        new_content = (op.get("content") or "").strip()
        new_lower = new_content.lower()
        old_items = Session._wm_extract_bullet_items(old_content or "")
        dropped: List[str] = []
        for it in old_items:
            if "[silently dropped, restored]" in it:
                continue
            snippet = it[:40].lower().strip("_* `").strip()
            if snippet and snippet not in new_lower:
                dropped.append(it)
        if not dropped:
            return op

        _wm_debug(
            f"guard: Open Issues UPDATE silently dropped {len(dropped)} "
            f"items; restoring once (will not restore again if re-dropped)"
        )
        restored = "\n".join(
            f"- [silently dropped, restored] {it}" for it in dropped
        )
        merged = (new_content + ("\n" if new_content else "") + restored).strip()
        return {"op": "UPDATE", "content": merged}

    @staticmethod
    def _merge_wm_sections(old_wm: str, ops: Dict[str, Any]) -> str:
        """Merge LLM per-section ops into a new Working Memory document.

        ``ops`` is the schema-validated dict shaped like::

            {"Session Title":  {"op": "KEEP"},
             "Current State":  {"op": "UPDATE", "content": "..."},
             "Open Issues":    {"op": "APPEND", "items": ["...", "..."]}}

        Per-section server-side guards run BEFORE the op is applied:

        - ``Errors & Corrections`` is append-only; UPDATE is demoted to
          APPEND of only-new items.
        - ``Key Facts & Decisions`` uses a fact-preserving dual-threshold
          guard: UPDATE is accepted only if the consolidated content has
          >= 15% of old bullet count AND >= 70% lexical anchor coverage.
          Rejected UPDATEs fall back to APPEND (salvaging new facts)
          or KEEP if no new facts can be extracted.
        - ``Files & Context`` UPDATE that loses old file paths is rejected
          (KEEP + APPEND newly-added paths instead).
        - ``Session Title`` UPDATE with zero meaningful-word overlap against
          the prior title is rejected (KEEP instead).
        - ``Open Issues`` UPDATE that silently drops old items restores them
          with an explicit marker.

        Missing sections or unknown ops default to ``KEEP`` (the schema
        should prevent this, but we stay defensive so a buggy LLM or
        schema-loose backend cannot wipe out the prior WM).
        """
        _wm_debug(
            f"_merge_wm_sections entry old_wm={len(old_wm or '')}B "
            f"sections={list((ops or {}).keys())[:7]}"
        )
        old_sections = Session._parse_wm_sections(old_wm)

        parts: List[str] = ["# Working Memory", ""]
        for header in WM_SEVEN_SECTIONS:
            full_header = f"## {header}"
            op = (ops or {}).get(header)
            old_content = old_sections.get(full_header, "").rstrip()

            # ---------- per-section guards ----------
            if old_content:
                if header == "Session Title":
                    op = Session._wm_enforce_title_stability(op, old_content)
                elif header == "Key Facts & Decisions":
                    op = Session._wm_enforce_key_facts_consolidation(op, old_content)
                elif header in Session._WM_APPEND_ONLY_SECTIONS:
                    op = Session._wm_enforce_append_only(header, op, old_content)
                elif header == "Files & Context":
                    op = Session._wm_enforce_files_no_regression(op, old_content)
                elif header == "Open Issues":
                    op = Session._wm_enforce_open_issues_resolved(op, old_content)
            # ----------------------------------------

            if op is None:
                new_content = old_content
            else:
                op_name = (op.get("op") or "").upper() if isinstance(op, dict) else ""
                if op_name == "KEEP":
                    new_content = old_content
                elif op_name == "UPDATE":
                    new_content = (op.get("content") or "").strip()
                elif op_name == "APPEND":
                    items = op.get("items") or []
                    bad_items = [s for s in items if not isinstance(s, str)]
                    if bad_items:
                        logger.warning(
                            "wm_v2: dropped %d non-string APPEND item(s) in section %r: %s",
                            len(bad_items), header, [type(s).__name__ for s in bad_items],
                        )
                    appended = "\n".join(
                        f"- {s.strip()}" for s in items if isinstance(s, str) and s.strip()
                    )
                    if old_content and appended:
                        new_content = f"{old_content}\n{appended}"
                    else:
                        new_content = old_content or appended
                else:
                    logger.warning(
                        "WM update: unknown op %r for section %r; keeping old content",
                        op,
                        header,
                    )
                    new_content = old_content

            parts.append(full_header)
            if new_content:
                parts.append(new_content)
            parts.append("")

        return "\n".join(parts).rstrip() + "\n"

    def _write_archive(
        self,
        index: int,
        messages: List[Message],
        abstract: str,
        overview: str,
    ) -> None:
        """Write archive to history/archive_N/."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        archive_uri = f"{self._session_uri}/history/archive_{index:03d}"

        # Write messages.jsonl
        lines = [m.to_jsonl() for m in messages]
        run_async(
            viking_fs.write_file(
                uri=f"{archive_uri}/messages.jsonl",
                content="\n".join(lines) + "\n",
                ctx=self.ctx,
            )
        )

        run_async(
            viking_fs.write_file(uri=f"{archive_uri}/.abstract.md", content=abstract, ctx=self.ctx)
        )
        run_async(
            viking_fs.write_file(uri=f"{archive_uri}/.overview.md", content=overview, ctx=self.ctx)
        )

        logger.debug(f"Written archive: {archive_uri}")

    def _write_to_agfs(self, messages: List[Message]) -> None:
        """Write messages.jsonl to AGFS."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        turn_count = len([m for m in messages if m.role == "user"])

        abstract = self._generate_abstract()
        overview = self._generate_overview(turn_count)

        lines = [m.to_jsonl() for m in messages]
        content = "\n".join(lines) + "\n" if lines else ""

        run_async(
            viking_fs.write_file(
                uri=f"{self._session_uri}/messages.jsonl",
                content=content,
                ctx=self.ctx,
            )
        )

        # Update L0/L1
        run_async(
            viking_fs.write_file(
                uri=f"{self._session_uri}/.abstract.md",
                content=abstract,
                ctx=self.ctx,
            )
        )
        run_async(
            viking_fs.write_file(
                uri=f"{self._session_uri}/.overview.md",
                content=overview,
                ctx=self.ctx,
            )
        )

    async def _write_to_agfs_async(self, messages: List[Message]) -> None:
        """Write messages.jsonl to AGFS (async)."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        turn_count = len([m for m in messages if m.role == "user"])

        abstract = self._generate_abstract()
        overview = self._generate_overview(turn_count)

        lines = [m.to_jsonl() for m in messages]
        content = "\n".join(lines) + "\n" if lines else ""

        await viking_fs.write_file(
            uri=f"{self._session_uri}/messages.jsonl",
            content=content,
            ctx=self.ctx,
        )
        await viking_fs.write_file(
            uri=f"{self._session_uri}/.abstract.md",
            content=abstract,
            ctx=self.ctx,
        )
        await viking_fs.write_file(
            uri=f"{self._session_uri}/.overview.md",
            content=overview,
            ctx=self.ctx,
        )

    def _append_to_jsonl(self, msg: Message) -> None:
        """Append to messages.jsonl."""
        if not self._viking_fs:
            return
        run_async(
            self._viking_fs.append_file(
                f"{self._session_uri}/messages.jsonl",
                msg.to_jsonl() + "\n",
                ctx=self.ctx,
            )
        )

    def _update_message_in_jsonl(self) -> None:
        """Update message in messages.jsonl."""
        if not self._viking_fs:
            return

        lines = [m.to_jsonl() for m in self._messages]
        content = "\n".join(lines) + "\n"
        run_async(
            self._viking_fs.write_file(
                f"{self._session_uri}/messages.jsonl",
                content,
                ctx=self.ctx,
            )
        )

    def _save_tool_result(
        self,
        tool_id: str,
        msg: Message,
        output: str,
        status: str,
    ) -> None:
        """Save tool result to tools/{tool_id}/tool.json."""
        if not self._viking_fs:
            return

        tool_part = msg.find_tool_part(tool_id)
        if not tool_part:
            return

        tool_data = {
            "tool_id": tool_id,
            "tool_name": tool_part.tool_name,
            "session_id": self.session_id,
            "input": tool_part.tool_input,
            "output": output,
            "status": status,
            "time": {"created": get_current_timestamp()},
            "duration_ms": tool_part.duration_ms,
            "prompt_tokens": tool_part.prompt_tokens,
            "completion_tokens": tool_part.completion_tokens,
        }
        run_async(
            self._viking_fs.write_file(
                f"{self._session_uri}/tools/{tool_id}/tool.json",
                json.dumps(tool_data, ensure_ascii=False),
                ctx=self.ctx,
            )
        )

    def _generate_abstract(self) -> str:
        """Generate one-sentence summary for session."""
        if not self._messages:
            return ""

        first = self._messages[0].content
        turn_count = self._stats.total_turns
        return f"{turn_count} turns, starting from '{first[:50]}...'"

    def _generate_overview(self, turn_count: int) -> str:
        """Generate session directory structure description."""
        parts = [
            "# Session Directory Structure",
            "",
            "## File Description",
            f"- `messages.jsonl` - Current messages ({turn_count} turns)",
        ]
        if self._compression.compression_index > 0:
            parts.append(
                f"- `history/` - Historical archives ({self._compression.compression_index} total)"
            )
        parts.extend(
            [
                "",
                "## Access Methods",
                f"- Full conversation: `{self._session_uri}`",
            ]
        )
        if self._compression.compression_index > 0:
            parts.append(f"- Historical archives: `{self._session_uri}/history/`")
        return "\n".join(parts)

    def _write_relations(self) -> None:
        """Create relations to used contexts/tools."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        for usage in self._usage_records:
            try:
                run_async(viking_fs.link(self._session_uri, usage.uri, ctx=self.ctx))
                logger.debug(f"Created relation: {self._session_uri} -> {usage.uri}")
            except Exception as e:
                logger.warning(f"Failed to create relation to {usage.uri}: {e}")

    async def _write_relations_async(self) -> None:
        """Create relations to used contexts/tools (async)."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        for usage in self._usage_records:
            try:
                await viking_fs.link(self._session_uri, usage.uri, ctx=self.ctx)
                logger.debug(f"Created relation: {self._session_uri} -> {usage.uri}")
            except Exception as e:
                logger.warning(f"Failed to create relation to {usage.uri}: {e}")

    # ============= Properties =============

    @property
    def uri(self) -> str:
        """Session's Viking URI."""
        return self._session_uri

    @property
    def summary(self) -> str:
        """Compression summary."""
        return self._compression.summary

    @property
    def compression(self) -> SessionCompression:
        """Get compression information."""
        return self._compression

    @property
    def usage_records(self) -> List[Usage]:
        """Get usage records."""
        return self._usage_records

    @property
    def stats(self) -> SessionStats:
        """Get session statistics."""
        return self._stats

    def __repr__(self) -> str:
        return f"Session(user={self.user}, id={self.session_id})"
