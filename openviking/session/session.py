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
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Literal, Optional
from uuid import uuid4

from openviking.core.namespace import canonical_session_uri
from openviking.core.peer_id import normalize_peer_id, safe_peer_id
from openviking.message import Message, Part
from openviking.message.part import ContextPart, TextPart, ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.constants import EXECUTION_MEMORY_TYPES
from openviking.session.memory_policy import MemoryPolicy
from openviking.session.retention import (
    RETENTION_MODE_TURN_BUDGET,
    RetentionPlan,
    build_turns,
    fit_active_messages_to_budget,
    is_user_query,
    plan_retention,
)
from openviking.session.tool_result_store import (
    ToolResultStore,
    build_tool_result_id,
    make_preview,
    render_preview_from_synopsis,
    sha256_text,
)
from openviking.session.tool_result_synopsis import (
    ToolResultSynopsis,
    generate_tool_result_synopsis,
)
from openviking.telemetry import get_current_telemetry, tracer
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.model_retry import is_retryable_api_error, retry_async
from openviking.utils.time_utils import get_current_timestamp
from openviking.utils.token_estimation import estimate_text_tokens, truncate_text_to_token_budget
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger, run_async
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.session.compressor_v2 import SessionCompressorV2 as SessionCompressor
    from openviking.storage import VikingDBManager
    from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
    from openviking.storage.viking_fs import VikingFS
    from openviking.usage_reporter import UsageReporter

logger = get_logger(__name__)

_ARCHIVE_WAIT_POLL_SECONDS = 0.1
_PHASE2_QUEUE_WAIT_TIMEOUT_SECONDS = 1800.0
_MEMORY_EXTRACTION_MAX_RETRIES = 3
_MEMORY_EXTRACTION_RETRY_BASE_DELAY_SECONDS = 1.0
_MEMORY_EXTRACTION_RETRY_MAX_DELAY_SECONDS = 8.0
_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS = 30.0
_MEMORY_STEP_NAMES = ("long_term", "execution")


def _wm_debug(msg: str) -> None:
    """Log a WM v2 debug message via the standard logger."""
    logger.debug("wm_v2: %s", msg)


def _enabled_memory_types() -> set[str]:
    """Return enabled memory type names registered for extraction."""
    from openviking.session.memory.memory_type_registry import MemoryTypeRegistry

    return set(MemoryTypeRegistry().list_names(include_disabled=False))


def _validate_memory_policy_types(policy: MemoryPolicy) -> None:
    if policy.memory_types is None:
        return
    policy.validate_memory_types(_enabled_memory_types())


def _split_policy_memory_types(
    memory_types: Optional[set[str]],
) -> tuple[Optional[set[str]], Optional[set[str]]]:
    if memory_types is None:
        return None, None
    return memory_types - EXECUTION_MEMORY_TYPES, memory_types & EXECUTION_MEMORY_TYPES


def _default_memory_counts() -> Dict[str, int]:
    return {"total": 0}


def _message_peer_ids(messages: List[Message]) -> set[str]:
    return {
        peer_id
        for message in messages
        if (peer_id := safe_peer_id(getattr(message, "peer_id", None)))
    }


@dataclass(frozen=True)
class _MemoryExtractionScope:
    allow_self_memory: bool
    allowed_peer_ids: set[str]
    include_session_skills: bool
    memory_types: Optional[set[str]]


def _resolve_memory_extraction_scope(
    ctx: RequestContext,
    policy: MemoryPolicy,
    messages: List[Message],
    *,
    config_session_skill_extraction_enabled: bool,
) -> _MemoryExtractionScope:
    allow_self_memory = policy.self_enabled
    allowed_peer_ids = _message_peer_ids(messages) if policy.peer_enabled else set()

    return _MemoryExtractionScope(
        allow_self_memory=allow_self_memory,
        allowed_peer_ids=allowed_peer_ids,
        include_session_skills=config_session_skill_extraction_enabled and allow_self_memory,
        memory_types=policy.memory_types,
    )


# =====================================================================
# Working Memory v2
# ---------------------------------------------------------------------
# Phase 2 of a commit generates / updates a structured 7-section Working
# Memory document stored at archive_NNN/.overview.md.
#
# First commit: call `compression.ov_wm_v2` with a plain completion unless
# partial-Turn retention also needs checkpoint summaries. In that case the
# same call uses `create_working_memory` and returns both products.
# Subsequent commits: call `compression.ov_wm_v2_update` with the
# `update_working_memory` tool to get a per-section decision plus any requested
# checkpoint summaries, then let the server do section-level merge against the
# previous WM.
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
                    "properties": dict.fromkeys(WM_SEVEN_SECTIONS, _WM_SECTION_OP_SCHEMA),
                },
                "checkpoint_summaries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "When checkpoint sources are present, one concise continuation "
                        "summary per checkpoint_source index, in ascending index order."
                    ),
                },
            },
        },
    },
}

WM_CREATE_WITH_CHECKPOINTS_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "create_working_memory",
        "description": (
            "Create the complete Working Memory and the requested checkpoint summaries "
            "from the same model pass."
        ),
        "parameters": {
            "type": "object",
            "required": ["working_memory", "checkpoint_summaries"],
            "additionalProperties": False,
            "properties": {
                "working_memory": {
                    "type": "string",
                    "description": "Complete 7-section Working Memory markdown.",
                },
                "checkpoint_summaries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One concise continuation summary per checkpoint_source index, "
                        "in ascending index order."
                    ),
                },
            },
        },
    },
}


@dataclass(frozen=True)
class _CheckpointRequest:
    """Server-owned mapping for one checkpoint summary requested from Phase 2."""

    turn_anchor_message_id: str
    source_message_ids: tuple[str, ...]
    retained_message_token_budget: int
    estimated_active_tokens: int


@dataclass(frozen=True)
class _ArchiveSummaryResult:
    """The two products emitted by the existing Working-Memory model call."""

    overview: str
    checkpoint_summaries: tuple[str, ...] = ()


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
class ArchiveState:
    """Filesystem-derived state for one archive directory."""

    archive_id: str
    archive_uri: str
    index: int
    state: Literal["pending", "completed", "failed"]
    overview: str = ""
    done: Dict[str, Any] = field(default_factory=dict)
    failed: Dict[str, Any] = field(default_factory=dict)

    @property
    def coverage_start_index(self) -> int:
        raw = self.done.get("coverage_start_archive")
        if isinstance(raw, str):
            match = re.fullmatch(r"archive_(\d+)", raw)
            if match:
                return int(match.group(1))
        return self.index

    @property
    def coverage_end_index(self) -> int:
        raw = self.done.get("coverage_end_archive")
        if isinstance(raw, str):
            match = re.fullmatch(r"archive_(\d+)", raw)
            if match:
                return int(match.group(1))
        return self.index


@dataclass
class SessionMeta:
    """Session metadata persisted in .meta.json."""

    session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    created_by_account_id: str = ""
    created_by_user_id: str = ""
    message_count: int = 0
    total_message_count: Optional[int] = 0
    commit_count: int = 0
    memories_extracted: Dict[str, int] = field(default_factory=_default_memory_counts)
    last_commit_at: str = ""
    llm_token_usage: Dict[str, int] = field(
        default_factory=lambda: {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
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
    # Opt-in Turn-aware retention. Empty mode preserves the physical-message
    # keep_recent_count behavior for existing integrations.
    retention_mode: str = ""
    keep_recent_turn_count: int = 0
    retained_message_token_budget: int = 0
    min_raw_tail_steps: int = 1
    memory_policy: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by_account_id": self.created_by_account_id,
            "created_by_user_id": self.created_by_user_id,
            "message_count": self.message_count,
            "commit_count": self.commit_count,
            "memories_extracted": dict(self.memories_extracted),
            "last_commit_at": self.last_commit_at,
            "llm_token_usage": dict(self.llm_token_usage),
            "embedding_token_usage": dict(self.embedding_token_usage),
            "pending_tokens": self.pending_tokens,
            "keep_recent_count": self.keep_recent_count,
            "retention_mode": self.retention_mode,
            "keep_recent_turn_count": self.keep_recent_turn_count,
            "retained_message_token_budget": self.retained_message_token_budget,
            "min_raw_tail_steps": self.min_raw_tail_steps,
            "memory_policy": dict(self.memory_policy) if self.memory_policy is not None else None,
        }
        if self.total_message_count is not None:
            data["total_message_count"] = self.total_message_count
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionMeta":
        llm_token_usage = data.get("llm_token_usage", {})
        embedding_token_usage = data.get("embedding_token_usage", {})
        memories = data.get("memories_extracted", {})

        memory_counts = _default_memory_counts()
        for key, value in memories.items():
            try:
                memory_counts[key] = int(value or 0)
            except (TypeError, ValueError):
                memory_counts[key] = 0

        return cls(
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            created_by_account_id=data.get("created_by_account_id", "")
            or data.get("account_id", ""),
            created_by_user_id=data.get("created_by_user_id", ""),
            message_count=data.get("message_count", 0),
            total_message_count=data.get("total_message_count"),
            commit_count=data.get("commit_count", 0),
            memories_extracted=memory_counts,
            last_commit_at=data.get("last_commit_at", ""),
            llm_token_usage={
                "prompt_tokens": llm_token_usage.get("prompt_tokens", 0),
                "completion_tokens": llm_token_usage.get("completion_tokens", 0),
                "total_tokens": llm_token_usage.get("total_tokens", 0),
                "cached_tokens": llm_token_usage.get("cached_tokens", 0),
                "reasoning_tokens": llm_token_usage.get("reasoning_tokens", 0),
            },
            embedding_token_usage={
                "total_tokens": embedding_token_usage.get("total_tokens", 0),
            },
            pending_tokens=max(0, int(data.get("pending_tokens", 0) or 0)),
            keep_recent_count=max(0, int(data.get("keep_recent_count", 0) or 0)),
            retention_mode=str(data.get("retention_mode", "") or ""),
            keep_recent_turn_count=max(0, int(data.get("keep_recent_turn_count", 0) or 0)),
            retained_message_token_budget=max(
                0, int(data.get("retained_message_token_budget", 0) or 0)
            ),
            min_raw_tail_steps=max(0, int(data.get("min_raw_tail_steps", 1) or 0)),
            memory_policy=data.get("memory_policy"),
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
        session_uri: Optional[str] = None,
        auto_commit_threshold: int = 8000,
        tool_output_externalization_config: Optional[ToolOutputExternalizationConfig] = None,
        usage_reporter: Optional["UsageReporter"] = None,
    ):
        self._viking_fs = viking_fs
        self._vikingdb_manager = vikingdb_manager
        self._session_compressor = session_compressor
        self.user = user or UserIdentifier.the_default_user()
        self.ctx = ctx or RequestContext(user=self.user, role=Role.ROOT)
        self.session_id = (
            session_id or f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex[:16]}"
        )
        self.created_at = int(datetime.now(timezone.utc).timestamp() * 1000)
        self._auto_commit_threshold = auto_commit_threshold
        self._session_uri = session_uri or canonical_session_uri(self.ctx, self.session_id)

        self._messages: List[Message] = []
        self._usage_records: List[Usage] = []
        self._archive_meta_merge_lock = asyncio.Lock()
        self._compression: SessionCompression = SessionCompression()
        self._stats: SessionStats = SessionStats()
        self._meta = SessionMeta(
            session_id=self.session_id,
            created_at=get_current_timestamp(),
            created_by_account_id=self.ctx.account_id,
            created_by_user_id=self.ctx.user.user_id,
        )
        self._loaded = False
        self._tool_output_externalization_config = (
            tool_output_externalization_config.model_copy(deep=True)
            if tool_output_externalization_config is not None
            else ToolOutputExternalizationConfig()
        )
        self._usage_reporter = usage_reporter

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

        if not self._meta.created_by_account_id:
            self._meta.created_by_account_id = self.ctx.account_id
        if not self._meta.created_by_user_id:
            self._meta.created_by_user_id = self.ctx.user.user_id
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
        if (
            self._meta.retention_mode == RETENTION_MODE_TURN_BUDGET
            and self._meta.keep_recent_turn_count > 0
            and self._meta.retained_message_token_budget > 0
        ):
            plan = plan_retention(
                self._messages,
                keep_recent_turn_count=self._meta.keep_recent_turn_count,
                token_budget=self._meta.retained_message_token_budget,
                min_raw_tail_steps=self._meta.min_raw_tail_steps,
            )
            retained_ids = {message.id for message in plan.retained_messages}
            self._meta.pending_tokens = sum(
                int(message.estimated_tokens or 0)
                for message in plan.archive_messages
                if message.id not in retained_ids
            )
            self._meta.pending_tokens = max(0, self._meta.pending_tokens)
            return

        keep = max(0, int(self._meta.keep_recent_count or 0))
        total = len(self._messages)
        if keep <= 0:
            self._meta.pending_tokens = sum(int(m.estimated_tokens or 0) for m in self._messages)
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

    def _tool_result_store(self) -> Optional[ToolResultStore]:
        if not self._viking_fs:
            return None
        return ToolResultStore(
            self._viking_fs,
            self._session_uri,
            self.session_id,
            self.ctx,
        )

    async def _hydrate_tool_outputs_for_extraction(
        self,
        messages: List[Message],
    ) -> List[Message]:
        """Return a memory-only copy with externalized tool outputs restored."""
        hydrated = [Message.from_dict(m.to_dict()) for m in messages]
        store = self._tool_result_store()
        if not store:
            return hydrated

        for msg in hydrated:
            for part in msg.parts:
                if not isinstance(part, ToolPart):
                    continue
                if not part.tool_output_ref:
                    continue
                if not (part.tool_output_truncated or part.tool_output_source_ref):
                    continue

                ref = part.tool_output_source_ref or part.tool_output_ref
                tool_result_id = ref.rstrip("/").split("/")[-1]
                offset = part.tool_output_source_offset if part.tool_output_source_ref else 0
                limit = part.tool_output_source_limit if part.tool_output_source_ref else -1
                if (
                    part.tool_output_source_ref
                    and limit is None
                    and part.tool_output_original_chars is not None
                ):
                    limit = part.tool_output_original_chars
                try:
                    result = await store.read(
                        tool_result_id,
                        offset=max(0, int(offset or 0)),
                        limit=int(limit) if limit is not None else -1,
                        include_metadata=False,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to hydrate externalized tool output for extraction: "
                        "session=%s message_id=%s tool_id=%s ref=%s error=%s",
                        self.session_id,
                        msg.id,
                        part.tool_id,
                        ref,
                        exc,
                    )
                    continue
                part.tool_output = result.get("content", "")

        return hydrated

    def _effective_tool_preview_chars(
        self,
        cfg: ToolOutputExternalizationConfig,
        externalized_count: int,
    ) -> int:
        if externalized_count <= 0:
            return cfg.preview_chars
        group_share = cfg.assistant_turn_preview_budget_chars // externalized_count
        return max(0, min(cfg.preview_chars, max(cfg.min_preview_chars, group_share)))

    def _rewrite_source_read_tool_output(
        self,
        part: ToolPart,
        cfg: ToolOutputExternalizationConfig,
        *,
        group_id: str,
        group_original_chars: int,
    ) -> bool:
        """Rewrite read-back tool output as a source reference, not a new result."""
        if part.tool_name != "openviking_tool_result_read":
            return False
        tool_input = part.tool_input if isinstance(part.tool_input, dict) else {}
        source_ref = str(
            tool_input.get("tool_output_ref")
            or tool_input.get("ref")
            or tool_input.get("uri")
            or ""
        )
        if not source_ref.startswith(f"{self._session_uri}/tool-results/"):
            return False

        output = part.tool_output or ""
        preview_chars = max(cfg.min_preview_chars, cfg.preview_chars)
        preview = make_preview(
            output,
            preview_chars=preview_chars,
            ref=source_ref,
            tool_name=part.tool_name,
            sha256=sha256_text(output) if output else "",
            reason="source_read",
            original_chars=len(output),
            mime_type=part.tool_output_mime_type or "text/plain",
        )
        part.tool_output = preview
        part.tool_output_ref = source_ref
        part.tool_output_truncated = len(output) > len(preview)
        part.tool_output_original_chars = len(output)
        part.tool_output_preview_chars = len(preview)
        part.tool_output_sha256 = sha256_text(output) if output else ""
        part.tool_output_storage_uri = source_ref
        part.tool_output_source_ref = source_ref
        part.tool_output_source_offset = tool_input.get("offset")
        part.tool_output_source_limit = tool_input.get("limit")
        part.tool_output_group_id = group_id
        part.tool_output_externalized_reason = "source_read"
        part.tool_output_group_original_chars = group_original_chars
        part.tool_output_group_budget_chars = cfg.assistant_turn_inline_budget_chars
        return True

    def _externalize_tool_part(
        self,
        msg: Message,
        part: ToolPart,
        cfg: ToolOutputExternalizationConfig,
        *,
        preview_chars: int,
        reason: str,
        group_id: str,
        group_original_chars: int,
        synopsis: Optional[ToolResultSynopsis] = None,
    ) -> None:
        store = self._tool_result_store()
        original_output = part.tool_output or ""
        if not store or not original_output:
            return

        digest = sha256_text(original_output)
        try:
            stored = run_async(
                store.write(
                    content=original_output,
                    tool_id=part.tool_id,
                    tool_name=part.tool_name,
                    message_id=msg.id,
                    user_id=self.ctx.user.user_id if self.ctx and self.ctx.user else None,
                    peer_id=msg.peer_id,
                    created_at=msg.created_at,
                    preview_chars=preview_chars,
                    mime_type=part.tool_output_mime_type or "text/plain",
                    synopsis=synopsis,
                )
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            part.tool_output_externalization_error = error
            if cfg.failure_mode == "reject":
                raise FailedPreconditionError(
                    "Failed to externalize tool output",
                    details={"tool_id": part.tool_id, "error": error},
                ) from exc
            if cfg.failure_mode == "preview_only":
                part.tool_output = make_preview(
                    original_output,
                    preview_chars=preview_chars,
                    tool_name=part.tool_name,
                    sha256=digest,
                    reason=f"{reason}:externalization_failed",
                    original_chars=len(original_output),
                    mime_type=part.tool_output_mime_type or "text/plain",
                )
                part.tool_output_ref = ""
                part.tool_output_truncated = True
                part.tool_output_original_chars = len(original_output)
                part.tool_output_preview_chars = len(part.tool_output)
                part.tool_output_sha256 = digest
                part.tool_output_externalized_reason = reason
            return

        ref = stored.storage_uri
        part.tool_output = render_preview_from_synopsis(
            stored.synopsis,
            ref=ref,
            tool_name=part.tool_name,
            sha256=digest,
            reason=reason,
            original_chars=len(original_output),
            preview_chars=min(len(original_output), max(preview_chars, 0)),
        )
        part.tool_output_ref = ref
        part.tool_output_truncated = True
        part.tool_output_original_chars = len(original_output)
        part.tool_output_preview_chars = len(part.tool_output)
        part.tool_output_sha256 = digest
        part.tool_output_storage_uri = ref
        part.tool_output_mime_type = stored.metadata.get("mime_type", "text/plain")
        part.tool_output_group_id = group_id
        part.tool_output_externalized_reason = reason
        part.tool_output_group_original_chars = group_original_chars
        part.tool_output_group_budget_chars = cfg.assistant_turn_inline_budget_chars

    def _externalize_large_tool_output_group(self, messages: List[Message]) -> None:
        cfg = self._tool_output_externalization_config
        if not cfg.enabled:
            return

        tool_parts = [
            (msg, p)
            for msg in messages
            for p in msg.parts
            if isinstance(p, ToolPart) and (p.tool_output or "")
        ]
        if not tool_parts:
            return

        group_id = messages[0].id
        group_original_chars = sum(
            (
                int(p.tool_output_original_chars)
                if p.tool_output_ref
                and p.tool_output_truncated
                and p.tool_output_original_chars is not None
                else len(p.tool_output or "")
            )
            for _, p in tool_parts
        )
        normal_indices: List[int] = []
        selected: set[int] = set()
        externalized_preview_cache: Dict[tuple[int, int, str], tuple[ToolResultSynopsis, int]] = {}

        for idx, (_msg, part) in enumerate(tool_parts):
            part.tool_output_group_id = group_id
            part.tool_output_group_original_chars = group_original_chars
            part.tool_output_group_budget_chars = cfg.assistant_turn_inline_budget_chars
            if self._rewrite_source_read_tool_output(
                part,
                cfg,
                group_id=group_id,
                group_original_chars=group_original_chars,
            ):
                continue
            if part.tool_output_ref and part.tool_output_truncated:
                continue
            normal_indices.append(idx)
            if len(part.tool_output or "") > cfg.threshold_chars:
                selected.add(idx)

        def prepared_externalized_preview(
            idx: int, part: ToolPart, preview_chars: int
        ) -> tuple[ToolResultSynopsis, int]:
            content = part.tool_output or ""
            reason = "single_threshold" if len(content) > cfg.threshold_chars else "turn_budget"
            cache_key = (idx, preview_chars, reason)
            cached = externalized_preview_cache.get(cache_key)
            if cached is not None:
                return cached

            synopsis = generate_tool_result_synopsis(
                content,
                preview_chars=preview_chars,
                tool_name=part.tool_name,
                mime_type=part.tool_output_mime_type or "text/plain",
            )
            digest = sha256_text(content)
            ref = f"{self._session_uri}/tool-results/{build_tool_result_id(part.tool_id, digest)}"
            rendered = render_preview_from_synopsis(
                synopsis,
                ref=ref,
                tool_name=part.tool_name,
                sha256=digest,
                reason=reason,
                original_chars=len(content),
                preview_chars=min(len(content), max(preview_chars, 0)),
            )
            prepared = (synopsis, len(rendered))
            externalized_preview_cache[cache_key] = prepared
            return prepared

        def projected_inline_chars(selected_indices: set[int]) -> int:
            preview_chars = self._effective_tool_preview_chars(cfg, len(selected_indices))
            total = 0
            for idx, (_, part) in enumerate(tool_parts):
                output_len = len(part.tool_output or "")
                if idx in selected_indices:
                    _synopsis, rendered_len = prepared_externalized_preview(
                        idx, part, preview_chars
                    )
                    total += rendered_len
                else:
                    total += output_len
            return total

        remaining = sorted(
            [idx for idx in normal_indices if idx not in selected],
            key=lambda idx: len(tool_parts[idx][1].tool_output or ""),
            reverse=True,
        )
        while (
            projected_inline_chars(selected) >= cfg.assistant_turn_inline_budget_chars and remaining
        ):
            baseline = projected_inline_chars(selected)
            chosen_pos = None
            for pos, idx in enumerate(remaining):
                candidate = set(selected)
                candidate.add(idx)
                if projected_inline_chars(candidate) < baseline:
                    chosen_pos = pos
                    break
            if chosen_pos is None:
                break
            selected.add(remaining.pop(chosen_pos))

        preview_chars = self._effective_tool_preview_chars(cfg, len(selected))
        for idx in sorted(selected):
            msg, part = tool_parts[idx]
            reason = (
                "single_threshold"
                if len(part.tool_output or "") > cfg.threshold_chars
                else "turn_budget"
            )
            synopsis, _rendered_len = prepared_externalized_preview(idx, part, preview_chars)
            self._externalize_tool_part(
                msg,
                part,
                cfg,
                preview_chars=preview_chars,
                reason=reason,
                group_id=group_id,
                group_original_chars=group_original_chars,
                synopsis=synopsis,
            )

    def _externalize_large_tool_outputs(self, msg: Message) -> None:
        self._externalize_large_tool_output_group([msg])

    def _is_tool_result_aggregate(self, role: str, parts: List[Part]) -> bool:
        return (
            role == "user" and len(parts) > 1 and all(isinstance(part, ToolPart) for part in parts)
        )

    def _append_messages(self, messages: List[Message]) -> None:
        """Append messages through the same authoritative lock as commit Phase 1."""
        run_async(self._append_messages_authoritatively(messages))

    async def _append_messages_authoritatively(self, messages: List[Message]) -> None:
        """Reload and append under the session path lock.

        Different workers can hold stale Session objects. Without sharing the
        commit lock, an append between commit's root read and root rewrite can
        be overwritten even though add_message already returned successfully.
        """
        if not messages:
            return
        if not self._viking_fs:
            self._apply_appended_messages_to_state(messages)
            return

        uri_to_path = getattr(self._viking_fs, "_uri_to_path", None)
        if not callable(uri_to_path):
            # Minimal/embedded VikingFS implementations predate transaction
            # locks. Preserve their existing append contract; production
            # VikingFS always takes the authoritative path-lock branch below.
            await self._append_messages_without_path_lock(messages)
            return

        from openviking.storage.transaction import LockContext, get_lock_manager

        session_path = uri_to_path(self._session_uri, ctx=self.ctx)
        async with LockContext(
            get_lock_manager(),
            [session_path],
            lock_mode="exact",
            timeout=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS,
        ):
            self._messages = await self._read_live_messages_strict()
            in_memory_meta = self._meta
            try:
                meta_content = await self._viking_fs.read_file(
                    f"{self._session_uri}/.meta.json",
                    ctx=self.ctx,
                )
                self._meta = SessionMeta.from_dict(json.loads(meta_content))
            except Exception:
                # Legacy/malformed metadata must not prevent an otherwise safe
                # append. Message correctness remains rooted in messages.jsonl.
                self._meta = in_memory_meta

            self._apply_appended_messages_to_state(messages)
            batch_content = "".join(message.to_jsonl() + "\n" for message in messages)
            await self._viking_fs.append_file(
                f"{self._session_uri}/messages.jsonl",
                batch_content,
                ctx=self.ctx,
            )
            await self._save_meta()

    async def _append_messages_without_path_lock(self, messages: List[Message]) -> None:
        """Compatibility append for storage adapters without path locking."""
        from openviking_cli.exceptions import NotFoundError

        try:
            self._messages = await self._read_live_messages_strict()
        except (FileNotFoundError, NotFoundError):
            # A fresh lightweight adapter may not materialize messages.jsonl
            # until its first append.
            pass

        in_memory_meta = self._meta
        try:
            meta_content = await self._viking_fs.read_file(
                f"{self._session_uri}/.meta.json",
                ctx=self.ctx,
            )
            self._meta = SessionMeta.from_dict(json.loads(meta_content))
        except Exception:
            # Keep the in-memory legacy metadata if the lightweight adapter
            # has no metadata file or cannot decode an older one.
            self._meta = in_memory_meta

        self._apply_appended_messages_to_state(messages)
        batch_content = "".join(message.to_jsonl() + "\n" for message in messages)
        await self._viking_fs.append_file(
            f"{self._session_uri}/messages.jsonl",
            batch_content,
            ctx=self.ctx,
        )
        await self._save_meta()

    def _apply_appended_messages_to_state(self, messages: List[Message]) -> None:
        """Update in-memory counters after an authoritative root reload."""
        for msg in messages:
            self._messages.append(msg)

            if is_user_query(msg):
                self._stats.total_turns += 1
            msg_tokens = int(msg.estimated_tokens or 0)
            self._stats.total_tokens += msg_tokens

            if self._meta.retention_mode != RETENTION_MODE_TURN_BUDGET:
                keep = int(self._meta.keep_recent_count or 0)
                if keep <= 0:
                    self._meta.pending_tokens += msg_tokens
                elif len(self._messages) > keep:
                    pushed_out = self._messages[-(keep + 1)]
                    self._meta.pending_tokens += int(pushed_out.estimated_tokens or 0)

        if self._meta.retention_mode == RETENTION_MODE_TURN_BUDGET:
            self._rebuild_pending_tokens()

        self._meta.message_count = len(self._messages)
        if self._meta.total_message_count is not None:
            self._meta.total_message_count += len(messages)

    def _build_messages(
        self,
        messages_spec: List[dict],
    ) -> List[Message]:
        """Validate message specs and build their durable Message objects.

        Args:
            messages_spec: List of dicts, each with keys:
                role, parts, peer_id/created_at and optional semantic fields.
        """
        all_messages = []
        for i, spec in enumerate(messages_spec):
            if "role" not in spec:
                raise ValueError(f"messages_spec[{i}]: missing required key 'role'")
            if "parts" not in spec:
                raise ValueError(f"messages_spec[{i}]: missing required key 'parts'")
            role = spec["role"]
            parts = spec["parts"]
            created_at = spec.get("created_at") or datetime.now(timezone.utc).isoformat()
            turn_id = spec.get("turn_id")
            message_kind = spec.get("message_kind")
            source_message_ids = spec.get("source_message_ids")

            try:
                peer_id = normalize_peer_id(spec.get("peer_id"))
            except ValueError as exc:
                from openviking_cli.exceptions import InvalidArgumentError

                raise InvalidArgumentError(str(exc)) from exc

            if self._is_tool_result_aggregate(role, parts):
                msgs = [
                    Message(
                        id=f"msg_{uuid4().hex}",
                        role=role,
                        parts=[part],
                        peer_id=peer_id,
                        created_at=created_at,
                        turn_id=turn_id,
                        message_kind=message_kind or "tool_transport",
                        source_message_ids=(
                            list(source_message_ids) if source_message_ids is not None else None
                        ),
                    )
                    for part in parts
                ]
                self._externalize_large_tool_output_group(msgs)
                all_messages.extend(msgs)
            else:
                msg = Message(
                    id=f"msg_{uuid4().hex}",
                    role=role,
                    parts=parts,
                    peer_id=peer_id,
                    created_at=created_at,
                    turn_id=turn_id,
                    message_kind=message_kind,
                    source_message_ids=(
                        list(source_message_ids) if source_message_ids is not None else None
                    ),
                )
                self._externalize_large_tool_outputs(msg)
                all_messages.append(msg)

        return all_messages

    def add_messages(
        self,
        messages_spec: List[dict],
    ) -> List[Message]:
        """Synchronously add multiple messages in one authoritative batch."""
        messages = self._build_messages(messages_spec)
        self._append_messages(messages)
        return messages

    async def add_messages_async(
        self,
        messages_spec: List[dict],
    ) -> List[Message]:
        """Asynchronously add multiple messages without blocking the caller loop."""
        messages = self._build_messages(messages_spec)
        await self._append_messages_authoritatively(messages)
        return messages

    def add_message(
        self,
        role: str,
        parts: List[Part],
        peer_id: Optional[str] = None,
        created_at: str = None,
        turn_id: Optional[str] = None,
        message_kind: Optional[str] = None,
        source_message_ids: Optional[List[str]] = None,
    ) -> Message:
        """Add a message.

        A user message containing only multiple tool results is treated as a
        transport aggregate and stored as one message per tool result.
        """
        msgs = self.add_messages(
            [
                {
                    "role": role,
                    "parts": parts,
                    "peer_id": peer_id,
                    "created_at": created_at,
                    "turn_id": turn_id,
                    "message_kind": message_kind,
                    "source_message_ids": source_message_ids,
                }
            ]
        )
        return msgs[0]

    async def add_message_async(
        self,
        role: str,
        parts: List[Part],
        peer_id: Optional[str] = None,
        created_at: str = None,
        turn_id: Optional[str] = None,
        message_kind: Optional[str] = None,
        source_message_ids: Optional[List[str]] = None,
    ) -> Message:
        """Asynchronously add one message through the authoritative path lock."""
        msgs = await self.add_messages_async(
            [
                {
                    "role": role,
                    "parts": parts,
                    "peer_id": peer_id,
                    "created_at": created_at,
                    "turn_id": turn_id,
                    "message_kind": message_kind,
                    "source_message_ids": source_message_ids,
                }
            ]
        )
        return msgs[0]

    async def read_tool_result(
        self,
        tool_result_id: str,
        *,
        offset: int = 0,
        limit: int = 20_000,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        store = self._tool_result_store()
        if not store:
            from openviking_cli.exceptions import NotFoundError

            raise NotFoundError(tool_result_id, "tool result")
        return await store.read(
            tool_result_id,
            offset=offset,
            limit=limit,
            include_metadata=include_metadata,
        )

    async def search_tool_result(
        self,
        tool_result_id: str,
        *,
        query: str,
        limit: int = 20,
        context_chars: int = 300,
    ) -> Dict[str, Any]:
        store = self._tool_result_store()
        if not store:
            from openviking_cli.exceptions import NotFoundError

            raise NotFoundError(tool_result_id, "tool result")
        return await store.search(
            tool_result_id,
            query=query,
            limit=limit,
            context_chars=context_chars,
        )

    async def list_tool_results(
        self,
        *,
        tool_name: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        store = self._tool_result_store()
        if not store:
            return {"tool_results": []}
        return await store.list(tool_name=tool_name, limit=limit)

    def _remember_retention_policy(
        self,
        *,
        keep_recent_count: int,
        retention_mode: Optional[str],
        keep_recent_turn_count: int,
        retained_message_token_budget: int,
        min_raw_tail_steps: int,
    ) -> None:
        """Persist the policy used by the latest Phase 1 decision."""
        self._meta.keep_recent_count = keep_recent_count
        self._meta.retention_mode = retention_mode or ""
        self._meta.keep_recent_turn_count = keep_recent_turn_count
        self._meta.retained_message_token_budget = retained_message_token_budget
        self._meta.min_raw_tail_steps = min_raw_tail_steps

    async def _merge_archive_meta(self, archive_uri: str, updates: Dict[str, Any]) -> None:
        """Merge archive metadata so Phase 2 cannot erase Phase 1 planning data."""
        if not self._viking_fs:
            return
        # Summary generation and memory extraction run concurrently. Serialize
        # their read/merge/write cycles so overview token metadata, retention
        # planning and extraction progress cannot overwrite one another.
        async with self._archive_meta_merge_lock:
            meta: Dict[str, Any] = {}
            try:
                content = await self._viking_fs.read_file(f"{archive_uri}/.meta.json", ctx=self.ctx)
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:
                pass
            meta.update(updates)
            await self._viking_fs.write_file(
                uri=f"{archive_uri}/.meta.json",
                content=json.dumps(meta, ensure_ascii=False),
                ctx=self.ctx,
            )

    @staticmethod
    def _retention_plan_meta(
        plan: RetentionPlan,
        *,
        keep_recent_turn_count: int,
        retained_message_token_budget: int,
        min_raw_tail_steps: int,
    ) -> Dict[str, Any]:
        return {
            "mode": RETENTION_MODE_TURN_BUDGET,
            "keep_recent_turn_count": keep_recent_turn_count,
            "retained_message_token_budget": retained_message_token_budget,
            "min_raw_tail_steps": min_raw_tail_steps,
            "partial_turn": plan.partial_turn,
            "turn_anchor_message_id": plan.turn_anchor.id if plan.turn_anchor else None,
            "checkpoint_source_message_ids": list(plan.checkpoint_source_message_ids),
            "raw_tail_start_message_id": plan.raw_tail_start_message_id,
            "estimated_active_tokens": plan.estimated_active_tokens,
            "budget_exceeded": plan.budget_exceeded,
        }

    async def _write_phase1_marker(
        self,
        archive_uri: str,
        *,
        queue_message: Dict[str, Any],
        original_messages: List[Message],
        archived_messages: List[Message],
        retained_messages: List[Message],
        keep_recent_count: int,
        retention_mode: Optional[str],
        keep_recent_turn_count: int,
        retained_message_token_budget: int,
        min_raw_tail_steps: int,
    ) -> None:
        """Persist the Phase 1 intent before any destructive root rewrite."""
        payload = {
            "version": 1,
            "status": "preparing",
            "created_at": get_current_timestamp(),
            "queue_message": queue_message,
            "original_message_ids": [message.id for message in original_messages],
            "archived_message_ids": [message.id for message in archived_messages],
            "retained_message_ids": [message.id for message in retained_messages],
            "keep_recent_count": keep_recent_count,
            "retention_mode": retention_mode or "",
            "keep_recent_turn_count": keep_recent_turn_count,
            "retained_message_token_budget": retained_message_token_budget,
            "min_raw_tail_steps": min_raw_tail_steps,
        }
        await self._merge_archive_meta(archive_uri, {"phase1": payload})

    async def _write_phase1_ready_marker(self, archive_uri: str) -> None:
        phase1 = await self._read_phase1_meta(archive_uri)
        phase1.update(
            {
                "status": "ready",
                "ready_at": get_current_timestamp(),
            }
        )
        await self._merge_archive_meta(archive_uri, {"phase1": phase1})

    async def _archive_file_exists(self, archive_uri: str, file_name: str) -> bool:
        try:
            return await self._viking_fs.exists(f"{archive_uri}/{file_name}", ctx=self.ctx)
        except Exception:
            return False

    async def _read_phase1_meta(self, archive_uri: str) -> Dict[str, Any]:
        phase1 = (await self._read_archive_meta(archive_uri)).get("phase1")
        return dict(phase1) if isinstance(phase1, dict) else {}

    async def _ensure_phase1_ready(self, archive_uri: str) -> bool:
        """Verify or recover a queued Phase 1 before Phase 2 consumes it.

        New commits enqueue while holding the session lock and before rewriting
        root messages. A consumer therefore acquires the same lock, then either
        observes ``phase1.status=ready`` in archive metadata or
        deterministically reconciles a process crash from the persisted intent.
        """
        marker = await self._read_phase1_meta(archive_uri)
        if not marker:
            # Archives created by older OpenViking versions have no Phase 1
            # metadata and keep their previous processing contract.
            return True
        if marker.get("status") == "ready":
            return True
        if await self._archive_file_exists(archive_uri, ".failed.json"):
            return False

        from openviking.storage.transaction import LockContext, get_lock_manager

        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        async with LockContext(
            get_lock_manager(),
            [session_path],
            lock_mode="exact",
            timeout=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS,
        ):
            marker = await self._read_phase1_meta(archive_uri)
            if marker.get("status") == "ready":
                return True
            if await self._archive_file_exists(archive_uri, ".failed.json"):
                return False

            try:
                if not marker:
                    raise ValueError("Phase 1 metadata is missing")
                retained_ids = marker.get("retained_message_ids")
                archived_ids = marker.get("archived_message_ids")
                if not isinstance(retained_ids, list) or not isinstance(archived_ids, list):
                    raise ValueError("Phase 1 metadata has invalid message ID lists")
                retained_ids = [item for item in retained_ids if isinstance(item, str)]
                archived_ids = [item for item in archived_ids if isinstance(item, str)]
                live_messages = await self._read_live_messages_strict()
            except Exception as exc:
                await self._write_failed_marker(
                    archive_uri,
                    stage="phase1_recovery",
                    error=f"Cannot verify Phase 1 state: {exc}",
                )
                return False

            live_ids = [message.id for message in live_messages]
            archived_only_ids = set(archived_ids) - set(retained_ids)
            phase1_applied = (
                live_ids[: len(retained_ids)] == retained_ids
                and not archived_only_ids.intersection(live_ids)
            )
            if not phase1_applied:
                await self._write_failed_marker(
                    archive_uri,
                    stage="phase1_recovery",
                    error="Root rewrite was not durably completed before process interruption",
                )
                return False

            # Root is authoritative and proves the rewrite completed. Reconcile
            # metadata that may have been interrupted immediately afterwards.
            try:
                meta_content = await self._viking_fs.read_file(
                    f"{self._session_uri}/.meta.json",
                    ctx=self.ctx,
                )
                self._meta = SessionMeta.from_dict(json.loads(meta_content))
            except Exception:
                pass
            self._messages = live_messages
            self._remember_retention_policy(
                keep_recent_count=max(0, int(marker.get("keep_recent_count", 0) or 0)),
                retention_mode=str(marker.get("retention_mode", "") or "") or None,
                keep_recent_turn_count=max(
                    0, int(marker.get("keep_recent_turn_count", 0) or 0)
                ),
                retained_message_token_budget=max(
                    0, int(marker.get("retained_message_token_budget", 0) or 0)
                ),
                min_raw_tail_steps=max(0, int(marker.get("min_raw_tail_steps", 1) or 0)),
            )
            self._meta.message_count = len(live_messages)
            self._meta.commit_count = max(
                self._meta.commit_count,
                self._archive_index_from_uri(archive_uri),
            )
            self._meta.last_commit_at = get_current_timestamp()
            self._rebuild_pending_tokens()
            await self._save_meta()
            await self._write_phase1_ready_marker(archive_uri)
            logger.warning("Recovered interrupted Session Phase 1: %s", archive_uri)
            return True

    def commit(
        self,
        keep_recent_count: int = 0,
        *,
        memory_policy: Optional[Dict[str, Any]] = None,
        retention_mode: Optional[str] = None,
        keep_recent_turn_count: Optional[int] = None,
        retained_message_token_budget: Optional[int] = None,
        min_raw_tail_steps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sync wrapper for commit_async()."""
        return run_async(
            self.commit_async(
                keep_recent_count=keep_recent_count,
                memory_policy=memory_policy,
                retention_mode=retention_mode,
                keep_recent_turn_count=keep_recent_turn_count,
                retained_message_token_budget=retained_message_token_budget,
                min_raw_tail_steps=min_raw_tail_steps,
            )
        )

    @tracer("session.commit.phase1")
    async def commit_async(
        self,
        keep_recent_count: int = 0,
        *,
        memory_policy: Optional[Dict[str, Any]] = None,
        retention_mode: Optional[str] = None,
        keep_recent_turn_count: Optional[int] = None,
        retained_message_token_budget: Optional[int] = None,
        min_raw_tail_steps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Archive immediately and enqueue restart-safe Phase 2 processing.

        Phase 1 (Archive prep, path-lock protected): Split messages into
        archive/retain parts, persist a recoverable intent and archive raw,
        enqueue Phase 2, then atomically publish the retained root state with
        ``phase1.status=ready``. Uses a distributed filesystem lock across
        workers and processes.
        Phase 2 (Memory extraction): Runs through the persistent QueueFS queue.

        Args:
            keep_recent_count: Number of most-recent messages to keep in the
                live session after commit. ``0`` (default) preserves the old
                behavior of archiving everything. The plugin's afterTurn path
                typically passes its configured value (default 10); the compact
                path passes ``0``.

        Returns a task_id for tracking Phase 2 progress.
        """
        from openviking.service.task_tracker import get_task_tracker
        from openviking.storage.queuefs import QueueManager, get_queue_manager
        from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
        from openviking.storage.transaction import LockContext, get_lock_manager

        trace_id = tracer.get_trace_id()
        keep_recent_count = max(0, int(keep_recent_count or 0))
        if retention_mode not in (None, RETENTION_MODE_TURN_BUDGET):
            raise ValueError(f"Unsupported retention_mode: {retention_mode}")
        if retention_mode is None and any(
            value is not None
            for value in (
                keep_recent_turn_count,
                retained_message_token_budget,
                min_raw_tail_steps,
            )
        ):
            raise ValueError(
                "retention_mode='turn_budget' is required when Turn retention fields are set"
            )
        turn_mode = retention_mode == RETENTION_MODE_TURN_BUDGET
        effective_keep_turns = max(
            0, int(3 if keep_recent_turn_count is None else keep_recent_turn_count)
        )
        effective_token_budget = max(
            0,
            int(12_000 if retained_message_token_budget is None else retained_message_token_budget),
        )
        effective_min_tail = max(0, int(1 if min_raw_tail_steps is None else min_raw_tail_steps))
        if turn_mode and effective_token_budget <= 0:
            raise ValueError("retained_message_token_budget must be greater than 0")
        in_memory_default_memory_policy = self._meta.memory_policy
        effective_policy = MemoryPolicy.from_dict(
            memory_policy if memory_policy is not None else self._meta.memory_policy
        )
        _validate_memory_policy_types(effective_policy)
        effective_memory_policy = effective_policy.to_dict()
        logger.info(
            f"[TRACER] session_commit started, trace_id={trace_id}, "
            f"keep_recent_count={keep_recent_count}, retention_mode={retention_mode}, "
            f"keep_recent_turn_count={effective_keep_turns}, "
            f"retained_message_token_budget={effective_token_budget}"
        )

        # ===== Phase 1: authoritative snapshot + split (path-lock protected) =====
        # Use a waiting filesystem lock and reload inside it. Different workers
        # can hold stale Session objects, so in-memory emptiness is never a
        # correctness boundary.
        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        async with LockContext(
            get_lock_manager(),
            [session_path],
            lock_mode="exact",
            timeout=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS,
        ):
            self._messages = await self._read_live_messages_strict()
            try:
                meta_content = await self._viking_fs.read_file(
                    f"{self._session_uri}/.meta.json",
                    ctx=self.ctx,
                )
                self._meta = SessionMeta.from_dict(json.loads(meta_content))
                if (
                    memory_policy is None
                    and self._meta.memory_policy is None
                    and in_memory_default_memory_policy is not None
                ):
                    self._meta.memory_policy = in_memory_default_memory_policy
            except Exception:
                # The root JSONL remains authoritative for message correctness;
                # legacy sessions may not have metadata yet.
                pass

            # A Session object may have been loaded by another worker before a
            # different worker updated the persisted default policy. Phase 2
            # must use the policy from the same lock-protected snapshot as the
            # messages being archived, unless this commit supplied an explicit
            # override.
            if memory_policy is None:
                effective_policy = MemoryPolicy.from_dict(self._meta.memory_policy)
                _validate_memory_policy_types(effective_policy)
                effective_memory_policy = effective_policy.to_dict()

            archive_refs = await self._list_archive_refs()
            self._compression.compression_index = max(
                [archive["index"] for archive in archive_refs],
                default=0,
            )
            if not self._messages:
                self._meta.pending_tokens = 0
                self._remember_retention_policy(
                    keep_recent_count=keep_recent_count,
                    retention_mode=retention_mode,
                    keep_recent_turn_count=effective_keep_turns if turn_mode else 0,
                    retained_message_token_budget=effective_token_budget if turn_mode else 0,
                    min_raw_tail_steps=effective_min_tail,
                )
                await self._save_meta()
                get_current_telemetry().set("memory.extracted", 0)
                return {
                    "session_id": self.session_id,
                    "status": "skipped",
                    "task_id": None,
                    "archive_uri": None,
                    "archived": False,
                    "reason": "no_messages",
                    "trace_id": trace_id,
                }

            total = len(self._messages)
            retention_plan: Optional[RetentionPlan] = None
            if turn_mode:
                # The externalization budget belongs to a logical Turn, not one
                # physical assistant message. This catches N small tool outputs
                # whose aggregate exceeds the configured inline budget.
                for turn in build_turns(self._messages):
                    self._externalize_large_tool_output_group(turn.messages)
                retention_plan = plan_retention(
                    self._messages,
                    keep_recent_turn_count=effective_keep_turns,
                    token_budget=effective_token_budget,
                    min_raw_tail_steps=effective_min_tail,
                )
                messages_to_archive = retention_plan.archive_messages
                retained_messages = retention_plan.retained_messages
            elif keep_recent_count > 0:
                split_idx = max(0, total - keep_recent_count)
                messages_to_archive = self._messages[:split_idx]
                retained_messages = self._messages[split_idx:]
            else:
                messages_to_archive = self._messages.copy()
                retained_messages = []

            # No archive work: persist possible Turn-wide externalization and
            # remember the policy for subsequent add_message accounting.
            if not messages_to_archive:
                self._messages = retained_messages
                await self._write_to_agfs_async(messages=self._messages)
                self._meta.pending_tokens = 0
                self._meta.message_count = total
                self._remember_retention_policy(
                    keep_recent_count=keep_recent_count,
                    retention_mode=retention_mode,
                    keep_recent_turn_count=effective_keep_turns if turn_mode else 0,
                    retained_message_token_budget=effective_token_budget if turn_mode else 0,
                    min_raw_tail_steps=effective_min_tail,
                )
                await self._save_meta()
                get_current_telemetry().set("memory.extracted", 0)
                return {
                    "session_id": self.session_id,
                    "status": "skipped",
                    "task_id": None,
                    "archive_uri": None,
                    "archived": False,
                    "reason": "all_within_keep_window",
                    "trace_id": trace_id,
                    "estimated_active_tokens": (
                        retention_plan.estimated_active_tokens if retention_plan else 0
                    ),
                    "budget_exceeded": retention_plan.budget_exceeded if retention_plan else False,
                }

            self._compression.compression_index += 1
            archive_uri = (
                f"{self._session_uri}/history/archive_{self._compression.compression_index:03d}"
            )
            original_messages = list(self._messages)
            usage_snapshot = self._usage_records.copy()
            task_id = str(uuid4())
            queue_msg = SessionCommitMsg(
                task_id=task_id,
                session_id=self.session_id,
                session_uri=self._session_uri,
                archive_uri=archive_uri,
                user=self.ctx.user.to_dict(),
                actor_peer_id=self.ctx.actor_peer_id,
                memory_policy=effective_memory_policy,
                usage_uris=list(dict.fromkeys(u.uri for u in usage_snapshot if u.uri)),
            )
            phase1_stage = "phase1_persist"
            try:
                # Persist the full intent before any root rewrite. Queueing while
                # holding the same session lock lets a consumer either observe
                # final ready metadata or recover the exact interrupted state.
                await self._write_phase1_marker(
                    archive_uri,
                    queue_message=queue_msg.to_dict(),
                    original_messages=original_messages,
                    archived_messages=messages_to_archive,
                    retained_messages=retained_messages,
                    keep_recent_count=keep_recent_count,
                    retention_mode=retention_mode,
                    keep_recent_turn_count=effective_keep_turns if turn_mode else 0,
                    retained_message_token_budget=effective_token_budget if turn_mode else 0,
                    min_raw_tail_steps=effective_min_tail,
                )

                # Archive raw remains durable and recoverable before any live
                # conversation history is removed from the root JSONL.
                if self._viking_fs:
                    lines = [m.to_jsonl() for m in messages_to_archive]
                    await self._viking_fs.write_file(
                        uri=f"{archive_uri}/messages.jsonl",
                        content="\n".join(lines) + "\n",
                        ctx=self.ctx,
                    )
                    if retention_plan is not None:
                        await self._merge_archive_meta(
                            archive_uri,
                            {
                                "retention_plan": self._retention_plan_meta(
                                    retention_plan,
                                    keep_recent_turn_count=effective_keep_turns,
                                    retained_message_token_budget=effective_token_budget,
                                    min_raw_tail_steps=effective_min_tail,
                                )
                            },
                        )

                phase1_stage = "queue_enqueue"
                await get_queue_manager().enqueue(
                    QueueManager.SESSION_COMMIT,
                    queue_msg.to_dict(),
                )

                # Register after the durable queue write but before publishing
                # ready. The consumer is blocked by this session lock until
                # ready, so it cannot complete the task before registration;
                # if registration fails, the queued consumer will observe the
                # Phase 1 failure marker and create/fail the task itself.
                phase1_stage = "task_tracker_create"
                await get_task_tracker().create(
                    "session_commit",
                    resource_id=self.session_id,
                    account_id=self.ctx.account_id,
                    user_id=self.ctx.user.user_id,
                    task_id=task_id,
                )

                phase1_stage = "phase1_persist"
                self._messages = retained_messages
                await self._write_to_agfs_async(messages=self._messages)
                self._meta.message_count = len(self._messages)
                self._meta.pending_tokens = 0
                self._remember_retention_policy(
                    keep_recent_count=keep_recent_count,
                    retention_mode=retention_mode,
                    keep_recent_turn_count=effective_keep_turns if turn_mode else 0,
                    retained_message_token_budget=effective_token_budget if turn_mode else 0,
                    min_raw_tail_steps=effective_min_tail,
                )
                self._meta.commit_count = max(
                    self._meta.commit_count,
                    self._compression.compression_index,
                )
                self._meta.last_commit_at = get_current_timestamp()
                await self._save_meta()
                await self._write_phase1_ready_marker(archive_uri)
            except Exception as e:
                logger.error(f"[commit] Failed during {phase1_stage}: {e}")
                # Whether the queue write failed or a queued Phase 1 stopped
                # before publication, a terminal marker makes archive raw
                # logically live and prevents a permanent pending directory.
                try:
                    await self._write_failed_marker(
                        archive_uri,
                        stage=phase1_stage,
                        error=str(e),
                    )
                except Exception:
                    logger.exception(
                        "Failed to mark archive after Phase 1 persistence failure: %s",
                        archive_uri,
                    )
                self._messages = original_messages
                self._compression.compression_index -= 1
                raise
        # Lock released; Phase 1 intent, queue item, retained root, metadata and
        # ready metadata are all durable.

        self._compression.original_count += len(messages_to_archive)
        logger.info(
            f"Archived: {len(messages_to_archive)} messages → "
            f"history/archive_{self._compression.compression_index:03d}/"
        )

        return {
            "session_id": self.session_id,
            "status": "accepted",
            "task_id": task_id,
            "archive_uri": archive_uri,
            "archived": True,
            "trace_id": trace_id,
            "estimated_active_tokens": (
                retention_plan.estimated_active_tokens if retention_plan else 0
            ),
            "budget_exceeded": retention_plan.budget_exceeded if retention_plan else False,
        }

    async def resume_queued_commit(self, msg: "SessionCommitMsg") -> None:
        """Run one durable Phase 2 job from its archived messages."""
        from openviking.service.task_tracker import get_task_tracker

        tracker = get_task_tracker()
        task = await tracker.create(
            "session_commit",
            resource_id=self.session_id,
            account_id=self.ctx.account_id,
            user_id=self.ctx.user.user_id,
            task_id=msg.task_id,
        )

        try:
            await self._viking_fs.read_file(f"{msg.archive_uri}/.done", ctx=self.ctx)
        except Exception:
            pass
        else:
            if task.status.value == "completed":
                return
            await tracker.complete(
                msg.task_id,
                {"session_id": self.session_id, "archive_uri": msg.archive_uri},
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            return

        try:
            failed = json.loads(
                await self._viking_fs.read_file(f"{msg.archive_uri}/.failed.json", ctx=self.ctx)
            )
        except Exception:
            pass
        else:
            await tracker.fail(
                msg.task_id,
                str(failed.get("error") or "session commit failed"),
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            return

        if not await self._ensure_phase1_ready(msg.archive_uri):
            try:
                failed = json.loads(
                    await self._viking_fs.read_file(
                        f"{msg.archive_uri}/.failed.json",
                        ctx=self.ctx,
                    )
                )
                error = str(failed.get("error") or "session commit Phase 1 is not ready")
            except Exception:
                error = "session commit Phase 1 is not ready"
            await tracker.fail(
                msg.task_id,
                error,
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            return

        archive_messages = await self._read_archive_messages(msg.archive_uri)
        if not archive_messages:
            error = "session commit archive has no messages"
            await self._write_failed_marker(
                msg.archive_uri,
                stage="archive_read",
                error=error,
            )
            await tracker.fail(
                msg.task_id,
                error,
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            return

        await self._run_memory_extraction(
            task_id=msg.task_id,
            archive_uri=msg.archive_uri,
            messages=archive_messages,
            usage_records=[Usage(uri=uri, type="context") for uri in msg.usage_uris],
            first_message_id=archive_messages[0].id,
            last_message_id=archive_messages[-1].id,
            memory_policy=msg.memory_policy,
        )

    async def _run_usage_reporting(
        self,
        *,
        task_id: str,
        archive_uri: str,
        messages: List[Message],
    ) -> list[Any]:
        reporter = getattr(self, "_usage_reporter", None)
        if reporter is None:
            return []

        from openviking.usage_reporter import UsageContext

        context = UsageContext(
            account_id=self.ctx.account_id,
            user_id=self.ctx.user.user_id,
            session_id=self.session_id,
            archive_uri=archive_uri,
            task_id=task_id,
        )
        return await reporter.extract_and_report(messages=messages, context=context)

    @tracer("session.commit.phase2", ignore_result=True, ignore_args=True)
    async def _run_memory_extraction(
        self,
        task_id: str,
        archive_uri: str,
        messages: List[Message],
        usage_records: List["Usage"],
        first_message_id: str,
        last_message_id: str,
        memory_policy: Optional[Dict[str, Any]],
    ) -> None:
        """Phase 2: Extract memories, write relations, enqueue — runs in background."""
        from openviking.service.task_tracker import get_task_tracker
        from openviking.telemetry import OperationTelemetry, bind_telemetry
        from openviking.telemetry.registry import register_telemetry, unregister_telemetry

        tracker = get_task_tracker()
        request_wait_tracker = get_request_wait_tracker()

        memories_extracted: Dict[str, int] = {}
        usage_events_extracted = 0
        extracted_skill_results: list[dict] = []
        active_count_updated = 0
        memory_diff_uri: Optional[str] = None
        completed_memory_steps: Dict[str, set[str]] = {}
        telemetry = OperationTelemetry(operation="session_commit_phase2", enabled=True)
        archive_index = self._archive_index_from_uri(archive_uri)

        try:
            await self._wait_for_previous_archive_done(archive_index)
            (
                messages,
                coverage_start_archive,
                coverage_end_archive,
                covered_failed_archives,
                completed_memory_steps,
            ) = await self._prepare_phase2_archive_messages(archive_uri, messages)
            if not messages:
                raise ValueError("session commit archive has no recoverable messages")
            first_message_id = messages[0].id
            last_message_id = messages[-1].id

            await tracker.start(
                task_id,
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            request_wait_tracker.register_request(telemetry.telemetry_id)
            register_telemetry(telemetry)
            try:
                with bind_telemetry(telemetry):
                    ov_config = get_openviking_config()
                    effective_policy = MemoryPolicy.from_dict(memory_policy)
                    working_memory_enabled = effective_policy.working_memory_enabled
                    checkpoint_requests = (
                        await self._collect_checkpoint_requests_for_phase2(
                            archive_uri,
                            covered_failed_archives,
                            messages,
                        )
                        if working_memory_enabled
                        else []
                    )
                    latest_archive_overview = (
                        await self._get_latest_completed_archive_overview(
                            exclude_archive_uri=archive_uri,
                            before_archive_index=archive_index,
                        )
                        if working_memory_enabled
                        else ""
                    )
                    extraction_messages = await self._hydrate_tool_outputs_for_extraction(messages)
                    usage_events_extracted = len(
                        await self._run_usage_reporting(
                            task_id=task_id,
                            archive_uri=archive_uri,
                            messages=extraction_messages,
                        )
                    )

                    async def _run_archive_summary() -> None:
                        if not working_memory_enabled:
                            logger.info(
                                "Working Memory summary skipped "
                                "(memory_policy.working_memory.enabled=false)"
                            )
                            return
                        summary_kwargs: Dict[str, Any] = {
                            "latest_archive_overview": latest_archive_overview,
                        }
                        if checkpoint_requests:
                            summary_kwargs["checkpoint_requests"] = checkpoint_requests
                        generated = await self._generate_archive_summary_async(
                            extraction_messages,
                            **summary_kwargs,
                        )
                        summary_result = (
                            generated
                            if isinstance(generated, _ArchiveSummaryResult)
                            else _ArchiveSummaryResult(overview=str(generated or ""))
                        )
                        checkpoint_records = self._build_checkpoint_records(
                            checkpoint_requests,
                            summary_result.checkpoint_summaries,
                        )
                        summary = summary_result.overview
                        if checkpoint_requests and not summary.strip():
                            raise ValueError(
                                "Working Memory output is empty for a required checkpoint"
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
                            await self._merge_archive_meta(
                                archive_uri,
                                {
                                    "overview_tokens": estimate_text_tokens(summary),
                                    "abstract_tokens": estimate_text_tokens(abstract),
                                    "checkpoints": checkpoint_records,
                                },
                            )

                    async def _run_retryable_phase2_step(
                        operation_name: str,
                        fn: Callable[[], Awaitable[Any]],
                    ) -> Any:
                        # Secondary safety net on top of the per-call retry that the
                        # VLM/embedding layer already performs. Reuses the shared
                        # transient-error classifier so permanent failures (auth,
                        # quota, content-safety, 400, oversized input) fail fast
                        # instead of being retried pointlessly.
                        return await retry_async(
                            fn,
                            max_retries=_MEMORY_EXTRACTION_MAX_RETRIES,
                            base_delay=_MEMORY_EXTRACTION_RETRY_BASE_DELAY_SECONDS,
                            max_delay=_MEMORY_EXTRACTION_RETRY_MAX_DELAY_SECONDS,
                            is_retryable=is_retryable_api_error,
                            logger=logger,
                            operation_name=operation_name,
                        )

                    async def _run_recorded_memory_step(
                        operation_name: str,
                        step: str,
                        step_messages: List[Message],
                        fn: Callable[[], Awaitable[Any]],
                    ) -> Any:
                        result = await _run_retryable_phase2_step(operation_name, fn)
                        completed_memory_steps.setdefault(step, set()).update(
                            message.id for message in step_messages
                        )
                        # Persist progress before waiting for sibling Phase 2
                        # tasks. A process restart or a sibling failure can then
                        # resume without applying this memory step twice.
                        await self._merge_archive_meta(
                            archive_uri,
                            {
                                "completed_memory_steps": (
                                    self._serialize_completed_memory_steps(completed_memory_steps)
                                )
                            },
                        )
                        return result

                    # Summary, long-term memory, and execution-derived memory run concurrently.
                    memory_extraction_enabled = ov_config.memory.extraction_enabled
                    config_session_skill_extraction_enabled = (
                        ov_config.memory.session_skill_extraction_enabled
                    )
                    extraction_scope = _resolve_memory_extraction_scope(
                        self.ctx,
                        effective_policy,
                        extraction_messages,
                        config_session_skill_extraction_enabled=(
                            config_session_skill_extraction_enabled
                        ),
                    )
                    self_memory_enabled = extraction_scope.allow_self_memory
                    allowed_peer_ids = extraction_scope.allowed_peer_ids
                    session_skill_extraction_enabled = extraction_scope.include_session_skills
                    memory_type_filter = extraction_scope.memory_types
                    long_term_memory_types, execution_memory_types = _split_policy_memory_types(
                        memory_type_filter
                    )

                    long_term_messages = [
                        message
                        for message in extraction_messages
                        if message.id not in completed_memory_steps.get("long_term", set())
                    ]
                    execution_messages = [
                        message
                        for message in extraction_messages
                        if message.id not in completed_memory_steps.get("execution", set())
                    ]

                    long_term_has_work = (
                        memory_extraction_enabled
                        and (self_memory_enabled or allowed_peer_ids)
                        and (long_term_memory_types is None or bool(long_term_memory_types))
                        and bool(long_term_messages)
                    )
                    execution_memory_has_work = (
                        self_memory_enabled
                        and memory_extraction_enabled
                        and (execution_memory_types is None or bool(execution_memory_types))
                        and bool(execution_messages)
                    )
                    session_skill_extraction_enabled = (
                        session_skill_extraction_enabled and execution_memory_has_work
                    )
                    has_policy_work = bool(long_term_has_work or execution_memory_has_work)
                    if self._session_compressor and has_policy_work:
                        logger.info(
                            "Starting post-commit extraction from %s archived messages",
                            len(messages),
                        )

                        has_execution_memory = hasattr(
                            self._session_compressor, "extract_execution_memories"
                        )

                        extraction_tasks: List[Any] = []
                        extraction_labels: List[str] = []
                        if working_memory_enabled:
                            extraction_tasks.append(
                                _run_retryable_phase2_step("archive_summary", _run_archive_summary)
                            )
                            extraction_labels.append("archive_summary")

                        if long_term_has_work:

                            async def _run_long_term_memory_extraction() -> Any:
                                # strict_extract_errors=True lets transient failures
                                # surface so _run_retryable_phase2_step can retry them
                                # (and so a final failure is recorded as a skipped
                                # archive instead of silently dropping the memory).
                                return await self._session_compressor.extract_long_term_memories(
                                    messages=long_term_messages,
                                    user=self.user,
                                    session_id=self.session_id,
                                    ctx=self.ctx,
                                    strict_extract_errors=True,
                                    latest_archive_overview=latest_archive_overview,
                                    archive_uri=archive_uri,
                                    allowed_memory_types=long_term_memory_types,
                                    allow_self_memory=self_memory_enabled,
                                    allowed_peer_ids=allowed_peer_ids,
                                )

                            extraction_tasks.append(
                                _run_recorded_memory_step(
                                    "long_term_memory_extraction",
                                    "long_term",
                                    long_term_messages,
                                    _run_long_term_memory_extraction,
                                )
                            )
                            extraction_labels.append("long_term")

                        if has_execution_memory and execution_memory_has_work:

                            async def _run_execution_memory_extraction() -> Any:
                                # See _run_long_term_memory_extraction: surface errors
                                # so retries can engage and final failures are visible.
                                return await self._session_compressor.extract_execution_memories(
                                    messages=execution_messages,
                                    ctx=self.ctx,
                                    strict_extract_errors=True,
                                    latest_archive_overview=latest_archive_overview,
                                    archive_uri=archive_uri,
                                    allowed_memory_types=execution_memory_types,
                                    include_session_skills=session_skill_extraction_enabled,
                                )

                            extraction_tasks.append(
                                _run_recorded_memory_step(
                                    "execution_memory_extraction",
                                    "execution",
                                    execution_messages,
                                    _run_execution_memory_extraction,
                                )
                            )
                            extraction_labels.append("execution")

                        _results = await asyncio.gather(
                            *extraction_tasks,
                            return_exceptions=True,
                        )
                        # The archive outcome is binary: if ANY Phase 2 step
                        # still fails after retries, no .done coverage is
                        # published. Successful memory steps keep a message-ID
                        # progress marker solely to make later raw replay
                        # idempotent.
                        extraction_error: Optional[BaseException] = None
                        for label, result in zip(extraction_labels, _results, strict=True):
                            if isinstance(result, Exception):
                                logger.error(
                                    "Phase 2 step %s failed: %s",
                                    label,
                                    result,
                                    exc_info=result,
                                )
                                if extraction_error is None:
                                    extraction_error = result

                        if extraction_error is not None:
                            raise extraction_error

                        if long_term_has_work and self._viking_fs:
                            candidate_memory_diff_uri = f"{archive_uri}/memory_diff.json"
                            if await self._viking_fs.exists(
                                candidate_memory_diff_uri,
                                ctx=self.ctx,
                            ):
                                memory_diff_uri = candidate_memory_diff_uri

                        total_extracted = 0
                        for label, result in zip(extraction_labels, _results, strict=True):
                            if label == "archive_summary":
                                continue
                            if isinstance(result, dict):
                                target_contexts = list(result.get("contexts", []))
                                target_skills = list(result.get("session_skills", []))
                            else:
                                target_contexts = list(result or [])
                                target_skills = []
                            logger.info(
                                "Extracted %s memories for %s",
                                len(target_contexts),
                                label,
                            )
                            total_extracted += len(target_contexts)
                            for ctx_item in target_contexts:
                                cat = getattr(ctx_item, "category", "") or "unknown"
                                memories_extracted[cat] = memories_extracted.get(cat, 0) + 1
                            if target_skills:
                                extracted_skill_results.extend(target_skills)

                        if total_extracted:
                            self._stats.memories_extracted += total_extracted
                        if extracted_skill_results:
                            logger.info(
                                "Extracted %s session skills",
                                len(extracted_skill_results),
                            )
                        get_current_telemetry().set("memory.extracted", total_extracted)
                    else:
                        if self._session_compressor:
                            logger.info(
                                "Memory and session skill extraction skipped "
                                "(disabled by config or memory_policy)"
                            )
                        if working_memory_enabled:
                            await _run_retryable_phase2_step(
                                "archive_summary", _run_archive_summary
                            )
                        else:
                            await _run_archive_summary()

                    # Write relations (using snapshot, not self._usage_records)
                    if self._viking_fs:
                        for usage in usage_records:
                            try:
                                await self._viking_fs.link(
                                    self._session_uri, usage.uri, ctx=self.ctx
                                )
                            except Exception as e:
                                logger.warning(f"Failed to create relation to {usage.uri}: {e}")

                    # Update active_count (using snapshot, not self._usage_records)
                    if self._vikingdb_manager:
                        uris = [u.uri for u in usage_records if u.uri]
                        try:
                            active_count_updated = (
                                await self._vikingdb_manager.increment_active_count(self.ctx, uris)
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

            # Write .done last so a recovered queue item can skip completed work.
            await self._write_done_file(
                archive_uri,
                first_message_id,
                last_message_id,
                working_memory_enabled=working_memory_enabled,
                coverage_start_archive=coverage_start_archive,
                coverage_end_archive=coverage_end_archive,
                covered_failed_archives=covered_failed_archives,
                completed_memory_steps=self._serialize_completed_memory_steps(
                    completed_memory_steps
                ),
            )

            result_payload = {
                "session_id": self.session_id,
                "archive_uri": archive_uri,
                "memories_extracted": memories_extracted,
                "session_skills_extracted": len(extracted_skill_results),
                "session_skill_uris": [
                    item.get("uri") or item.get("root_uri")
                    for item in extracted_skill_results
                    if isinstance(item, dict) and (item.get("uri") or item.get("root_uri"))
                ],
                "usage_events_extracted": usage_events_extracted,
                "active_count_updated": active_count_updated,
                "token_usage": {
                    "llm": dict(self._meta.llm_token_usage),
                    "embedding": dict(self._meta.embedding_token_usage),
                    "total": {
                        "total_tokens": self._meta.llm_token_usage["total_tokens"]
                        + self._meta.embedding_token_usage["total_tokens"],
                        "cached_tokens": self._meta.llm_token_usage["cached_tokens"],
                        "reasoning_tokens": self._meta.llm_token_usage["reasoning_tokens"],
                    },
                },
            }
            if memory_diff_uri:
                result_payload["memory_diff_uri"] = memory_diff_uri

            await tracker.complete(
                task_id,
                result_payload,
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            logger.info(f"Session {self.session_id} memory extraction completed")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._write_failed_marker(
                archive_uri,
                stage="memory_extraction",
                error=str(e),
                completed_memory_steps=self._serialize_completed_memory_steps(
                    completed_memory_steps
                ),
            )
            await tracker.fail(
                task_id, str(e), account_id=self.ctx.account_id, user_id=self.ctx.user.user_id
            )
            logger.exception(f"Memory extraction failed for session {self.session_id}")

    async def _write_done_file(
        self,
        archive_uri: str,
        first_message_id: str,
        last_message_id: str,
        *,
        working_memory_enabled: Optional[bool] = None,
        coverage_start_archive: Optional[str] = None,
        coverage_end_archive: Optional[str] = None,
        covered_failed_archives: Optional[List[str]] = None,
        completed_memory_steps: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """Write .done marker file to the archive directory."""
        if not self._viking_fs:
            return
        archive_id = archive_uri.rstrip("/").split("/")[-1]
        content = json.dumps(
            {
                "starting_message_id": first_message_id,
                "ending_message_id": last_message_id,
                "working_memory_enabled": working_memory_enabled,
                "coverage_start_archive": coverage_start_archive or archive_id,
                "coverage_end_archive": coverage_end_archive or archive_id,
                "covered_failed_archives": list(covered_failed_archives or []),
                "completed_memory_steps": dict(completed_memory_steps or {}),
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
        skipped: bool = True,
        completed_memory_steps: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """Persist a terminal failure marker for the archive."""
        if not self._viking_fs:
            return
        payload = {
            "stage": stage,
            "error": error,
            "failed_at": get_current_timestamp(),
            "skipped": skipped,
            "completed_memory_steps": dict(completed_memory_steps or {}),
        }
        if blocked_by:
            payload["blocked_by"] = blocked_by
        await self._viking_fs.write_file(
            uri=f"{archive_uri}/.failed.json",
            content=json.dumps(payload, ensure_ascii=False),
            ctx=self.ctx,
        )

    async def get_session_context(self, token_budget: int = 128_000) -> Dict[str, Any]:
        """Get assembled session context with the latest summary archive and merged messages."""
        if token_budget < 0:
            raise ValueError("token_budget must be greater than or equal to 0")

        context = await self._collect_session_context_components()
        merged_messages = context["messages"]
        budgeted = fit_active_messages_to_budget(
            merged_messages,
            token_budget=token_budget,
        )
        merged_messages = budgeted.messages
        message_tokens = budgeted.estimated_tokens
        if budgeted.dropped_message_ids or budgeted.truncated_message_ids:
            logger.info(
                "[get_session_context] active budget applied: session_id=%s, "
                "budget=%s, dropped=%s, truncated=%s",
                self.session_id,
                token_budget,
                len(budgeted.dropped_message_ids),
                len(budgeted.truncated_message_ids),
            )

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
        archive_states = await self._scan_archive_states()
        completed_archives = [
            state for state in reversed(archive_states) if state.state == "completed"
        ]
        latest_archive = None
        pre_archive_abstracts: List[Dict[str, Any]] = []
        covered_archive_ids = self._covered_archive_ids(archive_states)
        failed_archives = sum(
            state.state == "failed" and state.archive_id not in covered_archive_ids
            for state in archive_states
        )

        for archive in completed_archives:
            # ``.done`` is the authoritative completion marker. A completed
            # archive may intentionally have no overview when Working Memory is
            # disabled, so keep looking for the newest completed archive that
            # actually has a readable overview.
            if latest_archive is None and archive.overview.strip():
                latest_archive = {
                    "archive_id": archive.archive_id,
                    "archive_uri": archive.archive_uri,
                    "overview": archive.overview,
                    "overview_tokens": await self._read_archive_overview_tokens(
                        archive.archive_uri, archive.overview
                    ),
                }
            abstract = await self._read_archive_abstract(archive.archive_uri, archive.overview)
            if abstract:
                pre_archive_abstracts.append(
                    {
                        "archive_id": archive.archive_id,
                        "abstract": abstract,
                        "tokens": estimate_text_tokens(abstract),
                    }
                )

        uncovered = await self._get_uncovered_archive_messages(archive_states)
        merged_messages = self._stable_deduplicate_messages(uncovered + list(self._messages))
        merged_messages = await self._insert_checkpoint_if_needed(
            merged_messages,
            archive_states,
        )

        return {
            "latest_archive": latest_archive,
            "pre_archive_abstracts": pre_archive_abstracts,
            "total_archives": len(archive_states),
            "failed_archives": failed_archives,
            "messages": merged_messages,
        }

    async def _list_archive_refs(self) -> List[Dict[str, Any]]:
        """List archive refs sorted by archive index descending."""
        if not self._viking_fs:
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

    async def _scan_archive_states(self) -> List[ArchiveState]:
        """Derive every archive state exclusively from its directory markers."""
        states: List[ArchiveState] = []
        refs = sorted(await self._list_archive_refs(), key=lambda item: item["index"])
        for archive in refs:
            done_uri = f"{archive['archive_uri']}/.done"
            try:
                done_exists = await self._viking_fs.exists(done_uri, ctx=self.ctx)
            except Exception:
                done_exists = False
            done: Dict[str, Any] = {}
            if done_exists:
                try:
                    raw_done = await self._viking_fs.read_file(done_uri, ctx=self.ctx)
                    parsed_done = json.loads(raw_done or "{}")
                    if isinstance(parsed_done, dict):
                        done = parsed_done
                except Exception as exc:
                    # Marker existence still means completion, but unreadable
                    # contents cannot extend coverage to earlier archives.
                    logger.warning(
                        "Unreadable archive done marker %s: %s", archive["archive_uri"], exc
                    )

            if done_exists:
                overview = await self._read_archive_overview(archive["archive_uri"])
                if done.get("working_memory_enabled") is True and not overview.strip():
                    # New markers distinguish an intentionally overview-less
                    # working_memory=false commit from a missing/corrupt
                    # required overview. The latter remains logically live and
                    # can be rolled forward by a later successful archive.
                    logger.warning(
                        "Completed archive has no readable required overview: %s",
                        archive["archive_uri"],
                    )
                    states.append(
                        ArchiveState(
                            archive_id=archive["archive_id"],
                            archive_uri=archive["archive_uri"],
                            index=archive["index"],
                            state="failed",
                            done=done,
                            failed={
                                "stage": "archive_overview",
                                "error": "required overview is missing or unreadable",
                            },
                        )
                    )
                    continue

                # working_memory=false legitimately writes .done without an
                # overview. Legacy markers lack the explicit flag, so retain
                # their established completed semantics for compatibility.
                states.append(
                    ArchiveState(
                        archive_id=archive["archive_id"],
                        archive_uri=archive["archive_uri"],
                        index=archive["index"],
                        state="completed",
                        overview=overview,
                        done=done,
                    )
                )
                continue

            failed: Dict[str, Any] = {}
            failed_uri = f"{archive['archive_uri']}/.failed.json"
            try:
                failed_exists = await self._viking_fs.exists(failed_uri, ctx=self.ctx)
            except Exception:
                failed_exists = False
            if failed_exists:
                try:
                    parsed_failed = json.loads(
                        await self._viking_fs.read_file(failed_uri, ctx=self.ctx) or "{}"
                    )
                    if isinstance(parsed_failed, dict):
                        failed = parsed_failed
                except Exception as exc:
                    logger.warning("Unreadable archive failed marker %s: %s", failed_uri, exc)
            states.append(
                ArchiveState(
                    archive_id=archive["archive_id"],
                    archive_uri=archive["archive_uri"],
                    index=archive["index"],
                    state="failed" if failed_exists else "pending",
                    failed=failed,
                )
            )
        return states

    @staticmethod
    def _covered_archive_ids(states: List[ArchiveState]) -> set[str]:
        """Return archives covered by an authoritative completion marker."""
        existing = {state.archive_id: state for state in states}
        covered: set[str] = set()
        for state in states:
            if state.state != "completed":
                continue
            start = max(
                1,
                min(state.coverage_start_index, state.coverage_end_index, state.index),
            )
            end = min(
                state.index,
                max(state.coverage_start_index, state.coverage_end_index),
            )
            for candidate in states:
                # A pending archive still has a live Phase 2 owner and is never
                # valid coverage input. Even malformed/manual range metadata
                # must not make its raw messages disappear.
                if start <= candidate.index <= end and candidate.state != "pending":
                    covered.add(candidate.archive_id)
            explicit = state.done.get("covered_failed_archives", [])
            if isinstance(explicit, list):
                covered.update(
                    archive_id
                    for archive_id in explicit
                    if isinstance(archive_id, str)
                    and archive_id in existing
                    and existing[archive_id].index <= state.index
                    and existing[archive_id].state == "failed"
                )
        return covered

    @staticmethod
    def _stable_deduplicate_messages(messages: List[Message]) -> List[Message]:
        """Stable-deduplicate crash/recovery overlaps by durable message id."""
        seen: set[str] = set()
        result: List[Message] = []
        for message in messages:
            if message.id in seen:
                continue
            seen.add(message.id)
            result.append(message)
        return result

    @staticmethod
    def _merge_completed_memory_steps(
        target: Dict[str, set[str]],
        raw: Any,
    ) -> None:
        """Merge durable per-step message coverage from archive metadata."""
        if not isinstance(raw, dict):
            return
        for step in _MEMORY_STEP_NAMES:
            message_ids = raw.get(step)
            if not isinstance(message_ids, list):
                continue
            target.setdefault(step, set()).update(
                item for item in message_ids if isinstance(item, str) and item
            )

    @staticmethod
    def _serialize_completed_memory_steps(
        completed: Dict[str, set[str]],
    ) -> Dict[str, List[str]]:
        return {
            step: sorted(completed.get(step, set()))
            for step in _MEMORY_STEP_NAMES
            if completed.get(step)
        }

    async def _get_completed_archive_refs(
        self,
        exclude_archive_uri: Optional[str] = None,
        before_archive_index: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return completed archive refs sorted by archive index descending."""
        completed: List[Dict[str, Any]] = []
        exclude = exclude_archive_uri.rstrip("/") if exclude_archive_uri else None

        for state in reversed(await self._scan_archive_states()):
            if exclude and state.archive_uri == exclude:
                continue
            if before_archive_index is not None and state.index >= before_archive_index:
                continue
            if state.state != "completed":
                continue
            completed.append(
                {
                    "archive_id": state.archive_id,
                    "archive_uri": state.archive_uri,
                    "index": state.index,
                }
            )

        return completed

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
        overview_tokens = estimate_text_tokens(overview)
        try:
            meta_content = await self._viking_fs.read_file(
                f"{archive_uri}/.meta.json", ctx=self.ctx
            )
            meta_tokens = int(json.loads(meta_content).get("overview_tokens", overview_tokens))
            overview_tokens = max(overview_tokens, meta_tokens)
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
        before_archive_index: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the newest readable completed archive summary."""
        for archive in await self._get_completed_archive_refs(
            exclude_archive_uri,
            before_archive_index,
        ):
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
        before_archive_index: Optional[int] = None,
    ) -> str:
        """Return the newest completed archive overview, skipping incomplete archives."""
        summary = await self._get_latest_completed_archive_summary(
            exclude_archive_uri,
            before_archive_index,
        )
        return summary["overview"] if summary else ""

    async def _read_archive_meta(self, archive_uri: str) -> Dict[str, Any]:
        try:
            content = await self._viking_fs.read_file(f"{archive_uri}/.meta.json", ctx=self.ctx)
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def _collect_checkpoint_requests_for_phase2(
        self,
        archive_uri: str,
        covered_failed_archives: List[str],
        messages: List[Message],
    ) -> List[_CheckpointRequest]:
        """Collect and validate partial-Turn checkpoint work owned by this Phase 2.

        Failed archives rolled into the current commit contribute their pending
        checkpoint sources. Requests sharing one retained user anchor are merged
        before the LLM call, so context assembly inserts one checkpoint per Turn.
        """
        archive_root = archive_uri.rstrip("/").rsplit("/", 1)[0]
        current_archive_id = archive_uri.rstrip("/").split("/")[-1]
        archive_ids = list(
            dict.fromkeys(
                [
                    archive_id
                    for archive_id in [*covered_failed_archives, current_archive_id]
                    if isinstance(archive_id, str) and re.fullmatch(r"archive_\d+", archive_id)
                ]
            )
        )
        archive_ids.sort(key=lambda item: int(item.split("_")[1]))

        message_by_id = {message.id: message for message in messages}
        message_ids = set(message_by_id)
        message_order = {message.id: index for index, message in enumerate(messages)}
        turn_anchor_by_message_id: Dict[str, Optional[str]] = {}
        for turn in build_turns(messages):
            owner_anchor_id = turn.anchor.id if turn.anchor is not None else None
            for message in turn.messages:
                turn_anchor_by_message_id[message.id] = owner_anchor_id
        merged: Dict[str, Dict[str, Any]] = {}
        for archive_id in archive_ids:
            meta = await self._read_archive_meta(f"{archive_root}/{archive_id}")
            plan = meta.get("retention_plan")
            if not isinstance(plan, dict) or not plan.get("partial_turn"):
                continue

            anchor_id = plan.get("turn_anchor_message_id")
            raw_source_ids = plan.get("checkpoint_source_message_ids")
            if not isinstance(anchor_id, str) or not anchor_id:
                raise ValueError(f"{archive_id} has a partial Turn without a valid anchor")
            if not isinstance(raw_source_ids, list) or not raw_source_ids:
                raise ValueError(
                    f"{archive_id} has a partial Turn without checkpoint source messages"
                )
            source_ids = [
                source_id
                for source_id in raw_source_ids
                if isinstance(source_id, str) and source_id
            ]
            if len(source_ids) != len(raw_source_ids):
                raise ValueError(f"{archive_id} has invalid checkpoint source message IDs")

            missing_ids = [
                message_id
                for message_id in [anchor_id, *source_ids]
                if message_id not in message_ids
            ]
            if missing_ids:
                raise ValueError(
                    f"{archive_id} checkpoint source is missing messages: {missing_ids}"
                )
            invalid_source_ids = [
                source_id
                for source_id in source_ids
                if is_user_query(message_by_id[source_id])
                or turn_anchor_by_message_id.get(source_id) != anchor_id
            ]
            if invalid_source_ids:
                raise ValueError(
                    f"{archive_id} checkpoint source is outside its Assistant/Tool prefix: "
                    f"{invalid_source_ids}"
                )

            request = merged.setdefault(
                anchor_id,
                {
                    "source_message_ids": [],
                    "retained_message_token_budget": 0,
                    "estimated_active_tokens": 0,
                },
            )
            request["source_message_ids"] = list(
                dict.fromkeys([*request["source_message_ids"], *source_ids])
            )
            # The newest plan for the same still-active Turn is authoritative.
            request["retained_message_token_budget"] = max(
                0, int(plan.get("retained_message_token_budget", 0) or 0)
            )
            request["estimated_active_tokens"] = max(
                0, int(plan.get("estimated_active_tokens", 0) or 0)
            )

        requests: List[_CheckpointRequest] = []
        for anchor_id, request in merged.items():
            source_ids = sorted(
                request["source_message_ids"],
                key=lambda message_id: message_order[message_id],
            )
            requests.append(
                _CheckpointRequest(
                    turn_anchor_message_id=anchor_id,
                    source_message_ids=tuple(source_ids),
                    retained_message_token_budget=request["retained_message_token_budget"],
                    estimated_active_tokens=request["estimated_active_tokens"],
                )
            )
        requests.sort(
            key=lambda request: min(
                message_order[source_id] for source_id in request.source_message_ids
            )
        )
        return requests

    @staticmethod
    def _build_checkpoint_records(
        requests: List[_CheckpointRequest],
        summaries: tuple[str, ...],
    ) -> List[Dict[str, Any]]:
        """Bind ordinal LLM outputs to server-owned IDs and enforce local budgets."""
        if len(summaries) != len(requests):
            raise ValueError(
                "Working Memory output returned "
                f"{len(summaries)} checkpoint summaries for {len(requests)} requests"
            )

        records: List[Dict[str, Any]] = []
        for request, raw_summary in zip(requests, summaries, strict=True):
            summary = raw_summary.strip() if isinstance(raw_summary, str) else ""
            if not summary:
                raise ValueError("Working Memory output contains an empty checkpoint summary")

            configured_budget = request.retained_message_token_budget
            if configured_budget > 0:
                available = configured_budget - request.estimated_active_tokens
                checkpoint_budget = (
                    min(1024, available) if available > 0 else min(256, configured_budget)
                )
            else:
                checkpoint_budget = 1024
            abstract = truncate_text_to_token_budget(summary, max(1, checkpoint_budget))
            if not abstract:
                raise ValueError("Checkpoint summary is empty after local token truncation")
            records.append(
                {
                    "turn_anchor_message_id": request.turn_anchor_message_id,
                    "source_message_ids": list(request.source_message_ids),
                    "abstract": abstract,
                    "estimated_tokens": estimate_text_tokens(abstract),
                }
            )
        return records

    async def _insert_checkpoint_if_needed(
        self,
        messages: List[Message],
        states: List[ArchiveState],
    ) -> List[Message]:
        """Insert persisted checkpoint products after retained anchors.

        Only completed archives are authoritative. Legacy archives without a
        ``checkpoints`` metadata field do not synthesize one from their overview.
        """
        if not messages:
            return messages

        message_ids = {message.id for message in messages}
        candidates: Dict[str, Dict[str, Any]] = {}
        for state in states:
            if state.state != "completed":
                continue
            meta = await self._read_archive_meta(state.archive_uri)
            checkpoints = meta.get("checkpoints")
            if not isinstance(checkpoints, list):
                continue
            for checkpoint in checkpoints:
                if not isinstance(checkpoint, dict):
                    continue
                anchor_id = checkpoint.get("turn_anchor_message_id")
                source_ids = checkpoint.get("source_message_ids")
                abstract = checkpoint.get("abstract")
                if not isinstance(anchor_id, str) or anchor_id not in message_ids:
                    continue
                if not isinstance(source_ids, list) or not source_ids:
                    continue
                if not isinstance(abstract, str) or not abstract.strip():
                    continue
                valid_source_ids = [
                    item for item in source_ids if isinstance(item, str) and item
                ]
                if not valid_source_ids:
                    continue

                # One long-running User Turn may be partially committed more
                # than once. Keep every disjoint completed prefix instead of
                # letting the newest checkpoint erase earlier compressed Steps.
                candidate = candidates.setdefault(
                    anchor_id,
                    {
                        "archive_id": state.archive_id,
                        "archive_uri": state.archive_uri,
                        "source_message_ids": [],
                        "abstracts": [],
                    },
                )
                seen_source_ids = set(candidate["source_message_ids"])
                new_source_ids = [
                    source_id
                    for source_id in valid_source_ids
                    if source_id not in seen_source_ids
                ]
                if not new_source_ids:
                    continue
                candidate["archive_id"] = state.archive_id
                candidate["archive_uri"] = state.archive_uri
                candidate["source_message_ids"].extend(new_source_ids)
                candidate["abstracts"].append(abstract.strip())

        if not candidates:
            return messages

        result: List[Message] = []
        for message in messages:
            result.append(message)
            candidate = candidates.get(message.id)
            if not candidate:
                continue
            abstract = "\n\n".join(candidate["abstracts"])
            if not abstract:
                continue
            result.append(
                Message(
                    id=f"checkpoint_{candidate['archive_id']}_{message.id}",
                    role="assistant",
                    parts=[
                        ContextPart(
                            uri=candidate["archive_uri"],
                            context_type="memory",
                            abstract=abstract,
                        )
                    ],
                    # The checkpoint is synthesized by OpenViking, not authored
                    # by the user who owns the retained anchor.
                    peer_id=None,
                    created_at=message.created_at,
                    turn_id=message.turn_id,
                    message_kind="checkpoint",
                    source_message_ids=candidate["source_message_ids"],
                )
            )
        return result

    async def _get_uncovered_archive_messages(
        self,
        states: Optional[List[ArchiveState]] = None,
    ) -> List[Message]:
        """Return pending/failed raw messages not covered by a completed archive."""
        states = states if states is not None else await self._scan_archive_states()
        covered = self._covered_archive_ids(states)
        messages: List[Message] = []
        for state in states:
            if state.archive_id in covered or state.state == "completed":
                continue
            messages.extend(await self._read_archive_messages(state.archive_uri))
        return self._stable_deduplicate_messages(messages)

    async def _get_pending_archive_messages(self) -> List[Message]:
        """Compatibility wrapper; uncovered includes pending and failed archives."""
        return await self._get_uncovered_archive_messages()

    @staticmethod
    def _archive_index_from_uri(archive_uri: str) -> int:
        """Parse archive_NNN suffix into an integer index."""
        match = re.search(r"archive_(\d+)$", archive_uri.rstrip("/"))
        if not match:
            raise ValueError(f"Invalid archive URI: {archive_uri}")
        return int(match.group(1))

    async def _wait_for_previous_archive_done(self, archive_index: int) -> bool:
        """Wait until every earlier archive reaches a terminal state."""
        if archive_index <= 1 or not self._viking_fs:
            return True

        while True:
            earlier_states = [
                state
                for state in await self._scan_archive_states()
                if state.index < archive_index
            ]
            pending_states = [state for state in earlier_states if state.state == "pending"]
            if not pending_states:
                non_completed = [state for state in earlier_states if state.state == "failed"]
                if non_completed:
                    logger.info(
                        "Earlier archives reached terminal non-completed states; "
                        "continuing with raw replay: %s",
                        [state.archive_id for state in non_completed],
                    )
                return True

            # A new-format intent without ready status may be left by a
            # process interruption. Reconcile it under the session lock rather
            # than waiting forever for a queue item that may never have existed.
            reconciled = False
            for state in pending_states:
                phase1 = await self._read_phase1_meta(state.archive_uri)
                if not phase1 or phase1.get("status") == "ready":
                    continue
                await self._ensure_phase1_ready(state.archive_uri)
                reconciled = True
            if reconciled:
                continue
            await asyncio.sleep(_ARCHIVE_WAIT_POLL_SECONDS)

    async def _prepare_phase2_archive_messages(
        self,
        archive_uri: str,
        current_messages: List[Message],
    ) -> tuple[List[Message], str, str, List[str], Dict[str, set[str]]]:
        """Roll earlier failed raw archives into current Phase 2 input.

        Pending archives are never replayed: their own Phase 2 job still owns
        them, and this method is called only after all earlier directories have
        reached a terminal state.
        """
        current_index = self._archive_index_from_uri(archive_uri)
        states = await self._scan_archive_states()
        covered = self._covered_archive_ids(states)
        replay_states = [
            state
            for state in states
            if state.index < current_index
            and state.archive_id not in covered
            and state.state == "failed"
        ]

        combined: List[Message] = []
        completed_memory_steps: Dict[str, set[str]] = {}
        for state in replay_states:
            combined.extend(await self._read_archive_messages(state.archive_uri))
            marker = state.failed
            self._merge_completed_memory_steps(
                completed_memory_steps,
                marker.get("completed_memory_steps") if marker else None,
            )
            state_meta = await self._read_archive_meta(state.archive_uri)
            self._merge_completed_memory_steps(
                completed_memory_steps,
                state_meta.get("completed_memory_steps"),
            )
        combined.extend(current_messages)
        combined = self._stable_deduplicate_messages(combined)

        # A restarted queue item can resume the current archive after a process
        # died between a successful memory step and the final .done write.
        current_meta = await self._read_archive_meta(archive_uri)
        self._merge_completed_memory_steps(
            completed_memory_steps,
            current_meta.get("completed_memory_steps"),
        )

        coverage_start_index = min([current_index] + [state.index for state in replay_states])
        covered_failed = [state.archive_id for state in replay_states if state.state == "failed"]
        return (
            combined,
            f"archive_{coverage_start_index:03d}",
            f"archive_{current_index:03d}",
            covered_failed,
            completed_memory_steps,
        )

    async def _merge_and_save_commit_meta(
        self,
        archive_index: int,
        memories_extracted: Dict[str, int],
        telemetry_snapshot: Any,
    ) -> None:
        """Merge Phase 2 results without overwriting concurrent root updates."""
        from openviking.storage.transaction import LockContext, get_lock_manager

        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        async with LockContext(
            get_lock_manager(),
            [session_path],
            lock_mode="exact",
            timeout=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS,
        ):
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
                latest_meta.llm_token_usage["cached_tokens"] += llm.get("prompt_cached", 0)
                latest_meta.llm_token_usage["reasoning_tokens"] += llm.get(
                    "completion_reasoning", 0
                )
                embedding = telemetry_snapshot.summary.get("tokens", {}).get("embedding", {})
                latest_meta.embedding_token_usage["total_tokens"] += embedding.get("total", 0)

            latest_meta.commit_count = max(latest_meta.commit_count, archive_index)
            for cat, count in memories_extracted.items():
                latest_meta.memories_extracted[cat] = (
                    latest_meta.memories_extracted.get(cat, 0) + count
                )
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

    async def _read_live_messages_strict(self) -> List[Message]:
        """Read the authoritative root JSONL without silently dropping corrupt rows."""
        if not self._viking_fs:
            return list(self._messages)
        content = await self._viking_fs.read_file(
            f"{self._session_uri}/messages.jsonl",
            ctx=self.ctx,
        )
        messages: List[Message] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                messages.append(Message.from_dict(json.loads(line)))
            except Exception as exc:
                raise ValueError(
                    f"Invalid live message JSONL at line {line_number}: {exc}"
                ) from exc
        return messages

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

    @classmethod
    def _format_messages_for_wm(
        cls,
        messages: List[Message],
        checkpoint_requests: List[_CheckpointRequest],
    ) -> str:
        """Format WM input and mark checkpoint sources with ordinal-only tags."""
        source_indexes: Dict[str, int] = {}
        for index, request in enumerate(checkpoint_requests):
            for message_id in request.source_message_ids:
                previous = source_indexes.setdefault(message_id, index)
                if previous != index:
                    raise ValueError(
                        f"Checkpoint source message {message_id} belongs to multiple requests"
                    )

        lines: List[str] = []
        open_index: Optional[int] = None
        for message in messages:
            index = source_indexes.get(message.id)
            if index != open_index:
                if open_index is not None:
                    lines.append("</checkpoint_source>")
                if index is not None:
                    lines.append(f'<checkpoint_source index="{index}">')
                open_index = index
            lines.append(cls._format_message_for_wm(message))
        if open_index is not None:
            lines.append("</checkpoint_source>")
        return "\n".join(lines)

    @staticmethod
    def _checkpoint_prompt_instructions(request_count: int) -> str:
        if request_count <= 0:
            return ""
        return (
            "# CHECKPOINT OUTPUT\n\n"
            f"The session content contains checkpoint_source blocks indexed 0 through "
            f"{request_count - 1}. In the SAME tool call, return checkpoint_summaries "
            f"with exactly {request_count} strings in index order. Each string must "
            "summarize only its marked block as a compact continuation note: preserve "
            "the assistant's intent, important tool actions and results, conclusions, "
            "and unfinished work; omit raw output bulk and do not mention archiving, "
            "checkpointing, or this instruction."
        )

    @staticmethod
    def _parse_required_checkpoint_summaries(
        args: Dict[str, Any],
        request_count: int,
    ) -> tuple[str, ...]:
        raw = args.get("checkpoint_summaries")
        if not isinstance(raw, list):
            raise ValueError("tool_call arguments.checkpoint_summaries missing")
        if len(raw) != request_count or not all(isinstance(item, str) for item in raw):
            raise ValueError(
                f"tool_call checkpoint_summaries must contain exactly {request_count} strings"
            )
        return tuple(raw)

    async def _generate_archive_summary_async(
        self,
        messages: List[Message],
        latest_archive_overview: str = "",
        checkpoint_requests: Optional[List[_CheckpointRequest]] = None,
    ) -> str | _ArchiveSummaryResult:
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
        _wm_debug(
            f"_generate_archive_summary_async called "
            f"messages={len(messages)} prior_wm={len(latest_archive_overview)}B"
        )
        checkpoint_requests = list(checkpoint_requests or [])
        if not messages:
            if checkpoint_requests:
                raise ValueError("Cannot generate checkpoints without archive messages")
            return ""

        formatted = self._format_messages_for_wm(messages, checkpoint_requests)
        checkpoint_instructions = self._checkpoint_prompt_instructions(len(checkpoint_requests))

        vlm = get_openviking_config().vlm
        if not (vlm and vlm.is_available()):
            if checkpoint_requests:
                raise ValueError("A configured VLM is required to generate checkpoint summaries")
            turn_count = len([m for m in messages if is_user_query(m)])
            return (
                f"# Session Summary\n\n**Overview**: {turn_count} turns, {len(messages)} messages"
            )

        try:
            from openviking.prompts import render_prompt
        except Exception as e:
            if checkpoint_requests:
                raise RuntimeError("Prompt module is required to generate checkpoints") from e
            logger.warning(f"Prompt module unavailable: {e}")
            turn_count = len([m for m in messages if is_user_query(m)])
            return (
                f"# Session Summary\n\n**Overview**: {turn_count} turns, {len(messages)} messages"
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
                    {
                        "messages": formatted,
                        "latest_archive_overview": latest_archive_overview or "",
                        "checkpoint_instructions": checkpoint_instructions,
                    },
                )
                if checkpoint_requests:
                    response = await vlm.get_completion_async(
                        prompt=prompt,
                        tools=[WM_CREATE_WITH_CHECKPOINTS_TOOL],
                        tool_choice={
                            "type": "function",
                            "function": {"name": "create_working_memory"},
                        },
                    )
                    if not (
                        getattr(response, "has_tool_calls", False)
                        and getattr(response, "tool_calls", None)
                    ):
                        raise ValueError(
                            "Working Memory creation returned no create_working_memory tool call"
                        )
                    args = response.tool_calls[0].arguments
                    if isinstance(args, str):
                        args = json.loads(args)
                    if not isinstance(args, dict):
                        raise ValueError("create_working_memory arguments must be an object")
                    working_memory = args.get("working_memory")
                    if not isinstance(working_memory, str) or not working_memory.strip():
                        raise ValueError("create_working_memory.working_memory is empty")
                    return _ArchiveSummaryResult(
                        overview=working_memory,
                        checkpoint_summaries=self._parse_required_checkpoint_summaries(
                            args,
                            len(checkpoint_requests),
                        ),
                    )
                return await vlm.get_completion_async(prompt)
            except Exception as e:
                _wm_debug(f"creation failed: {e}")
                logger.warning(f"WM creation failed: {e}")
                if checkpoint_requests:
                    raise
                turn_count = len([m for m in messages if is_user_query(m)])
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
                    "checkpoint_instructions": checkpoint_instructions,
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

            _wm_debug(f"tool_call raised: {type(e).__name__}: {e} tb={_tb.format_exc()[-400:]}")
            if checkpoint_requests:
                raise
            logger.warning("WM update tool_call failed (%s); falling back to creation prompt", e)
            return await self._fallback_generate_wm_creation(
                formatted, messages, latest_archive_overview
            )

        has_tc = bool(getattr(resp, "has_tool_calls", False) and getattr(resp, "tool_calls", None))
        _preview = (str(resp)[:200]).replace(chr(10), " ")
        _finish = getattr(resp, "finish_reason", "n/a")
        _usage = getattr(resp, "usage", {}) or {}
        _wm_debug(
            f"resp type={type(resp).__name__} has_tool_calls={has_tc} "
            f"finish_reason={_finish!r} usage={_usage} preview={_preview!r}"
        )

        if not has_tc:
            if checkpoint_requests:
                raise ValueError("Working Memory update returned no tool call for checkpoints")
            logger.warning("WM update: LLM returned no tool_call; falling back to creation prompt")
            return await self._fallback_generate_wm_creation(
                formatted, messages, latest_archive_overview
            )

        checkpoint_summaries: tuple[str, ...] = ()
        try:
            raw_args = resp.tool_calls[0].arguments
            _wm_debug(f"raw_args type={type(raw_args).__name__} preview={str(raw_args)[:400]!r}")
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
                                f"recovered by closing {opens} brace(s); patched_len={len(patched)}"
                            )
                    except Exception as e2:
                        _wm_debug(f"brace-close recovery failed: {e2}")
                if isinstance(recovered, dict):
                    args = recovered
                    _wm_debug(f"recovered args keys={list(args.keys())}")

            _wm_debug(f"args keys={list(args.keys())}")
            if checkpoint_requests:
                checkpoint_summaries = self._parse_required_checkpoint_summaries(
                    args,
                    len(checkpoint_requests),
                )
            # Tolerant: if LLM returned {"Session Title": {...}, ...} without
            # the outer "sections" wrapper, treat the top-level as ops.
            if "sections" in args and isinstance(args["sections"], dict):
                ops = args["sections"]
            elif all(k in args for k in WM_SEVEN_SECTIONS):
                _wm_debug("args has section keys directly; accepting as ops")
                ops = args
            else:
                raise ValueError(f"tool_call arguments.sections missing; keys={list(args.keys())}")
            if not isinstance(ops, dict):
                raise ValueError("ops is not a dict")
        except Exception as e:
            if checkpoint_requests:
                raise
            _wm_debug(
                f"args parse failed: {type(e).__name__}: {e}; attempting regex recovery from raw"
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
            _wm_debug("regex recovery salvaged 0 sections; falling back to creation prompt")
            logger.warning(
                "WM update: tool_call arguments parse failed (%s); "
                "regex recovery found nothing; falling back to creation prompt",
                e,
            )
            return await self._fallback_generate_wm_creation(
                formatted, messages, latest_archive_overview
            )

        _wm_debug(
            f"ops keys={list(ops.keys())[:7]} "
            f"ops_summary={[(k, v.get('op') if isinstance(v, dict) else type(v).__name__) for k, v in ops.items()][:7]}"
        )
        overview = self._merge_wm_sections(latest_archive_overview, ops)
        if checkpoint_requests:
            return _ArchiveSummaryResult(
                overview=overview,
                checkpoint_summaries=checkpoint_summaries,
            )
        return overview

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
                {
                    "messages": formatted_messages,
                    "latest_archive_overview": prior_overview,
                    "checkpoint_instructions": "",
                },
            )
            return await get_openviking_config().vlm.get_completion_async(prompt)
        except Exception as e:
            logger.warning(f"WM creation fallback failed: {e}")
            turn_count = len([m for m in messages if is_user_query(m)])
            return (
                f"# Session Summary\n\n**Overview**: {turn_count} turns, {len(messages)} messages"
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
            est_tokens = estimate_text_tokens(body)
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
    _WM_APPEND_ONLY_SECTIONS = frozenset(
        {
            "Errors & Corrections",
        }
    )

    # Very loose path-like token regex used to detect file paths that existed
    # in prior Files & Context and MUST NOT silently disappear after UPDATE.
    _WM_PATH_LIKE_RE = re.compile(
        r"(?:[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|yaml|yml|json|sh|ps1|cmd|bat|toml|ini|cfg|rs|go))"
        r"|(?:[a-zA-Z_][\w\-]*(?:/[a-zA-Z_][\w\-]*){1,})",
        re.IGNORECASE,
    )

    _WM_TITLE_STOPWORDS = frozenset(
        {
            "the",
            "a",
            "an",
            "and",
            "or",
            "of",
            "to",
            "in",
            "on",
            "for",
            "with",
            "by",
            "at",
            "from",
            "session",
            "title",
            "working",
            "memory",
            "plan",
            "plans",
            "notes",
            "note",
        }
    )

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
        keep_re = re.compile(rf'"({names_alt})"\s*:\s*\{{\s*"op"\s*:\s*"KEEP"\s*\}}')
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
            rf"([\s\S]*?)(?:\]|$)",
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
    def _wm_enforce_append_only(header: str, op: Any, old_content: str) -> Dict[str, Any]:
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
    _WM_ANCHOR_STOPWORDS = frozenset(
        {
            "the",
            "a",
            "an",
            "and",
            "or",
            "of",
            "to",
            "in",
            "on",
            "for",
            "with",
            "by",
            "at",
            "from",
            "is",
            "are",
            "was",
            "were",
            "has",
            "have",
            "had",
            "been",
            "be",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "this",
            "that",
            "these",
            "those",
            "not",
            "no",
            "but",
            "if",
            "then",
            "so",
            "as",
            "it",
            "its",
            "they",
            "their",
            "them",
            "she",
            "her",
            "he",
            "him",
            "his",
            "we",
            "our",
            "us",
            "you",
            "your",
            "who",
            "which",
            "what",
            "when",
            "where",
            "how",
            "why",
            "all",
            "each",
            "every",
            "both",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "than",
            "too",
            "very",
            "also",
            "just",
            "about",
            "after",
            "before",
            "between",
            "into",
            "through",
            "during",
            "again",
            "further",
            "once",
            "here",
            "there",
            "over",
            "under",
            "out",
            "up",
            "down",
            "off",
            "own",
            "same",
            "only",
            "new",
            "old",
            "key",
            "facts",
            "decisions",
            "session",
            "working",
            "memory",
        }
    )

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
            _wm_debug(f"guard: salvaged {len(fresh_items)} new items from rejected UPDATE")
            return {"op": "APPEND", "items": fresh_items}
        return {"op": "KEEP"}

    @staticmethod
    def _wm_enforce_key_facts_consolidation(op: Any, old_content: str) -> Dict[str, Any]:
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
            est_tokens = estimate_text_tokens(old_content or "")
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
            fresh = [it for it in append_items if it.strip("_* `").lower() not in old_lower]
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

        est_tokens = estimate_text_tokens(old_content or "")
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
            salvaged = Session._salvage_new_items_from_rejected_update(new_content, old_content)
            if is_emergency and salvaged.get("op") == "APPEND":
                _wm_debug("guard: suppressing salvage APPEND (emergency level)")
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
                salvaged = Session._salvage_new_items_from_rejected_update(new_content, old_content)
                if is_emergency and salvaged.get("op") == "APPEND":
                    _wm_debug("guard: suppressing salvage APPEND (emergency level)")
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
            f"{sorted(missing)[:5]}; forcing KEEP (+ APPEND new paths="
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
            return {t.lower() for t in tokens if t.lower() not in Session._WM_TITLE_STOPWORDS}

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
        restored = "\n".join(f"- [silently dropped, restored] {it}" for it in dropped)
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
                            len(bad_items),
                            header,
                            [type(s).__name__ for s in bad_items],
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

    async def _write_to_agfs_async(self, messages: List[Message]) -> None:
        """Write messages.jsonl to AGFS (async)."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        turn_count = len([m for m in messages if is_user_query(m)])

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
