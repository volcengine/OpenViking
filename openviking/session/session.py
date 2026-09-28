# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session management for OpenViking.

Session as Context: Sessions integrated into L0/L1/L2 system.
"""

import asyncio
import inspect
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from openviking.core.context import ContextLevel
from openviking.core.namespace import canonical_session_uri
from openviking.core.peer_id import normalize_peer_id, safe_peer_id
from openviking.message import Message, Part
from openviking.message.part import TextPart, ToolPart
from openviking.server.config import ToolOutputExternalizationConfig
from openviking.server.identity import RequestContext, Role
from openviking.session import working_memory as wm
from openviking.session.archive_store import (
    ArchiveStore,
    _ArchiveMessagesCorruptError,
    extract_abstract_from_summary,
)
from openviking.session.archive_store import (
    is_storage_not_found as _is_storage_not_found,
)
from openviking.session.auto_commit_policy import AutoCommitPolicy
from openviking.session.checkpoints import (
    CheckpointPlanner,
)
from openviking.session.checkpoints import (
    CheckpointRequest as _CheckpointRequest,
)
from openviking.session.extraction_batch import (
    ExtractionBatchLimits,
    ExtractionMessageBatch,
    estimate_extraction_message_tokens,
    plan_extraction_batches,
    resolve_extraction_batch_limits,
)
from openviking.session.memory.constants import AGENT_EVOLUTION_MEMORY_TYPES
from openviking.session.memory.utils.language import resolve_output_language_from_conversation
from openviking.session.memory_policy import MemoryPolicy
from openviking.session.retention import (
    RETENTION_MODE_TURN_BUDGET,
    RetentionPlan,
    build_turns,
    fit_active_messages_to_budget,
    is_user_query,
    plan_retention,
)
from openviking.session.tool_output_externalizer import ToolOutputExternalizer
from openviking.session.working_memory import (
    WM_CREATE_WITH_CHECKPOINTS_TOOL,
    WM_SEVEN_SECTIONS,
    WM_UPDATE_TOOL,
)
from openviking.storage.abstract_overview import render_abstract_overview
from openviking.telemetry import get_current_telemetry, tracer
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.utils.model_retry import is_retryable_api_error, retry_async
from openviking.utils.time_utils import get_current_timestamp
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.exceptions import (
    NotFoundError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger, run_async
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.config.vlm import VLMResolver
    from openviking.session.compressor_v3 import SessionCompressorV3 as SessionCompressor
    from openviking.storage import VikingDBManager
    from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
    from openviking.storage.viking_fs import VikingFS
    from openviking.usage_reporter import UsageReporter

MemoryPolicyData = Optional[Dict[str, Any]]
MemoryPolicyProvider = Callable[[], Awaitable[MemoryPolicyData]]

logger = get_logger(__name__)

_PHASE2_QUEUE_WAIT_TIMEOUT_SECONDS = 1800.0
_MEMORY_EXTRACTION_MAX_RETRIES = 3
_MEMORY_EXTRACTION_RETRY_BASE_DELAY_SECONDS = 1.0
_MEMORY_EXTRACTION_RETRY_MAX_DELAY_SECONDS = 8.0
_AGENT_TRAINING_REQUIRED_MEMORY_TYPES = frozenset({"experiences"})
_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS = 30.0


def _load_render_prompt() -> Callable[..., str]:
    from openviking.prompts import render_prompt

    return render_prompt


def _publish_telemetry_summary_best_effort(snapshot: Any) -> None:
    """Publish a completed session telemetry summary without affecting the task outcome."""
    if snapshot is None:
        return
    try:
        from openviking.metrics.datasources.telemetry_bridge import (
            TelemetryBridgeEventDataSource,
        )

        TelemetryBridgeEventDataSource.record_summary(snapshot.summary)
    except Exception:
        logger.debug("failed to publish session telemetry summary to metrics bridge", exc_info=True)


def _enabled_memory_types() -> set[str]:
    """Return enabled memory type names registered for extraction."""
    from openviking.session.memory.memory_type_registry import get_default_registry

    return set(get_default_registry().list_names(include_disabled=False))


def _validate_memory_policy_types(policy: MemoryPolicy) -> None:
    if policy.memory_types is None:
        return
    policy.validate_memory_types(_enabled_memory_types())


def _apply_agent_evolution_setting(
    policy: MemoryPolicy,
    *,
    agent_evolution_enabled: bool,
) -> MemoryPolicy:
    if agent_evolution_enabled:
        return policy
    effective_types = (
        _enabled_memory_types() if policy.memory_types is None else set(policy.memory_types)
    )
    effective_types -= AGENT_EVOLUTION_MEMORY_TYPES
    return MemoryPolicy(
        self_enabled=policy.self_enabled,
        peer_enabled=policy.peer_enabled,
        memory_types=effective_types,
        working_memory_enabled=policy.working_memory_enabled,
    )


def _effective_memory_types(policy: MemoryPolicy) -> set[str]:
    if policy.memory_types is None:
        return _enabled_memory_types()
    return set(policy.memory_types)


def _agent_memory_skip_reason(
    *,
    agent_evolution_enabled: bool,
    effective_memory_types: set[str],
    user_config_error: Optional[str] = None,
) -> Optional[str]:
    if user_config_error:
        return "invalid_user_config"
    if not agent_evolution_enabled:
        return "agent_evolution_disabled"
    if not _AGENT_TRAINING_REQUIRED_MEMORY_TYPES.issubset(effective_memory_types):
        return "memory_types_filtered"
    return None


def _default_memory_counts() -> Dict[str, int]:
    return {"total": 0}


def _resolve_event_search_tags(
    commit_tags: Optional[List[str]],
    session_default_tags: Optional[List[str]],
) -> List[str]:
    """Resolve the event tags for a commit against the session default.

    Three-state precedence:
      - ``commit_tags is None``  -> use the session default (may be empty)
      - ``commit_tags == []``    -> do not inject default tags for this commit
      - non-empty ``commit_tags`` -> override the session default

    The chosen list is normalized to canonical ``key=value`` tags.
    """
    from openviking.utils.tags import normalize_search_tags

    chosen = commit_tags if commit_tags is not None else session_default_tags
    return normalize_search_tags(chosen)


def _message_peer_ids(messages: List[Message]) -> set[str]:
    return {
        peer_id
        for message in messages
        if (peer_id := safe_peer_id(getattr(message, "peer_id", None)))
    }


@dataclass(frozen=True)
class _MemoryExtractionScope:
    allow_self_memory: bool
    peer_memory_enabled: bool
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
        peer_memory_enabled=policy.peer_enabled,
        allowed_peer_ids=allowed_peer_ids,
        include_session_skills=config_session_skill_extraction_enabled and allow_self_memory,
        memory_types=policy.memory_types,
    )


# =====================================================================
# Working Memory v2
# ---------------------------------------------------------------------
# The Working Memory document logic (7-section schema, tool definitions,
# per-section merge guards, and tool-call recovery) lives in
# ``openviking.session.working_memory``. Session composes those pure
# helpers during Phase 2. ``WM_SEVEN_SECTIONS``, ``WM_UPDATE_TOOL`` and
# ``WM_CREATE_WITH_CHECKPOINTS_TOOL`` are imported above for the model call.
# =====================================================================


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
    memories_extracted: int = 0


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
    # Automatic-commit policy. None keeps auto-commit disabled; a dict enables
    # it with the stored bounds. Session config PATCH may update it.
    auto_commit_policy: Optional[Dict[str, Any]] = None
    # Timestamp of the most recent add_message, used by the idle scan to decide
    # whether an idle-timeout commit is due.
    last_message_at: str = ""
    # Timestamp of the most recent successful auto-commit, surfaced via session
    # GET and used to throttle auto-commit frequency.
    last_auto_commit_at: str = ""
    # Default custom scalar tags applied to event memories extracted from this
    # session. Maps to config.memory_extraction_config.events.tags in the API.
    # None means no session default; a commit may still override per-call.
    event_search_tags: Optional[List[str]] = None

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
            "auto_commit_policy": (
                dict(self.auto_commit_policy) if self.auto_commit_policy is not None else None
            ),
            "last_message_at": self.last_message_at,
            "last_auto_commit_at": self.last_auto_commit_at,
        }
        if self.total_message_count is not None:
            data["total_message_count"] = self.total_message_count
        if self.event_search_tags is not None:
            data["event_search_tags"] = list(self.event_search_tags)
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
            auto_commit_policy=data.get("auto_commit_policy"),
            last_message_at=data.get("last_message_at", ""),
            last_auto_commit_at=data.get("last_auto_commit_at", ""),
            event_search_tags=data.get("event_search_tags"),
        )


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
        agent_evolution_enabled: bool = True,
        usage_reporter: Optional["UsageReporter"] = None,
        agent_evolution_enabled_provider: Optional[Callable[[], bool | Awaitable[bool]]] = None,
        memory_policy_provider: Optional[MemoryPolicyProvider] = None,
        vlm_resolver: Optional["VLMResolver"] = None,
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
        self._archives = ArchiveStore(self._viking_fs, self.ctx, self._session_uri)
        self._checkpoints = CheckpointPlanner(self._archives)
        self._tool_outputs = ToolOutputExternalizer(
            self._viking_fs, self._session_uri, self.session_id, self.ctx
        )

        self._messages: List[Message] = []
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
        self._agent_evolution_enabled = agent_evolution_enabled
        self._agent_evolution_enabled_provider = agent_evolution_enabled_provider
        self._memory_policy_provider = memory_policy_provider
        self._usage_reporter = usage_reporter
        self._vlm_resolver = vlm_resolver

    async def _get_vlm_config(self):
        if self._vlm_resolver is None:
            raise RuntimeError("Session requires a VLM resolver for account-owned work")
        return await self._vlm_resolver.get_vlm(self.ctx.account_id)

    async def _resolve_memory_policy(
        self, override: Optional[Dict[str, Any]] = None
    ) -> MemoryPolicy:
        policy = override if override is not None else self._meta.memory_policy
        if policy is None and self._memory_policy_provider is not None:
            policy = await self._memory_policy_provider()
        return MemoryPolicy.from_dict(policy)

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
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
            logger.debug(f"Session {self.session_id} not found, starting fresh")

        # Load .meta.json
        try:
            meta_content = await self._viking_fs.read_file(
                f"{self._session_uri}/.meta.json", ctx=self.ctx
            )
            self._meta = SessionMeta.from_dict(json.loads(meta_content))
            self._compression.compression_index = max(0, int(self._meta.commit_count))
            self._stats.compression_count = self._compression.compression_index
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
            # Old session without meta — derive from existing data
            try:
                history_items = await self._viking_fs.ls(
                    f"{self._session_uri}/history", ctx=self.ctx
                )
                archive_indices = [
                    int(match.group(1))
                    for item in history_items
                    if (match := re.fullmatch(r"archive_(\d+)", item["name"]))
                ]
                if archive_indices:
                    self._compression.compression_index = max(archive_indices)
                    self._stats.compression_count = len(archive_indices)
            except Exception as exc:
                if not _is_storage_not_found(exc):
                    raise
            self._meta.commit_count = self._compression.compression_index
            self._meta.total_message_count = None

        # message_count mirrors the live message list, maintained by every write
        # path. Recompute on load so a stale persisted value can't drift.
        self._meta.message_count = len(self._messages)

        if not self._meta.created_by_account_id:
            self._meta.created_by_account_id = self.ctx.account_id
        if not self._meta.created_by_user_id:
            self._meta.created_by_user_id = self.ctx.user.user_id
        # Auto-commit stays disabled when no policy is stored. When present,
        # normalize the stored policy so missing fields are filled and bounds
        # are clamped. The policy's keep_recent_count is a commit-time
        # reservation only and is intentionally NOT mirrored onto
        # meta.keep_recent_count.
        if self._meta.auto_commit_policy is not None:
            self._meta.auto_commit_policy = AutoCommitPolicy.from_dict(
                self._meta.auto_commit_policy
            ).to_dict()
        # WM v2: always rebuild pending_tokens from current messages so the
        # counter stays consistent across restarts and is also backfilled for
        # legacy sessions whose .meta.json predates these fields. O(n) once,
        # subsequent add_message() maintains it in O(1).
        await self._rebuild_pending_tokens()

        self._loaded = True

    async def _rebuild_pending_tokens(self) -> None:
        """Recompute ``pending_tokens`` from the current message list.

        Used on load, turn-budget appends, and recovery. Respects the current
        retention settings from meta.
        """
        pending_tokens = await asyncio.to_thread(self._calculate_pending_tokens)
        self._meta.pending_tokens = max(0, pending_tokens)

    def _calculate_pending_tokens(self) -> int:
        """Calculate pending tokens without mutating session state."""
        if (
            self._meta.retention_mode == RETENTION_MODE_TURN_BUDGET
            and self._meta.retained_message_token_budget > 0
        ):
            plan = plan_retention(
                self._messages,
                keep_recent_turn_count=self._meta.keep_recent_turn_count,
                token_budget=self._meta.retained_message_token_budget,
                min_raw_tail_steps=self._meta.min_raw_tail_steps,
            )
            retained_ids = {message.id for message in plan.retained_messages}
            return sum(
                int(message.estimated_tokens or 0)
                for message in plan.archive_messages
                if message.id not in retained_ids
            )

        keep = max(0, int(self._meta.keep_recent_count or 0))
        total = len(self._messages)
        if keep <= 0:
            return sum(int(m.estimated_tokens or 0) for m in self._messages)
        if total > keep:
            return sum(int(m.estimated_tokens or 0) for m in self._messages[: total - keep])
        return 0

    async def exists(self) -> bool:
        """Check whether this session already exists in storage."""
        try:
            await self._viking_fs.stat(self._session_uri, ctx=self.ctx, skip_count=True)
            return True
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
            return False

    async def is_materialized(self) -> bool:
        """Check whether the session's authoritative live-message file exists."""
        try:
            await self._viking_fs.stat(
                f"{self._session_uri}/messages.jsonl",
                ctx=self.ctx,
            )
            return True
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
            return False

    async def ensure_exists(self) -> None:
        """Materialize session root and messages file if missing."""
        if await self.exists():
            return
        await self._viking_fs.mkdir(self._session_uri, exist_ok=True, ctx=self.ctx)
        await self._viking_fs.write_file(
            f"{self._session_uri}/messages.jsonl",
            "",
            ctx=self.ctx,
        )
        await self._save_meta()

    async def _save_meta(self, lease_ref: Optional[Any] = None) -> None:
        """Persist .meta.json to storage using an optional held PathLock lease."""
        if not self._viking_fs:
            return
        self._meta.updated_at = get_current_timestamp()
        await self._viking_fs.write_file(
            uri=f"{self._session_uri}/.meta.json",
            content=json.dumps(self._meta.to_dict(), ensure_ascii=False),
            ctx=self.ctx,
            lease_ref=lease_ref,
        )

    async def update_config(
        self,
        *,
        event_search_tags: Optional[List[str]] = None,
        auto_commit_policy: Optional[Dict[str, Any]] = None,
        update_auto_commit_policy: bool = False,
    ) -> None:
        """Update mutable session config without overwriting concurrent meta changes."""
        update_auto_commit_policy = update_auto_commit_policy or auto_commit_policy is not None
        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        lease = await self._viking_fs._async_agfs.pathlock_acquire_exact(
            session_path, timeout_secs=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS
        )
        try:
            try:
                meta_content = await self._viking_fs.read_file(
                    f"{self._session_uri}/.meta.json",
                    ctx=self.ctx,
                )
                self._meta = SessionMeta.from_dict(json.loads(meta_content))
            except Exception as exc:
                if not _is_storage_not_found(exc):
                    raise
            if event_search_tags is not None:
                self._meta.event_search_tags = list(event_search_tags)
            if update_auto_commit_policy:
                if auto_commit_policy is None:
                    self._meta.auto_commit_policy = None
                else:
                    existing = dict(self._meta.auto_commit_policy or {})
                    existing.update(auto_commit_policy)
                    self._meta.auto_commit_policy = AutoCommitPolicy.from_dict(existing).to_dict()
            await self._save_meta()
        finally:
            await self._viking_fs._async_agfs.pathlock_release(lease)

    async def update_event_search_tags(self, event_search_tags: List[str]) -> None:
        """Update event-memory default tags."""
        await self.update_config(event_search_tags=event_search_tags)

    @property
    def messages(self) -> List[Message]:
        """Get message list."""
        return self._messages

    @property
    def meta(self) -> SessionMeta:
        """Get session metadata."""
        return self._meta

    # ============= Core methods =============

    def _is_tool_result_aggregate(self, role: str, parts: List[Part]) -> bool:
        return (
            role == "user" and len(parts) > 1 and all(isinstance(part, ToolPart) for part in parts)
        )

    async def _append_messages_authoritatively(self, messages: List[Message]) -> None:
        """Reload and append under the session path lock.

        Different workers can hold stale Session objects. Without sharing the
        commit lock, an append between commit's root read and root rewrite can
        be overwritten even though add_message already returned successfully.
        """
        if not messages:
            return
        if not self._viking_fs:
            await self._apply_appended_messages_to_state(messages)
            return

        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        lease = await self._viking_fs._async_agfs.pathlock_acquire_exact(
            session_path, timeout_secs=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS
        )
        try:
            live_messages_missing = False
            try:
                self._messages = await self._read_live_messages_strict()
            except Exception as exc:
                if not _is_storage_not_found(exc):
                    raise
                self._messages = []
                live_messages_missing = True
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

            await self._apply_appended_messages_to_state(messages)
            batch_content = "".join(message.to_jsonl() + "\n" for message in messages)
            if live_messages_missing:
                await self._viking_fs.write_file(
                    f"{self._session_uri}/messages.jsonl",
                    batch_content,
                    ctx=self.ctx,
                )
            else:
                await self._viking_fs.append_file(
                    f"{self._session_uri}/messages.jsonl",
                    batch_content,
                    ctx=self.ctx,
                )
            await self._save_meta()
        finally:
            await self._viking_fs._async_agfs.pathlock_release(lease)

    async def _apply_appended_messages_to_state(self, messages: List[Message]) -> None:
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
            await self._rebuild_pending_tokens()

        self._meta.message_count = len(self._messages)
        if self._meta.total_message_count is not None:
            self._meta.total_message_count += len(messages)
        if messages:
            # Track the newest activity so the idle scan can decide when an
            # idle-timeout auto-commit is due. Written under the same append
            # path lock as the counters above.
            self._meta.last_message_at = get_current_timestamp()

    def _build_message_groups(
        self,
        messages_spec: List[dict],
    ) -> List[List[Message]]:
        """Build messages grouped by input spec, preserving tool-output budgets.

        Args:
            messages_spec: List of dicts, each with keys:
                role, parts, peer_id/created_at and optional semantic fields.
        """
        message_groups = []
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
                message_groups.append(msgs)
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
                message_groups.append([msg])

        return message_groups

    def add_messages(
        self,
        messages_spec: List[dict],
    ) -> List[Message]:
        """Synchronously add multiple messages in one authoritative batch."""
        return run_async(self.add_messages_async(messages_spec))

    async def add_messages_async(
        self,
        messages_spec: List[dict],
    ) -> List[Message]:
        """Asynchronously add multiple messages without blocking the caller loop."""
        message_groups = self._build_message_groups(messages_spec)
        messages = []
        for group in message_groups:
            await self._tool_outputs.externalize_group(
                group, self._tool_output_externalization_config
            )
            messages.extend(group)
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
        return await self._tool_outputs.read_tool_result(
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
        return await self._tool_outputs.search_tool_result(
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
        return await self._tool_outputs.list_tool_results(tool_name=tool_name, limit=limit)

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

    async def _merge_archive_meta(
        self,
        archive_uri: str,
        updates: Dict[str, Any],
        lease_ref: Optional[Any] = None,
    ) -> None:
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
                lease_ref=lease_ref,
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
        agent_evolution_enabled: bool = True,
        agent_memory_skip_reason: Optional[str] = None,
        lease_ref: Optional[Any] = None,
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
        await self._merge_archive_meta(
            archive_uri,
            {
                "phase1": payload,
                "agent_evolution": {
                    "enabled": agent_evolution_enabled,
                    "skip_reason": agent_memory_skip_reason,
                },
            },
            lease_ref=lease_ref,
        )

    async def _write_phase1_ready_marker(
        self,
        archive_uri: str,
        lease_ref: Optional[Any] = None,
    ) -> None:
        """Persist that Phase 1 is ready using an optional held PathLock lease."""
        phase1 = await self._read_phase1_meta(archive_uri)
        phase1.update(
            {
                "status": "ready",
                "ready_at": get_current_timestamp(),
            }
        )
        await self._merge_archive_meta(archive_uri, {"phase1": phase1}, lease_ref=lease_ref)

    async def _archive_file_exists(self, archive_uri: str, file_name: str) -> bool:
        try:
            return await self._viking_fs.exists(f"{archive_uri}/{file_name}", ctx=self.ctx)
        except Exception:
            return False

    async def _read_phase1_meta(self, archive_uri: str) -> Dict[str, Any]:
        phase1 = (await self._archives.read_meta(archive_uri)).get("phase1")
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
        if await self._archives.file_exists(archive_uri, ".failed.json"):
            return False

        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        lease = await self._viking_fs._async_agfs.pathlock_acquire_exact(
            session_path, timeout_secs=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS
        )
        try:
            marker = await self._read_phase1_meta(archive_uri)
            if marker.get("status") == "ready":
                return True
            if await self._archives.file_exists(archive_uri, ".failed.json"):
                return False

            queue_message = marker.get("queue_message")
            task_id = queue_message.get("task_id") if isinstance(queue_message, dict) else None
            from openviking.service.task_tracker import get_task_tracker

            tracker = get_task_tracker()
            if not task_id or not tracker.has_work(str(task_id)):
                error = "Phase 1 has no QueueFS work to resume"
                await self._write_failed_marker(
                    archive_uri,
                    stage="phase1_recovery",
                    error=error,
                )
                if task_id:
                    await tracker.fail(
                        str(task_id),
                        error,
                        account_id=self.ctx.account_id,
                        user_id=self.ctx.user.user_id,
                    )
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
            phase1_applied = live_ids[
                : len(retained_ids)
            ] == retained_ids and not archived_only_ids.intersection(live_ids)
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
                keep_recent_turn_count=max(0, int(marker.get("keep_recent_turn_count", 0) or 0)),
                retained_message_token_budget=max(
                    0, int(marker.get("retained_message_token_budget", 0) or 0)
                ),
                min_raw_tail_steps=max(0, int(marker.get("min_raw_tail_steps", 1) or 0)),
            )
            self._meta.message_count = len(live_messages)
            self._meta.commit_count = max(
                self._meta.commit_count,
                self._archives.archive_index_from_uri(archive_uri),
            )
            self._meta.last_commit_at = get_current_timestamp()
            await self._rebuild_pending_tokens()
            await self._save_meta()
            await self._write_phase1_ready_marker(archive_uri)
            logger.warning("Recovered interrupted Session Phase 1: %s", archive_uri)
            return True
        finally:
            await self._viking_fs._async_agfs.pathlock_release(lease)

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
        persist_keep_recent_count: bool = True,
        record_auto_commit_success: bool = False,
        event_tags: Optional[List[str]] = None,
        reset_context: bool = False,
    ) -> Dict[str, Any]:
        """Archive immediately and enqueue restart-safe Phase 2 processing.

        Phase 1 (Archive prep, path-lock protected): Split messages into
        archive/retain parts, persist a recoverable intent and archive raw,
        enqueue Phase 2, then publish the retained root state with
        ``phase1.status=ready``. Uses a distributed filesystem lock across
        workers and processes.
        Phase 2 (Memory extraction): Runs through the persistent QueueFS queue.

        Args:
            keep_recent_count: Number of most-recent messages to keep in the
                live session after commit. ``0`` (default) preserves the old
                behavior of archiving everything. The plugin's afterTurn path
                typically passes its configured value (default 10); the compact
                path passes ``0``.
            reset_context: Archive all live messages, then append an empty completed
                archive to stop context and future summaries at this boundary.
            persist_keep_recent_count: When ``True`` (default), ``keep_recent_count``
                is remembered in meta for subsequent add_message() accounting.
                The idle full-commit path passes ``False`` with
                ``keep_recent_count=0`` so a one-off full archive does not wipe
                the stored keep preference.
            record_auto_commit_success: When ``True``, clear the auto-commit
                error fields and stamp ``last_auto_commit_at`` in the same
                lock-protected meta update as the commit boundary.
            event_tags: Per-commit override for the custom scalar tags applied
                to event memories. ``None`` uses the session default
                (``meta.event_search_tags``); an empty list disables default-tag
                injection for this commit; a non-empty list overrides it.

        Returns a task_id for tracking Phase 2 progress.
        """
        from openviking.service.task_tracker import get_task_tracker
        from openviking.storage.queuefs import QueueManager, get_queue_manager
        from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg

        if reset_context and (keep_recent_count != 0 or retention_mode is not None):
            raise ValueError("reset_context requires keep_recent_count=0 and no retention_mode")
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
        agent_evolution_enabled = self._agent_evolution_enabled
        if self._agent_evolution_enabled_provider is not None:
            provided_enabled = self._agent_evolution_enabled_provider()
            agent_evolution_enabled = (
                await provided_enabled
                if inspect.isawaitable(provided_enabled)
                else provided_enabled
            )
        if memory_policy is not None:
            effective_policy = await self._resolve_memory_policy(memory_policy)
            _validate_memory_policy_types(effective_policy)
            effective_policy = _apply_agent_evolution_setting(
                effective_policy,
                agent_evolution_enabled=agent_evolution_enabled,
            )
            effective_memory_policy = effective_policy.to_dict()
            effective_memory_types = sorted(_effective_memory_types(effective_policy))
            agent_memory_skip_reason = _agent_memory_skip_reason(
                agent_evolution_enabled=agent_evolution_enabled,
                effective_memory_types=set(effective_memory_types),
            )
        logger.info(
            f"[TRACER] session_commit started, trace_id={trace_id}, "
            f"keep_recent_count={keep_recent_count}, retention_mode={retention_mode}, "
            f"keep_recent_turn_count={effective_keep_turns}, "
            f"retained_message_token_budget={effective_token_budget}"
        )

        # ===== Phase 1: authoritative snapshot + split (path-lock protected) =====
        # The exact lock on the Session directory is the mutex for root Session
        # state and archive-number allocation. Child files use their own exact
        # locks, so Phase 2 can keep writing an earlier archive concurrently.
        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        lease = await self._viking_fs._async_agfs.pathlock_acquire_exact(
            session_path, timeout_secs=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS
        )
        try:
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

            effective_event_tags = _resolve_event_search_tags(
                event_tags, self._meta.event_search_tags
            )

            # keep_recent_count controls how many live messages survive this
            # commit. stored_keep_recent_count is what we persist for future
            # add_message accounting: the idle full-commit path archives
            # everything once (keep_recent_count=0) without discarding the
            # caller's stored keep preference. Read it from the freshly reloaded
            # meta so a concurrent manual commit's value is not reverted.
            stored_keep_recent_count = (
                keep_recent_count
                if persist_keep_recent_count
                else max(0, int(self._meta.keep_recent_count or 0))
            )

            # A Session object may have been loaded by another worker before a
            # different worker updated the persisted default policy. Phase 2
            # must use the policy from the same lock-protected snapshot as the
            # messages being archived, unless this commit supplied an explicit
            # override.
            if memory_policy is None:
                effective_policy = await self._resolve_memory_policy()
                _validate_memory_policy_types(effective_policy)
                effective_policy = _apply_agent_evolution_setting(
                    effective_policy,
                    agent_evolution_enabled=agent_evolution_enabled,
                )
                effective_memory_policy = effective_policy.to_dict()
                effective_memory_types = sorted(_effective_memory_types(effective_policy))
                agent_memory_skip_reason = _agent_memory_skip_reason(
                    agent_evolution_enabled=agent_evolution_enabled,
                    effective_memory_types=set(effective_memory_types),
                )

            self._compression.compression_index = max(
                self._compression.compression_index,
                int(self._meta.commit_count),
            )
            while await self._viking_fs.exists(
                (
                    f"{self._session_uri}/history/"
                    f"archive_{self._compression.compression_index + 1:03d}"
                ),
                ctx=self.ctx,
            ):
                self._compression.compression_index += 1

            if not self._messages:
                self._meta.pending_tokens = 0
                self._remember_retention_policy(
                    keep_recent_count=stored_keep_recent_count,
                    retention_mode=retention_mode,
                    keep_recent_turn_count=effective_keep_turns if turn_mode else 0,
                    retained_message_token_budget=effective_token_budget if turn_mode else 0,
                    min_raw_tail_steps=effective_min_tail,
                )
                await self._save_meta()
                if reset_context:
                    await self._append_context_reset_archive()
                get_current_telemetry().set("memory.extracted", 0)
                return {
                    "session_id": self.session_id,
                    "status": "skipped",
                    "task_id": None,
                    "archive_uri": None,
                    "archived": False,
                    "reason": "no_messages",
                    "trace_id": trace_id,
                    **({"reset_context": True} if reset_context else {}),
                }

            total = len(self._messages)
            retention_plan: Optional[RetentionPlan] = None
            if turn_mode:
                # The externalization budget belongs to a logical Turn, not one
                # physical assistant message. This catches N small tool outputs
                # whose aggregate exceeds the configured inline budget.
                for turn in build_turns(self._messages):
                    await self._tool_outputs.externalize_group(
                        turn.messages, self._tool_output_externalization_config
                    )
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
                    keep_recent_count=stored_keep_recent_count,
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
            task_id = str(uuid4())
            queue_msg = SessionCommitMsg(
                task_id=task_id,
                session_id=self.session_id,
                session_uri=self._session_uri,
                archive_uri=archive_uri,
                user=self.ctx.user.to_dict(),
                memory_policy=effective_memory_policy,
                record_auto_commit_success=record_auto_commit_success,
                event_search_tags=list(effective_event_tags),
                auto_commit_policy=dict(self._meta.auto_commit_policy or {}),
            )
            phase1_stage = "phase1_persist"
            try:
                archive_persist_tasks = [
                    self._write_phase1_marker(
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
                        agent_evolution_enabled=agent_evolution_enabled,
                        agent_memory_skip_reason=agent_memory_skip_reason,
                    )
                ]
                if self._viking_fs:
                    lines = [m.to_jsonl() for m in messages_to_archive]
                    archive_persist_tasks.append(
                        self._viking_fs.write_file(
                            uri=f"{archive_uri}/messages.jsonl",
                            content="\n".join(lines) + "\n",
                            ctx=self.ctx,
                        )
                    )
                archive_persist_results = await asyncio.gather(
                    *archive_persist_tasks,
                    return_exceptions=True,
                )
                for result in archive_persist_results:
                    if isinstance(result, BaseException):
                        raise result
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
                    keep_recent_count=stored_keep_recent_count,
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
                if record_auto_commit_success:
                    # Stamp success in the same lock-protected meta write as the
                    # commit boundary, so an idle scan and a concurrent worker
                    # never see a stale state.
                    self._meta.last_auto_commit_at = get_current_timestamp()
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
            if reset_context:
                await self._append_context_reset_archive()
        finally:
            await self._viking_fs._async_agfs.pathlock_release(lease)
        # Lock released; Phase 1 intent, queue item, retained root, metadata and
        # ready metadata are all durable.

        self._compression.original_count += len(messages_to_archive)
        logger.info(f"Archived: {len(messages_to_archive)} messages → {archive_uri}/")

        return {
            "session_id": self.session_id,
            "status": "accepted",
            "task_id": task_id,
            "archive_uri": archive_uri,
            "archived": True,
            "trace_id": trace_id,
            **({"reset_context": True} if reset_context else {}),
            "estimated_active_tokens": (
                retention_plan.estimated_active_tokens if retention_plan else 0
            ),
            "budget_exceeded": retention_plan.budget_exceeded if retention_plan else False,
        }

    async def _append_context_reset_archive(self) -> None:
        """Publish a boundary archive while holding the Phase 1 session lock.

        The directory holds only ``.done``: terminal archives never have their
        ``messages.jsonl`` read, and a missing overview already reads as empty.
        """
        # ponytail: reuse archive ordering; no second session identity or context store.
        newest = f"{self._session_uri}/history/archive_{self._compression.compression_index:03d}"
        if self._compression.compression_index > 0 and await self._archives.is_context_reset_archive(newest):
            return  # Context is already empty; no second boundary needed.
        self._compression.compression_index += 1
        archive_uri = (
            f"{self._session_uri}/history/archive_{self._compression.compression_index:03d}"
        )
        try:
            await self._viking_fs.write_file(
                f"{archive_uri}/.done",
                json.dumps({"context_reset": True, "working_memory_enabled": False}),
                ctx=self.ctx,
            )
        except Exception as exc:
            # A directory left without any marker reads as pending and would
            # block the next Phase 2 forever; no queue owns this archive.
            await self._write_failed_marker(archive_uri, stage="context_reset", error=str(exc))
            raise
        self._meta.commit_count = self._compression.compression_index
        await self._save_meta()

    async def finalize_cancelled_commit(self, archive_uri: str) -> None:
        """Make a cancelled queued commit terminal without discarding its raw archive."""
        await self._write_failed_marker(
            archive_uri,
            stage="cancelled",
            error="session commit cancelled",
        )

    async def resume_queued_commit(self, msg: "SessionCommitMsg") -> bool:
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
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
        else:
            if task.status.value == "completed":
                return True
            await tracker.complete(
                msg.task_id,
                {"session_id": self.session_id, "archive_uri": msg.archive_uri},
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            return True

        try:
            failed = json.loads(
                await self._viking_fs.read_file(f"{msg.archive_uri}/.failed.json", ctx=self.ctx)
            )
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
        else:
            await tracker.fail(
                msg.task_id,
                str(failed.get("error") or "session commit failed"),
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            return True

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
            return True

        if not await self._can_run_archive(self._archives.archive_index_from_uri(msg.archive_uri)):
            return False

        archive_error = ""
        try:
            archive_messages = await self._archives.read_messages(msg.archive_uri)
        except _ArchiveMessagesCorruptError as exc:
            archive_messages = []
            archive_error = f"session commit archive has invalid messages: {exc}"
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
            archive_messages = []
        if not archive_messages:
            error = archive_error or "session commit archive has no messages"
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
            return True

        queued_policy = MemoryPolicy.from_dict(msg.memory_policy)
        archive_meta = await self._archives.read_meta(msg.archive_uri)
        agent_evolution_snapshot = archive_meta.get("agent_evolution")
        if not isinstance(agent_evolution_snapshot, dict):
            # Compatibility with jobs created by pre-merge development builds
            # that temporarily stored this snapshot in the queue payload.
            phase1 = archive_meta.get("phase1")
            queue_snapshot = phase1.get("queue_message") if isinstance(phase1, dict) else None
            agent_evolution_snapshot = queue_snapshot if isinstance(queue_snapshot, dict) else {}

        snapshot_enabled = agent_evolution_snapshot.get("enabled")
        if snapshot_enabled is None:
            snapshot_enabled = agent_evolution_snapshot.get("agent_evolution_enabled")
        # Archives created before this setting existed preserve their original
        # behavior, where Agent memory extraction was enabled by default.
        agent_evolution_enabled = snapshot_enabled if isinstance(snapshot_enabled, bool) else True
        agent_memory_skip_reason = agent_evolution_snapshot.get("skip_reason")
        if agent_memory_skip_reason is None:
            agent_memory_skip_reason = agent_evolution_snapshot.get("agent_memory_skip_reason")
        if agent_memory_skip_reason is None:
            agent_memory_skip_reason = _agent_memory_skip_reason(
                agent_evolution_enabled=agent_evolution_enabled,
                effective_memory_types=_effective_memory_types(queued_policy),
            )
        user_config_error = agent_evolution_snapshot.get("user_config_error")
        if user_config_error is not None:
            user_config_error = str(user_config_error)

        await self._run_memory_extraction(
            task_id=msg.task_id,
            archive_uri=msg.archive_uri,
            messages=archive_messages,
            first_message_id=archive_messages[0].id,
            last_message_id=archive_messages[-1].id,
            memory_policy=msg.memory_policy,
            agent_evolution_enabled=agent_evolution_enabled,
            agent_memory_skip_reason=agent_memory_skip_reason,
            user_config_error=user_config_error,
            record_auto_commit_success=msg.record_auto_commit_success,
            event_search_tags=list(msg.event_search_tags or []),
            auto_commit_policy=msg.auto_commit_policy,
        )
        return True

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

    async def _extract_long_term_memories_with_batching(
        self,
        *,
        messages: List[Message],
        limits: ExtractionBatchLimits,
        archive_uri: str,
        extract_batch: Callable[[List[Message]], Awaitable[Any]],
        record_batch: Callable[
            [str, str, List[Message], Callable[[], Awaitable[Any]]],
            Awaitable[Any],
        ],
    ) -> Any:
        batches = plan_extraction_batches(messages, limits)
        if not batches:
            return []

        logger.info(
            "Processing Phase 2 long-term memory extraction in %s planned batches "
            "for %s estimated tokens",
            len(batches),
            estimate_extraction_message_tokens(messages),
        )
        contexts: List[Any] = []
        skills: List[Dict[str, Any]] = []
        memory_diffs: List[Dict[str, Any]] = []
        previous_diff_raw = ""
        if self._viking_fs:
            try:
                previous_diff_raw = await self._viking_fs.read_file(
                    f"{archive_uri}/memory_diff.json",
                    ctx=self.ctx,
                )
                previous_diff = json.loads(previous_diff_raw or "{}")
                if isinstance(previous_diff, dict):
                    memory_diffs.append(previous_diff)
            except Exception as exc:
                if not _is_storage_not_found(exc):
                    raise
                previous_diff_raw = ""

        for batch_number, batch in enumerate(batches, start=1):
            batch_messages = list(batch.messages)

            async def _extract_and_merge(
                batch_messages: List[Message] = batch_messages,
            ) -> Any:
                nonlocal previous_diff_raw
                result = await extract_batch(batch_messages)
                if not self._viking_fs:
                    return result
                try:
                    current_diff_raw = await self._viking_fs.read_file(
                        f"{archive_uri}/memory_diff.json",
                        ctx=self.ctx,
                    )
                    if current_diff_raw != previous_diff_raw:
                        current_diff = json.loads(current_diff_raw or "{}")
                        if isinstance(current_diff, dict):
                            memory_diffs.append(current_diff)
                            await self._session_compressor._write_final_memory_diff(
                                archive_uri=archive_uri,
                                ctx=self.ctx,
                                memory_diffs=memory_diffs,
                            )
                            previous_diff_raw = await self._viking_fs.read_file(
                                f"{archive_uri}/memory_diff.json",
                                ctx=self.ctx,
                            )
                except Exception as exc:
                    if not _is_storage_not_found(exc):
                        raise
                return result

            result = await record_batch(
                f"long_term_memory_extraction_batch_{batch_number}",
                "long_term",
                batch_messages,
                _extract_and_merge,
            )
            if isinstance(result, dict):
                contexts.extend(result.get("contexts", []))
                skills.extend(result.get("session_skills", []))
            else:
                contexts.extend(result or [])

        if skills:
            return {"contexts": contexts, "session_skills": skills}
        return contexts

    @tracer("session.commit.phase2", ignore_result=True, ignore_args=True)
    async def _run_memory_extraction(
        self,
        task_id: str,
        archive_uri: str,
        messages: List[Message],
        first_message_id: str,
        last_message_id: str,
        memory_policy: Optional[Dict[str, Any]],
        agent_evolution_enabled: bool = True,
        agent_memory_skip_reason: Optional[str] = None,
        user_config_error: Optional[str] = None,
        record_auto_commit_success: bool = False,
        event_search_tags: Optional[List[str]] = None,
        auto_commit_policy: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Phase 2: Extract memories and enqueue semantic work in the background."""
        from openviking.service.task_tracker import get_task_tracker
        from openviking.telemetry import OperationTelemetry, bind_telemetry
        from openviking.telemetry.registry import register_telemetry, unregister_telemetry

        tracker = get_task_tracker()
        request_wait_tracker = get_request_wait_tracker()

        memories_extracted: Dict[str, int] = {}
        usage_events_extracted = 0
        extracted_skill_results: list[dict] = []
        skipped_memory_operations: list[dict[str, Any]] = []
        memory_diff_uri: Optional[str] = None
        completed_memory_steps: Dict[str, set[str]] = {}
        telemetry = OperationTelemetry(operation="session_commit_phase2", enabled=True)
        archive_index = self._archives.archive_index_from_uri(archive_uri)

        try:
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
                    extraction_batch_limits = resolve_extraction_batch_limits(auto_commit_policy)
                    working_memory_enabled = effective_policy.working_memory_enabled
                    checkpoint_requests = (
                        await self._checkpoints.collect_requests_for_phase2(
                            archive_uri,
                            covered_failed_archives,
                            messages,
                        )
                        if working_memory_enabled
                        else []
                    )
                    latest_archive_overview = (
                        await self._archives.latest_completed_overview(
                            exclude_archive_uri=archive_uri,
                            before_archive_index=archive_index,
                        )
                        if working_memory_enabled
                        else ""
                    )
                    extraction_messages = await self._tool_outputs.hydrate_for_extraction(messages)
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
                        summary_batches = plan_extraction_batches(
                            extraction_messages,
                            extraction_batch_limits,
                        )
                        if checkpoint_requests or len(summary_batches) <= 1:
                            generated = await self._generate_archive_summary_async(
                                extraction_messages,
                                **summary_kwargs,
                            )
                        else:
                            generated = await self._generate_archive_summary_with_batching(
                                summary_batches,
                                limits=extraction_batch_limits,
                                **summary_kwargs,
                            )
                        summary_result = (
                            generated
                            if isinstance(generated, _ArchiveSummaryResult)
                            else _ArchiveSummaryResult(overview=str(generated or ""))
                        )
                        checkpoint_records = self._checkpoints.build_records(
                            checkpoint_requests,
                            summary_result.checkpoint_summaries,
                        )
                        summary = summary_result.overview
                        if checkpoint_requests and not summary.strip():
                            raise ValueError(
                                "Working Memory output is empty for a required checkpoint"
                            )
                        if self._viking_fs and summary:
                            abstract = extract_abstract_from_summary(summary)
                            await self._viking_fs.write_file(
                                uri=f"{archive_uri}/.abstract.md",
                                content=render_abstract_overview(
                                    ContextLevel.ABSTRACT,
                                    archive_uri,
                                    abstract,
                                    {
                                        "generated_by": {
                                            "component": "Session",
                                            "trigger": "archive_summary",
                                        }
                                    },
                                ),
                                ctx=self.ctx,
                            )
                            await self._viking_fs.write_file(
                                uri=f"{archive_uri}/.overview.md",
                                content=render_abstract_overview(
                                    ContextLevel.OVERVIEW,
                                    archive_uri,
                                    summary,
                                    {
                                        "generated_by": {
                                            "component": "Session",
                                            "trigger": "archive_summary",
                                        }
                                    },
                                ),
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
                                    self._archives.serialize_completed_memory_steps(completed_memory_steps)
                                )
                            },
                        )
                        return result

                    # Summary and V3 long-term memory extraction run concurrently.
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
                    peer_memory_enabled = extraction_scope.peer_memory_enabled
                    allowed_peer_ids = extraction_scope.allowed_peer_ids
                    long_term_memory_types = extraction_scope.memory_types

                    long_term_messages = [
                        message
                        for message in extraction_messages
                        if message.id not in completed_memory_steps.get("long_term", set())
                    ]

                    long_term_has_work = (
                        memory_extraction_enabled
                        and (self_memory_enabled or allowed_peer_ids)
                        and (long_term_memory_types is None or bool(long_term_memory_types))
                        and bool(long_term_messages)
                    )
                    if working_memory_enabled or (self._session_compressor and long_term_has_work):
                        logger.info(
                            "Starting post-commit extraction from %s archived messages",
                            len(messages),
                        )

                        extraction_tasks: List[Any] = []
                        extraction_labels: List[str] = []
                        if working_memory_enabled:
                            extraction_tasks.append(_run_archive_summary())
                            extraction_labels.append("archive_summary")

                        if self._session_compressor and long_term_has_work:

                            async def _run_long_term_memory_extraction(
                                batch_messages: Optional[List[Message]] = None,
                            ) -> Any:
                                return await self._session_compressor.extract_long_term_memories(
                                    messages=(
                                        long_term_messages
                                        if batch_messages is None
                                        else batch_messages
                                    ),
                                    user=self.user,
                                    session_id=self.session_id,
                                    ctx=self.ctx,
                                    strict_extract_errors=True,
                                    latest_archive_overview=latest_archive_overview,
                                    archive_uri=archive_uri,
                                    allowed_memory_types=long_term_memory_types,
                                    agent_evolution_enabled=agent_evolution_enabled,
                                    allow_self_memory=self_memory_enabled,
                                    peer_memory_enabled=peer_memory_enabled,
                                    allowed_peer_ids=allowed_peer_ids,
                                    event_search_tags=event_search_tags,
                                )

                            if extraction_batch_limits.enabled:
                                extraction_tasks.append(
                                    self._extract_long_term_memories_with_batching(
                                        messages=long_term_messages,
                                        limits=extraction_batch_limits,
                                        archive_uri=archive_uri,
                                        extract_batch=_run_long_term_memory_extraction,
                                        record_batch=_run_recorded_memory_step,
                                    )
                                )
                            else:
                                extraction_tasks.append(
                                    _run_recorded_memory_step(
                                        "long_term_memory_extraction",
                                        "long_term",
                                        long_term_messages,
                                        _run_long_term_memory_extraction,
                                    )
                                )
                            extraction_labels.append("long_term")

                        _results = await asyncio.gather(
                            *extraction_tasks,
                            return_exceptions=True,
                        )
                        # The archive outcome is binary: if any Phase 2 step
                        # still fails after retries, no .done marker is
                        # published. Successful steps retain message IDs so the
                        # same archive can resume without repeating them.
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
                        await _run_archive_summary()

                    # A recovered Phase 2 run may have already completed the
                    # long-term step before a sibling step failed. Reuse its
                    # persisted diff instead of reporting an empty task result.
                    if completed_memory_steps.get("long_term") and self._viking_fs:
                        candidate_memory_diff_uri = f"{archive_uri}/memory_diff.json"
                        if await self._viking_fs.exists(
                            candidate_memory_diff_uri,
                            ctx=self.ctx,
                        ):
                            memory_diff_uri = candidate_memory_diff_uri
                            try:
                                raw_memory_diff = await self._viking_fs.read_file(
                                    candidate_memory_diff_uri,
                                    ctx=self.ctx,
                                )
                                memory_diff = json.loads(raw_memory_diff or "{}")
                                if isinstance(memory_diff, dict):
                                    skipped_memory_operations.extend(
                                        item
                                        for item in memory_diff.get("skipped_operations", [])
                                        if isinstance(item, dict)
                                    )
                            except Exception as exc:
                                logger.warning(
                                    "Failed to read skipped memory operations from %s: %s",
                                    candidate_memory_diff_uri,
                                    exc,
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
            _publish_telemetry_summary_best_effort(snapshot)
            await self._merge_and_save_commit_meta(
                archive_index=archive_index,
                memories_extracted=memories_extracted,
                telemetry_snapshot=snapshot,
                record_auto_commit_success=record_auto_commit_success,
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
                completed_memory_steps=self._archives.serialize_completed_memory_steps(
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
                "memory_extraction": {
                    "skipped": len(skipped_memory_operations),
                    "skipped_operations": skipped_memory_operations,
                },
                "usage_events_extracted": usage_events_extracted,
                "effective_memory_types": sorted(
                    _effective_memory_types(MemoryPolicy.from_dict(memory_policy))
                ),
                "agent_evolution_enabled": agent_evolution_enabled,
                "agent_memory_skip_reason": agent_memory_skip_reason,
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
            if user_config_error:
                result_payload["user_config_error"] = user_config_error

            await tracker.complete(
                task_id,
                result_payload,
                account_id=self.ctx.account_id,
                user_id=self.ctx.user.user_id,
            )
            logger.info(f"Session {self.session_id} memory extraction completed")
        except asyncio.CancelledError:
            telemetry.set_error("session.commit.phase2", "CANCELLED", "session commit cancelled")
            snapshot = telemetry.finish("cancelled")
            _publish_telemetry_summary_best_effort(snapshot)
            await self._write_failed_marker(
                archive_uri,
                stage="cancelled",
                error="session commit cancelled",
            )
            raise
        except Exception as e:
            telemetry.set_error("session.commit.phase2", type(e).__name__, str(e))
            snapshot = telemetry.finish("error")
            _publish_telemetry_summary_best_effort(snapshot)
            await self._write_failed_marker(
                archive_uri,
                stage="memory_extraction",
                error=str(e),
                completed_memory_steps=self._archives.serialize_completed_memory_steps(
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
        lease_ref: Optional[Any] = None,
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
            lease_ref=lease_ref,
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

        for archive in await self._archives.completed_refs():
            if archive["archive_id"] != archive_id:
                continue

            overview = await self._archives.read_overview(archive["archive_uri"])
            if not overview:
                break

            abstract = await self._archives.read_abstract(archive["archive_uri"], overview)
            return {
                "archive_id": archive_id,
                "abstract": abstract,
                "overview": overview,
                "messages": [
                    m.to_dict() for m in await self._archives.read_messages(archive["archive_uri"])
                ],
            }

        raise NotFoundError(archive_id, "session archive")

    # ============= Internal methods =============

    async def _collect_session_context_components(self) -> Dict[str, Any]:
        """Collect overview and messages by stopping at the newest terminal archive.

        Archive history grows without bound, so the current-format read path scans
        newest → oldest and stops at the first terminal marker (``.done`` or
        ``.failed.json``):

        - newest terminal is ``completed``: inject that archive's overview when
          readable, plus raw messages from the newer non-terminal archives;
        - newest terminal is ``failed``: no overview, and only the newer
          non-terminal archives contribute raw messages;
        - no terminal at all: no overview, every archive is still non-terminal
          so all of their raw messages are returned.

        Nothing at or older than that terminal is normally read, which is a
        deliberate deviation from the RFC #3330 recovery formula: an uncovered
        ``failed`` archive no longer replays its raw messages. Cumulative v2
        checkpoints are restored from the terminal archive alone; only a
        terminal legacy v1 delta checkpoint invokes an older-history compatibility
        scan. Public ``pre_archive_abstracts`` stay empty; abstracts are not read.
        """
        archive_refs = await self._archives.list_refs()
        newer_pending: List[Dict[str, Any]] = []
        terminal: Optional[Dict[str, Any]] = None
        terminal_state = ""

        for archive in archive_refs:  # newest → oldest
            state = await self._archives.terminal_state(archive["archive_uri"])
            if state == "pending":
                newer_pending.append(archive)
                continue
            terminal = archive
            terminal_state = state
            break

        latest_archive = None
        failed_archives = 0
        if terminal is not None and terminal_state == "completed":
            overview = (await self._archives.read_overview(terminal["archive_uri"])).strip()
            if overview:
                latest_archive = {
                    "archive_id": terminal["archive_id"],
                    "archive_uri": terminal["archive_uri"],
                    "overview": overview,
                    "overview_tokens": await self._archives.read_overview_tokens(
                        terminal["archive_uri"], overview
                    ),
                }
            elif await self._archives.is_context_reset_archive(terminal["archive_uri"]):
                terminal = None
            else:
                # A required overview that is missing or unreadable still keeps
                # the archive terminal here; the warning is emitted by the full
                # scan used for Phase 2 bookkeeping.
                logger.warning(
                    "Completed archive has no readable overview: %s",
                    terminal["archive_uri"],
                )
                failed_archives = 1
        elif terminal is not None:
            failed_archives = 1

        archive_messages: List[Message] = []
        # newer_pending was collected newest-first; restore chronological order.
        for archive in reversed(newer_pending):
            try:
                archive_messages.extend(await self._archives.read_messages(archive["archive_uri"]))
            except Exception as exc:
                if not _is_storage_not_found(exc):
                    raise
                logger.warning(
                    "Skipping pending archive %s because messages.jsonl is missing",
                    archive["archive_uri"],
                )

        merged_messages = self._archives.stable_deduplicate_messages(archive_messages + list(self._messages))
        merged_messages = await self._checkpoints.insert_terminal_checkpoints(
            merged_messages,
            terminal if terminal_state == "completed" else None,
        )

        return {
            "latest_archive": latest_archive,
            "pre_archive_abstracts": [],
            # Directory listing only; deriving an exact count would require the
            # per-archive marker scan this read path exists to avoid.
            "total_archives": len(archive_refs),
            "failed_archives": failed_archives,
            "messages": merged_messages,
        }

    async def _can_run_archive(self, archive_index: int) -> bool:
        """Resolve an orphaned direct predecessor before this Archive runs."""
        if archive_index <= 1 or not self._viking_fs:
            return True

        predecessor_uri = f"{self._session_uri}/history/archive_{archive_index - 1:03d}"
        if not await self._viking_fs.exists(predecessor_uri, ctx=self.ctx):
            return True
        if await self._archives.terminal_state(predecessor_uri) != "pending":
            return True

        phase1 = await self._read_phase1_meta(predecessor_uri)
        if not phase1:
            return False
        if phase1.get("status") != "ready":
            return not await self._ensure_phase1_ready(predecessor_uri)

        queue_message = phase1.get("queue_message")
        task_id = queue_message.get("task_id") if isinstance(queue_message, dict) else None
        if not task_id:
            return False

        from openviking.service.task_tracker import get_task_tracker

        tracker = get_task_tracker()
        if tracker.has_work(str(task_id)):
            return False

        error = "Session commit queue work is missing"
        await self._write_failed_marker(
            predecessor_uri,
            stage="queue_missing",
            error=error,
        )
        await tracker.fail(
            str(task_id),
            error,
            account_id=self.ctx.account_id,
            user_id=self.ctx.user.user_id,
        )
        logger.warning(
            "Skipped orphaned Session archive without QueueFS work: %s",
            predecessor_uri,
        )
        return True

    async def _prepare_phase2_archive_messages(
        self,
        archive_uri: str,
        current_messages: List[Message],
    ) -> tuple[List[Message], str, str, List[str], Dict[str, set[str]]]:
        """Prepare only the current archive, preserving its retry progress."""
        current_archive_id = archive_uri.rstrip("/").split("/")[-1]
        completed_memory_steps: Dict[str, set[str]] = {}
        current_meta = await self._archives.read_meta(archive_uri)
        self._archives.merge_completed_memory_steps(
            completed_memory_steps,
            current_meta.get("completed_memory_steps"),
        )
        return (
            current_messages,
            current_archive_id,
            current_archive_id,
            [],
            completed_memory_steps,
        )

    async def _merge_and_save_commit_meta(
        self,
        archive_index: int,
        memories_extracted: Dict[str, int],
        telemetry_snapshot: Any,
        *,
        record_auto_commit_success: bool = False,
    ) -> None:
        """Merge Phase 2 results without overwriting concurrent root updates."""
        session_path = self._viking_fs._uri_to_path(self._session_uri, ctx=self.ctx)
        lease = await self._viking_fs._async_agfs.pathlock_acquire_exact(
            session_path, timeout_secs=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS
        )
        try:
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
            if record_auto_commit_success:
                # Mirror the Phase 1 success stamp so the persisted meta reflects
                # a clean auto-commit even after Phase 2 reloads the latest meta.
                latest_meta.last_auto_commit_at = get_current_timestamp()
            self._meta = latest_meta
            await self._save_meta()
        finally:
            await self._viking_fs._async_agfs.pathlock_release(lease)

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
        # Split on "\n" only, not str.splitlines(): the latter also treats
        # U+2028 / U+2029 / NEL (\x85) / \r / \v / \f as line boundaries, which
        # would cut a JSONL record in half when those characters appear inside a
        # JSON string value (e.g. assistant tool_output) and break json.loads()
        # with "Unterminated string". Normalize CRLF first so a trailing "\r"
        # does not leak into the record. See issue #3984.
        for line_number, line in enumerate(content.replace("\r\n", "\n").split("\n"), start=1):
            if not line.strip():
                continue
            try:
                messages.append(Message.from_dict(json.loads(line)))
            except Exception as exc:
                raise ValueError(
                    f"Invalid live message JSONL at line {line_number}: {exc}"
                ) from exc
        return messages

    async def _generate_archive_summary_with_batching(
        self,
        batches: List[ExtractionMessageBatch],
        *,
        latest_archive_overview: str,
        limits: ExtractionBatchLimits,
    ) -> str:
        messages = [message for batch in batches for message in batch.messages]
        vlm = await self._get_vlm_config()
        if not (vlm and vlm.is_available()):
            return await self._generate_archive_summary_async(
                messages,
                latest_archive_overview=latest_archive_overview,
            )
        try:
            _load_render_prompt()
        except Exception:
            return await self._generate_archive_summary_async(
                messages,
                latest_archive_overview=latest_archive_overview,
            )

        logger.info(
            "Processing Phase 2 Working Memory extraction in %s planned batches "
            "using auto-commit limits tokens=%s messages=%s",
            len(batches),
            limits.max_message_tokens,
            limits.max_messages,
        )
        current_overview = latest_archive_overview

        for batch in batches:
            batch_messages = list(batch.messages)
            generated = await self._generate_archive_summary_async(
                batch_messages,
                latest_archive_overview=current_overview,
            )
            current_overview = (
                generated.overview
                if isinstance(generated, _ArchiveSummaryResult)
                else str(generated or "")
            )
        return current_overview

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
          decisions and merge them against the previous WM. Invalid response
          content may fall back to the creation prompt. Model-call failures
          propagate to the task owner; retries belong to the VLM provider.
        """
        wm.wm_debug(
            f"_generate_archive_summary_async called "
            f"messages={len(messages)} prior_wm={len(latest_archive_overview)}B"
        )
        checkpoint_requests = list(checkpoint_requests or [])
        if not messages:
            if checkpoint_requests:
                raise ValueError("Cannot generate checkpoints without archive messages")
            return ""

        formatted = wm.format_messages_for_wm(messages, checkpoint_requests)
        language_conversation = "\n".join(
            f"[{message.role}]: {line}"
            for message in messages
            for part in message.parts
            if isinstance(part, TextPart)
            for line in part.text.splitlines()
            if line.strip()
        )
        output_language = resolve_output_language_from_conversation(
            language_conversation, config=get_openviking_config()
        )
        checkpoint_instructions = wm.checkpoint_prompt_instructions(len(checkpoint_requests))

        vlm = await self._get_vlm_config()
        if not (vlm and vlm.is_available()):
            if checkpoint_requests:
                raise ValueError("A configured VLM is required to generate checkpoint summaries")
            turn_count = len([m for m in messages if is_user_query(m)])
            return (
                f"# Session Summary\n\n**Overview**: {turn_count} turns, {len(messages)} messages"
            )

        try:
            render_prompt = _load_render_prompt()
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
            wm.wm_debug(
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
                        "output_language": output_language,
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
                        checkpoint_summaries=wm.parse_required_checkpoint_summaries(
                            args,
                            len(checkpoint_requests),
                        ),
                    )
                return await vlm.get_completion_async(prompt)
            except Exception as e:
                wm.wm_debug(f"creation failed: {e}")
                logger.warning(f"WM creation failed: {e}")
                if checkpoint_requests:
                    raise
                turn_count = len([m for m in messages if is_user_query(m)])
                return (
                    f"# Session Summary\n\n"
                    f"**Overview**: {turn_count} turns, {len(messages)} messages"
                )

        # -------- Branch 2: has prior WM v2 -> tool_call incremental update --------
        wm.wm_debug(f"branch=UPDATE (prior WM={len(latest_archive_overview)}B)")
        reminders = wm.build_wm_section_reminders(latest_archive_overview)
        if reminders:
            wm.wm_debug(f"section_reminders injected ({len(reminders)}B)")
        update_prompt = render_prompt(
            "compression.ov_wm_v2_update",
            {
                "messages": formatted,
                "latest_archive_overview": latest_archive_overview,
                "wm_section_reminders": reminders,
                "checkpoint_instructions": checkpoint_instructions,
                "output_language": output_language,
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

        has_tc = bool(getattr(resp, "has_tool_calls", False) and getattr(resp, "tool_calls", None))
        _preview = (str(resp)[:200]).replace(chr(10), " ")
        _finish = getattr(resp, "finish_reason", "n/a")
        _usage = getattr(resp, "usage", {}) or {}
        wm.wm_debug(
            f"resp type={type(resp).__name__} has_tool_calls={has_tc} "
            f"finish_reason={_finish!r} usage={_usage} preview={_preview!r}"
        )

        if not has_tc:
            if checkpoint_requests:
                raise ValueError("Working Memory update returned no tool call for checkpoints")
            logger.warning("WM update: LLM returned no tool_call; falling back to creation prompt")
            return await self._fallback_generate_wm_creation(
                formatted, messages, latest_archive_overview, output_language
            )

        checkpoint_summaries: tuple[str, ...] = ()
        try:
            raw_args = resp.tool_calls[0].arguments
            wm.wm_debug(f"raw_args type={type(raw_args).__name__} preview={str(raw_args)[:400]!r}")
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
                wm.wm_debug(f"args has only 'raw' key; attempting recovery len={len(raw_str)}")
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
                            wm.wm_debug(
                                f"recovered by closing {opens} brace(s); patched_len={len(patched)}"
                            )
                    except Exception as e2:
                        wm.wm_debug(f"brace-close recovery failed: {e2}")
                if isinstance(recovered, dict):
                    args = recovered
                    wm.wm_debug(f"recovered args keys={list(args.keys())}")

            wm.wm_debug(f"args keys={list(args.keys())}")
            if checkpoint_requests:
                checkpoint_summaries = wm.parse_required_checkpoint_summaries(
                    args,
                    len(checkpoint_requests),
                )
            # Tolerant: if LLM returned {"Session Title": {...}, ...} without
            # the outer "sections" wrapper, treat the top-level as ops.
            if "sections" in args and isinstance(args["sections"], dict):
                ops = args["sections"]
            elif all(k in args for k in WM_SEVEN_SECTIONS):
                wm.wm_debug("args has section keys directly; accepting as ops")
                ops = args
            else:
                raise ValueError(f"tool_call arguments.sections missing; keys={list(args.keys())}")
            if not isinstance(ops, dict):
                raise ValueError("ops is not a dict")
        except Exception as e:
            if checkpoint_requests:
                raise
            wm.wm_debug(
                f"args parse failed: {type(e).__name__}: {e}; attempting regex recovery from raw"
            )
            # Regex salvage: when the LLM emits slightly-broken JSON (curly
            # quote, unescaped newline, truncated string), OV's VLM backend
            # wraps it as {"raw": "..."} and all structural parsing fails. We
            # still try to pull each section's op directly via regex before
            # falling back to the creation prompt. Missing sections default
            # to KEEP in merge_wm_sections so old content is preserved.
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
            salvaged = wm.wm_recover_ops_from_raw(raw_for_recovery)
            if salvaged:
                wm.wm_debug(
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
                return wm.merge_wm_sections(latest_archive_overview, salvaged)
            wm.wm_debug("regex recovery salvaged 0 sections; falling back to creation prompt")
            logger.warning(
                "WM update: tool_call arguments parse failed (%s); "
                "regex recovery found nothing; falling back to creation prompt",
                e,
            )
            return await self._fallback_generate_wm_creation(
                formatted, messages, latest_archive_overview, output_language
            )

        wm.wm_debug(
            f"ops keys={list(ops.keys())[:7]} "
            f"ops_summary={[(k, v.get('op') if isinstance(v, dict) else type(v).__name__) for k, v in ops.items()][:7]}"
        )
        overview = wm.merge_wm_sections(latest_archive_overview, ops)
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
        output_language: str = "en",
    ) -> str:
        """Regenerate WM when a successful update response cannot be parsed.

        Passes ``prior_overview`` so the creation prompt can incorporate
        accumulated context instead of generating from scratch.
        """
        wm.wm_debug(
            f"fallback creation prompt: prior_overview={len(prior_overview)}B "
            f"messages={len(messages)}"
        )
        from openviking.prompts import render_prompt

        prompt = render_prompt(
            "compression.ov_wm_v2",
            {
                "messages": formatted_messages,
                "latest_archive_overview": prior_overview,
                "checkpoint_instructions": "",
                "output_language": output_language,
            },
        )
        vlm = await self._get_vlm_config()
        return await vlm.get_completion_async(prompt)

    async def _write_to_agfs_async(
        self,
        messages: List[Message],
        lease_ref: Optional[Any] = None,
    ) -> None:
        """Write messages.jsonl to AGFS using an optional held PathLock lease."""
        if not self._viking_fs:
            return

        viking_fs = self._viking_fs
        turn_count = len([m for m in messages if is_user_query(m)])

        abstract = self._generate_abstract()
        overview = self._generate_overview(turn_count)

        lines = [m.to_jsonl() for m in messages]
        content = "\n".join(lines) + "\n" if lines else ""
        abstract_content = render_abstract_overview(
            ContextLevel.ABSTRACT,
            self._session_uri,
            abstract,
            {
                "generated_by": {
                    "component": "Session",
                    "trigger": "session_update",
                }
            },
        )
        overview_content = render_abstract_overview(
            ContextLevel.OVERVIEW,
            self._session_uri,
            overview,
            {
                "generated_by": {
                    "component": "Session",
                    "trigger": "session_update",
                }
            },
        )

        root_write_results = await asyncio.gather(
            viking_fs.write_file(
                uri=f"{self._session_uri}/messages.jsonl",
                content=content,
                ctx=self.ctx,
                lease_ref=lease_ref,
            ),
            viking_fs.write_file(
                uri=f"{self._session_uri}/.abstract.md",
                content=abstract_content,
                ctx=self.ctx,
                lease_ref=lease_ref,
            ),
            viking_fs.write_file(
                uri=f"{self._session_uri}/.overview.md",
                content=overview_content,
                ctx=self.ctx,
                lease_ref=lease_ref,
            ),
            return_exceptions=True,
        )
        for result in root_write_results:
            if isinstance(result, BaseException):
                raise result

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
    def stats(self) -> SessionStats:
        """Get session statistics."""
        return self._stats

    def __repr__(self) -> str:
        return f"Session(user={self.user}, id={self.session_id})"
