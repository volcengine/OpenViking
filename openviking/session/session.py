# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Session management for OpenViking.

Session as Context: Sessions integrated into L0/L1/L2 system.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from openviking.message import Message, Part
from openviking.server.identity import RequestContext, Role
from openviking.telemetry import get_current_telemetry
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger, run_async
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.session.compressor import SessionCompressor
    from openviking.storage import VikingDBManager
    from openviking.storage.viking_fs import VikingFS

logger = get_logger(__name__)


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
    message_count: int = 0
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "commit_count": self.commit_count,
            "memories_extracted": dict(self.memories_extracted),
            "last_commit_at": self.last_commit_at,
            "llm_token_usage": dict(self.llm_token_usage),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMeta":
        token_usage = data.get("llm_token_usage", {})
        memories = data.get("memories_extracted", {})
        return cls(
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            message_count=data.get("message_count", 0),
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
                "prompt_tokens": token_usage.get("prompt_tokens", 0),
                "completion_tokens": token_usage.get("completion_tokens", 0),
                "total_tokens": token_usage.get("total_tokens", 0),
            },
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
        self.created_at = datetime.now()
        self._auto_commit_threshold = auto_commit_threshold
        self._session_uri = f"viking://session/{self.user.user_space_name()}/{self.session_id}"

        self._messages: List[Message] = []
        self._usage_records: List[Usage] = []
        self._compression: SessionCompression = SessionCompression()
        self._stats: SessionStats = SessionStats()
        self._meta = SessionMeta(session_id=self.session_id, created_at=get_current_timestamp())
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

        self._loaded = True

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

    def add_message(
        self,
        role: str,
        parts: List[Part],
    ) -> Message:
        """Add a message."""
        msg = Message(
            id=f"msg_{uuid4().hex}",
            role=role,
            parts=parts,
            created_at=datetime.now(),
        )
        self._messages.append(msg)

        # Update statistics
        if role == "user":
            self._stats.total_turns += 1
        self._stats.total_tokens += len(msg.content) // 4

        self._append_to_jsonl(msg)

        self._meta.message_count = len(self._messages)
        self._save_meta_sync()
        return msg

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

    def commit(self) -> Dict[str, Any]:
        """Sync wrapper for commit_async()."""
        return run_async(self.commit_async())

    async def commit_async(self) -> Dict[str, Any]:
        """Async commit session: archive immediately, extract memories in background.

        Phase 1 (Archive prep, PathLock-protected): Copy messages, clear live
        session, increment compression index. Uses a distributed filesystem lock
        (PathLock) so this works across workers and processes.
        Phase 2 (Memory extraction): Always runs in background via asyncio.create_task().

        Returns a task_id for tracking Phase 2 progress.
        """
        from openviking.service.task_tracker import get_task_tracker
        from openviking.storage.transaction import LockContext, get_lock_manager

        # ===== Phase 1: Snapshot + clear (PathLock-protected) =====
        # Fast pre-check: skip lock entirely if no messages (common case avoids
        # unnecessary filesystem lock acquisition).
        if not self._messages:
            get_current_telemetry().set("memory.extracted", 0)
            return {
                "session_id": self.session_id,
                "status": "accepted",
                "task_id": None,
                "archive_uri": None,
                "archived": False,
            }

        # Use filesystem-based distributed lock so this works across workers/processes.
        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        async with LockContext(get_lock_manager(), [session_path], lock_mode="point"):
            # Authoritative check under lock: handles the race where two concurrent
            # callers both passed the pre-check but only the first should archive.
            if not self._messages:
                get_current_telemetry().set("memory.extracted", 0)
                return {
                    "session_id": self.session_id,
                    "status": "accepted",
                    "task_id": None,
                    "archive_uri": None,
                    "archived": False,
                }

            self._compression.compression_index += 1
            messages_to_archive = self._messages.copy()
            self._messages.clear()

            try:
                await self._write_to_agfs_async(messages=[])
            except Exception:
                # Rollback: restore messages so they aren't lost
                self._messages.extend(messages_to_archive)
                self._compression.compression_index -= 1
                raise
        # Lock released — live session is now clean.
        # Any add_message() from here appends to the fresh empty list.

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

        self._meta.message_count = 0
        await self._save_meta()

        self._compression.original_count += len(messages_to_archive)
        logger.info(
            f"Archived: {len(messages_to_archive)} messages → "
            f"history/archive_{self._compression.compression_index:03d}/"
        )

        # Snapshot mutable state for Phase 2
        usage_snapshot = self._usage_records.copy()
        first_message_id = messages_to_archive[0].id if messages_to_archive else ""
        last_message_id = messages_to_archive[-1].id if messages_to_archive else ""

        # Create TaskRecord for tracking Phase 2
        tracker = get_task_tracker()
        task = tracker.create("session_commit", resource_id=self.session_id)

        asyncio.create_task(
            self._run_memory_extraction(
                task_id=task.task_id,
                archive_uri=archive_uri,
                messages=messages_to_archive,
                usage_records=usage_snapshot,
                first_message_id=first_message_id,
                last_message_id=last_message_id,
            )
        )

        return {
            "session_id": self.session_id,
            "status": "accepted",
            "task_id": task.task_id,
            "archive_uri": archive_uri,
            "archived": True,
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

        tracker = get_task_tracker()
        tracker.start(task_id)

        memories_extracted: Dict[str, int] = {}
        active_count_updated = 0
        telemetry = OperationTelemetry(operation="session_commit_phase2", enabled=True)

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

                # Memory extraction
                if self._session_compressor:
                    logger.info(
                        f"Starting memory extraction from {len(messages)} archived messages"
                    )
                    extracted = await self._session_compressor.extract_long_term_memories(
                        messages=messages,
                        user=self.user,
                        session_id=self.session_id,
                        ctx=self.ctx,
                        latest_archive_overview=latest_archive_overview,
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

            # Phase 2 complete — update meta with telemetry and commit info
            snapshot = telemetry.finish("ok")
            if snapshot:
                llm = snapshot.summary.get("tokens", {}).get("llm", {})
                self._meta.llm_token_usage["prompt_tokens"] += llm.get("input", 0)
                self._meta.llm_token_usage["completion_tokens"] += llm.get("output", 0)
                self._meta.llm_token_usage["total_tokens"] += llm.get("total", 0)
            self._meta.commit_count = self._compression.compression_index
            for cat, count in memories_extracted.items():
                self._meta.memories_extracted[cat] = (
                    self._meta.memories_extracted.get(cat, 0) + count
                )
                self._meta.memories_extracted["total"] = (
                    self._meta.memories_extracted.get("total", 0) + count
                )
            self._meta.last_commit_at = get_current_timestamp()
            self._meta.message_count = len(self._messages)
            await self._save_meta()

            # Write .done file last — signals that all state is finalized
            await self._write_done_file(archive_uri, first_message_id, last_message_id)

            tracker.complete(
                task_id,
                {
                    "session_id": self.session_id,
                    "archive_uri": archive_uri,
                    "memories_extracted": memories_extracted,
                    "active_count_updated": active_count_updated,
                },
            )
            logger.info(f"Session {self.session_id} memory extraction completed")
        except Exception as e:
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

    async def get_context_for_search(self, query: str, max_messages: int = 20) -> Dict[str, Any]:
        """Get session context for intent analysis.

        Args:
            query: Query string for the current request.
            max_messages: Maximum number of current messages to retrieve (default 20)

        Returns:
            - latest_archive_overview: Latest completed archive overview, if any
            - current_messages: Current message list (List[Message])
        """
        del query  # Current query no longer affects historical archive selection.

        current_messages = list(self._messages[-max_messages:]) if self._messages else []
        latest_archive_overview = await self._get_latest_completed_archive_overview()

        return {
            "latest_archive_overview": latest_archive_overview,
            "current_messages": current_messages,
        }

    # ============= Internal methods =============

    async def _get_latest_completed_archive_overview(
        self,
        exclude_archive_uri: Optional[str] = None,
    ) -> str:
        """Return the newest completed archive overview, skipping incomplete archives."""
        if not self._viking_fs or self.compression.compression_index <= 0:
            return ""

        try:
            history_items = await self._viking_fs.ls(f"{self._session_uri}/history", ctx=self.ctx)
        except Exception:
            return ""

        archive_names: List[str] = []
        for item in history_items:
            name = item.get("name") if isinstance(item, dict) else item
            if name and name.startswith("archive_"):
                archive_names.append(name)

        def _archive_index(name: str) -> int:
            try:
                return int(name.split("_")[1])
            except Exception:
                return -1

        exclude = exclude_archive_uri.rstrip("/") if exclude_archive_uri else None
        for name in sorted(archive_names, key=_archive_index, reverse=True):
            archive_uri = f"{self._session_uri}/history/{name}"
            if exclude and archive_uri == exclude:
                continue
            try:
                await self._viking_fs.read_file(f"{archive_uri}/.done", ctx=self.ctx)
                overview = await self._viking_fs.read_file(
                    f"{archive_uri}/.overview.md",
                    ctx=self.ctx,
                )
                if overview:
                    return overview
            except Exception:
                continue

        return ""

    def _extract_abstract_from_summary(self, summary: str) -> str:
        """Extract one-sentence overview from structured summary."""
        if not summary:
            return ""

        match = re.search(r"^\*\*[^*]+\*\*:\s*(.+)$", summary, re.MULTILINE)
        if match:
            return match.group(1).strip()

        first_line = summary.split("\n")[0].strip()
        return first_line if first_line else ""

    def _generate_archive_summary(
        self,
        messages: List[Message],
        latest_archive_overview: str = "",
    ) -> str:
        """Generate structured summary for archive."""
        if not messages:
            return ""

        formatted = "\n".join([f"[{m.role}]: {m.content}" for m in messages])

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
        """Generate structured summary for archive (async)."""
        if not messages:
            return ""

        formatted = "\n".join([f"[{m.role}]: {m.content}" for m in messages])

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
                return await vlm.get_completion_async(prompt)
            except Exception as e:
                logger.warning(f"LLM summary failed: {e}")

        turn_count = len([m for m in messages if m.role == "user"])
        return f"# Session Summary\n\n**Overview**: {turn_count} turns, {len(messages)} messages"

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
