# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session Compressor V3.

V3 keeps the V2 extraction interface while changing user-memory commits to a
patch-merge flow without directory-level memory locks. Reusable task scenarios
(called Cases) are a normal user-memory type (``cases``), emitted by the same
ExtractLoop as profile/preferences/events. Multiple concrete tasks may share a
Case. Session commit persists factual Trajectories; a separate Agent Evolution
API later batches them into Experience updates.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, List, Optional
from uuid import uuid4

from openviking.concurrency import bounded_map
from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.archive_store import is_storage_not_found
from openviking.session.memory import ExtractLoop, MemoryUpdater, StreamingMemoryUpdaterConfig
from openviking.session.memory.constants import (
    AGENT_EVOLUTION_MEMORY_TYPES,
    CASE_MEMORY_TYPE,
    DEFAULT_EXTRACT_MAX_ITERATIONS,
    EVENT_MEMORY_TYPE,
    EXECUTION_MEMORY_TYPES,
    EXPERIENCE_MEMORY_TYPE,
    TRAJECTORY_MEMORY_TYPE,
)
from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.experience.dag import MAX_EVIDENCE_SUMMARY_CHARS
from openviking.session.memory.experience.rubric import parse_case_rubric
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import get_default_registry
from openviking.session.memory.memory_updater import EventRangePaddingError
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.streaming_memory_updater import (
    MemoryUpdateRequest,
    get_streaming_memory_updater,
    make_streaming_memory_updater_key,
)
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.uri import generate_uri
from openviking.session.skill.session_skill_context_provider import (
    SESSION_SKILL_MEMORY_TYPE,
    load_skill_extract_registry,
)
from openviking.session.train import (
    Case,
    CriterionResult,
    ExperienceGradientContext,
    ExperienceGradientEstimator,
    ExperienceSet,
    MemoryFilePolicyUpdater,
    PatchMergePolicyOptimizer,
    PatchMergePolicyOptimizerContext,
    PipelineContext,
    PolicyApplyResult,
    PolicyPlanItem,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
    SessionAnalyzerContext,
    SessionRolloutAnalyzer,
    SkillPolicyUpdater,
    SkillSetLoader,
    StreamingPolicyTrainer,
    StreamingPolicyTrainerConfig,
    Trajectory,
    get_streaming_policy_trainer,
)
from openviking.session.train.components.trajectory_lifecycle import (
    archive_trajectory,
    trajectory_archive_uri,
)
from openviking.session.train.components.trajectory_observation import (
    TRAJECTORY_FORMAT,
    ObservationGradient,
    list_trajectory_uris,
    trajectory_source_session_uri,
)
from openviking.session.train.components.trajectory_optimizer import TrajectoryExperienceOptimizer
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import get_current_telemetry, tracer
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import ConflictError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

if TYPE_CHECKING:
    from openviking.config.vlm import VLMHandle, VLMResolver

logger = get_logger(__name__)

_CASES_MEMORY_TYPE = CASE_MEMORY_TYPE
_EXPERIENCES_MEMORY_TYPE = EXPERIENCE_MEMORY_TYPE
_TRAJECTORIES_MEMORY_TYPE = TRAJECTORY_MEMORY_TYPE
_EVENTS_MEMORY_TYPE = EVENT_MEMORY_TYPE
_AGENT_MEMORY_TYPES = EXECUTION_MEMORY_TYPES
_TRAINING_CASE_SPEC_PROTOCOL = "openviking.batch_train.case_spec.v1"
_TRAINING_CASE_SPEC_HEADER = "# OpenViking Batch Training CaseSpec v1"
_TRAINING_EVALUATION_HEADER = "# OpenViking OutcomeEvaluation"
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_EXPERIENCE_SESSION_MAP_PREFIX = "OpenViking-Experience-Session-Map: "
_EXPERIENCE_UPDATE_FIELD = "experience_update"
_EXPERIENCE_UPDATE_TERMINAL_STATES = {"applied", "no_op"}


class ExperienceTrainingError(RuntimeError):
    """A training operation failed; the caller selects strict or lenient handling."""


def _trajectory_update_state(memory: MemoryFile) -> dict[str, Any]:
    value = memory.extra_fields.get(_EXPERIENCE_UPDATE_FIELD)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = None
    state = dict(value) if isinstance(value, dict) else {}
    state.setdefault("state", "pending")
    state.setdefault("attempts", 0)
    state.pop("experience_uri", None)
    state.setdefault("batch_id", None)
    state.setdefault("lease_until", None)
    state.setdefault("processed_at", None)
    state.setdefault("error", None)
    return state


def _trajectory_lease_expired(state: dict[str, Any]) -> bool:
    raw = state.get("lease_until")
    if not raw:
        return True
    try:
        lease_until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    return lease_until <= datetime.now(timezone.utc)


def _case_uri_for_name(registry: Any, *, case_name: str, ctx: RequestContext) -> str:
    schema = registry.get(_CASES_MEMORY_TYPE)
    if schema is None or not schema.enabled:
        raise ValueError("Case schema is required for Experience learning")
    return generate_uri(schema, {"case_name": case_name}, user_space=ctx.user.user_id)


def _experience_uri_for_case(registry: Any, *, case_name: str, ctx: RequestContext) -> str:
    schema = registry.get(_EXPERIENCES_MEMORY_TYPE)
    if schema is None or not schema.enabled:
        raise ValueError("Experience schema is required for Experience learning")
    return generate_uri(schema, {"experience_name": case_name}, user_space=ctx.user.user_id)


def _trajectory_evaluation(value: Any) -> RubricEvaluation | None:
    if not isinstance(value, dict):
        return None
    return RubricEvaluation(
        passed=bool(value.get("passed", False)),
        score=float(value.get("score") or 0.0),
        criterion_results=[],
        feedback=[],
    )


def _apply_event_search_tags(
    operations: Optional["ResolvedOperations"],
    event_search_tags: Optional[List[str]],
) -> None:
    """Attach custom scalar tags to event-memory operations in place.

    Only ``events``-type upsert operations are tagged so the scalars ride the
    same first write into the vector index. Empty/None tags are a no-op, and a
    missing operations object is tolerated (the orchestrator may return None).
    """
    if not event_search_tags or operations is None:
        return
    tags = list(event_search_tags)
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) == _EVENTS_MEMORY_TYPE:
            op.search_tags = list(tags)


def _initialize_extraction_telemetry() -> None:
    telemetry = get_current_telemetry()
    for name in (
        "memory.extract.candidates.total",
        "memory.extract.candidates.standard",
        "memory.extract.candidates.tool_skill",
        "memory.extract.created",
        "memory.extract.merged",
        "memory.extract.deleted",
        "memory.extract.skipped",
        "memory.extract.failed",
    ):
        telemetry.set(name, 0)


def _memory_type_by_uri(operations: ResolvedOperations) -> dict[str, str]:
    """Map applied memory URIs to their stable extraction schema names."""
    types_by_uri: dict[str, str] = {}
    for operation in getattr(operations, "upsert_operations", []) or []:
        memory_type = str(getattr(operation, "memory_type", "") or "unknown")
        for uri in getattr(operation, "uris", []) or []:
            types_by_uri[str(uri)] = memory_type
    for file_content in getattr(operations, "delete_file_contents", []) or []:
        uri = str(getattr(file_content, "uri", "") or "")
        if uri:
            types_by_uri[uri] = str(getattr(file_content, "memory_type", "") or "unknown")
    return types_by_uri


def _report_extraction_telemetry(result: Any, operations: ResolvedOperations) -> None:
    telemetry = get_current_telemetry()
    telemetry.set(
        "memory.extract.candidates.total",
        len(result.written_uris) + len(result.edited_uris),
    )
    telemetry.set("memory.extract.created", len(result.written_uris))
    telemetry.set("memory.extract.merged", len(result.edited_uris))
    telemetry.set("memory.extract.deleted", len(result.deleted_uris))
    telemetry.set("memory.extract.skipped", len(result.skipped_operations))
    telemetry.set("memory.extract.failed", len(result.errors))

    types_by_uri = _memory_type_by_uri(operations)
    actions_by_type: dict[str, dict[str, int]] = {}

    def memory_type_for_telemetry(uri: Any) -> str:
        if memory_type := types_by_uri.get(str(uri)):
            return str(memory_type)
        try:
            return str(MemoryUpdater.memory_type_from_uri(str(uri)) or "unknown")
        except ValueError:
            return "unknown"

    def add(memory_type: Any, action: str) -> None:
        normalized_type = str(memory_type or "unknown")
        type_actions = actions_by_type.setdefault(normalized_type, {})
        type_actions[action] = type_actions.get(action, 0) + 1

    for uri in result.written_uris:
        add(memory_type_for_telemetry(uri), "created")
    for uri in result.edited_uris:
        add(memory_type_for_telemetry(uri), "merged")
    for uri in result.deleted_uris:
        add(memory_type_for_telemetry(uri), "deleted")
    for operation in result.skipped_operations:
        add(getattr(operation, "memory_type", None), "skipped")
    for uri, _error in result.errors:
        add(memory_type_for_telemetry(uri), "failed")

    for memory_type, actions in actions_by_type.items():
        for action, value in actions.items():
            telemetry.set(f"memory.extract.by_type.{memory_type}.{action}", value)


async def _commit_experience_snapshot(
    viking_fs: Any,
    *,
    ctx: RequestContext,
    experience_uris: list[str],
    archive_uri: str = "",
    experience_session_map: Optional[dict[str, list[str]]] = None,
) -> None:
    commit = getattr(viking_fs, "commit", None)
    if not callable(commit):
        return
    paths = [
        uri
        for uri in dict.fromkeys(str(uri or "") for uri in experience_uris)
        if "/memories/experiences/" in uri and uri.endswith(".md")
    ]
    if not paths:
        return
    archive_ref = archive_uri.rstrip("/") if archive_uri else "unknown"
    changed_experience_uris = set(paths)
    session_map = {
        experience_uri: list(dict.fromkeys(session_uris))
        for experience_uri, session_uris in (experience_session_map or {}).items()
        if experience_uri in changed_experience_uris
    }
    message = (
        f"Update experience memories from session commit {archive_ref}\n"
        f"{_EXPERIENCE_SESSION_MAP_PREFIX}"
        f"{json.dumps(session_map, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
    )
    try:
        await commit(
            message=message,
            paths=paths,
            ctx=ctx,
        )
    except Exception as exc:
        logger.warning("Failed to commit experience snapshot for %s: %s", paths, exc, exc_info=True)


class SessionCompressorV3:
    """Session compressor with lock-free patch-merge user memory extraction."""

    rollout_analyzer: SessionRolloutAnalyzer | Any
    streaming_trainer_config: StreamingPolicyTrainerConfig = field(
        default_factory=StreamingPolicyTrainerConfig
    )
    streaming_memory_updater_config: StreamingMemoryUpdaterConfig = field(
        default_factory=StreamingMemoryUpdaterConfig
    )

    def __init__(
        self,
        vikingdb,
        skill_processor: Optional[Any] = None,
        *,
        rollout_analyzer: SessionRolloutAnalyzer | Any | None = None,
        streaming_trainer_config: StreamingPolicyTrainerConfig | None = None,
        streaming_memory_updater_config: StreamingMemoryUpdaterConfig | None = None,
        vlm_resolver: VLMResolver | None = None,
    ):
        self.vikingdb = vikingdb
        self.skill_processor = skill_processor
        self.vlm_resolver = vlm_resolver
        self.rollout_analyzer = rollout_analyzer or SessionRolloutAnalyzer(
            viking_fs=get_viking_fs(),
            vikingdb=vikingdb,
            vlm_resolver=vlm_resolver,
        )
        self.streaming_trainer_config = streaming_trainer_config or StreamingPolicyTrainerConfig()
        self.streaming_memory_updater_config = (
            streaming_memory_updater_config or StreamingMemoryUpdaterConfig()
        )
        self._experience_learning_lock = asyncio.Lock()

    def _get_or_create_react(
        self,
        ctx: Optional[RequestContext] = None,
        messages: Optional[List] = None,
        latest_archive_overview: str = "",
        extraction_context: str = "",
        isolation_handler: Optional[MemoryIsolationHandler] = None,
        transaction_handle=None,
        context_provider: Optional[SessionExtractContextProvider] = None,
        vlm_config: VLMHandle | None = None,
        *,
        max_iterations: int = DEFAULT_EXTRACT_MAX_ITERATIONS,
        thinking: bool = False,
    ) -> ExtractLoop:
        if vlm_config is None:
            raise RuntimeError("SessionCompressorV3 requires an explicitly resolved VLM config")
        vlm = vlm_config
        viking_fs = get_viking_fs()
        if context_provider is None:
            context_provider = SessionExtractContextProvider(
                messages=messages,
                latest_archive_overview=latest_archive_overview,
                extraction_context=extraction_context,
                isolation_handler=isolation_handler,
                ctx=ctx,
                viking_fs=viking_fs,
                transaction_handle=transaction_handle,
                vlm_config=vlm_config,
            )
        return ExtractLoop(
            vlm=vlm,
            max_iterations=max_iterations,
            thinking=thinking,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )

    def _get_or_create_updater(self, registry, transaction_handle=None) -> MemoryUpdater:
        return MemoryUpdater(
            registry=registry,
            vikingdb=self.vikingdb,
            transaction_handle=transaction_handle,
        )

    async def _build_memory_diff(
        self,
        result: Any,
        operations: ResolvedOperations,
        viking_fs: Any,
        ctx: RequestContext,
        archive_uri: str = "",
    ) -> dict[str, Any]:
        adds: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        deletes: list[dict[str, Any]] = []

        upsert_by_uri = {}
        for op in operations.upsert_operations:
            for uri in op.uris:
                upsert_by_uri[uri] = op
        delete_by_uri = {dc.uri: dc for dc in operations.delete_file_contents}

        for uri in result.written_uris:
            op = upsert_by_uri.get(uri)
            memory_type = (
                op.memory_type if op else MemoryUpdater.memory_type_from_uri(uri) or "unknown"
            )
            old_file = op.old_memory_file_content if op else None
            if old_file:
                updates.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "before": old_file.content,
                        "after": "",
                    }
                )
            else:
                adds.append({"uri": uri, "memory_type": memory_type, "after": ""})

        for uri in result.edited_uris:
            op = upsert_by_uri.get(uri)
            memory_type = (
                op.memory_type if op else MemoryUpdater.memory_type_from_uri(uri) or "unknown"
            )
            old_file = op.old_memory_file_content if op and op.old_memory_file_content else None
            updates.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "before": old_file.content if old_file else "",
                    "after": "",
                }
            )

        for uri in result.deleted_uris:
            deleted = delete_by_uri.get(uri)
            deletes.append(
                {
                    "uri": uri,
                    "memory_type": (deleted.memory_type if deleted else None) or "unknown",
                    "deleted_content": deleted.content if deleted else "",
                }
            )

        # Read new content for adds and updates.
        # Some upsert operations can be reported as successful even when the
        # final file body is identical to the pre-existing content (for
        # example, a no-op merge/patch or a write that only re-serializes the
        # same memory). memory_diff.json should only include effective content
        # changes, so filter no-op updates after the final content is known.
        for item in adds:
            try:
                content = await viking_fs.read_file(uri=item["uri"], ctx=ctx)
                item["after"] = MemoryFileUtils.read(content).content
            except Exception:
                pass

        effective_updates: list[dict[str, Any]] = []
        for item in updates:
            op = upsert_by_uri.get(item["uri"])
            old_file = op.old_memory_file_content if op else None
            new_file: Optional[MemoryFile] = None
            try:
                content = await viking_fs.read_file(uri=item["uri"], ctx=ctx)
                new_file = MemoryFileUtils.read(content, uri=item["uri"])
                item["after"] = new_file.content
            except Exception:
                pass
            if old_file is not None and _same_memory_file(old_file, new_file):
                logger.info(
                    "Skipping unchanged memory file in memory_diff.json: %s",
                    item.get("uri"),
                )
                continue
            effective_updates.append(item)
        updates = effective_updates

        return _make_memory_diff(
            archive_uri=archive_uri,
            adds=adds,
            updates=updates,
            deletes=deletes,
            skipped_operations=_serialize_skipped_operations(
                getattr(result, "skipped_operations", [])
            ),
        )

    @tracer(ignore_result=True)
    @tracer.errors_to_trace()
    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional[Any] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: Optional[str] = None,
        allowed_memory_types: Optional[set[str]] = None,
        agent_evolution_enabled: bool = True,
        allow_self_memory: bool = True,
        allowed_peer_ids: Optional[set[str]] = None,
        event_search_tags: Optional[List[str]] = None,
        peer_memory_enabled: bool = True,
        extraction_context: str = "",
        *,
        extract_max_iterations: int = DEFAULT_EXTRACT_MAX_ITERATIONS,
        extract_thinking: bool = False,
    ):
        memory_config = get_openviking_config().memory
        minimal_mode = getattr(memory_config, "minimal_mode", False)
        if minimal_mode:
            allowed_memory_types = {"events"} & (
                allowed_memory_types if allowed_memory_types is not None else {"events"}
            )
            agent_evolution_enabled = False
        if not agent_evolution_enabled:
            effective_types = (
                set(get_default_registry().list_names(include_disabled=False))
                if allowed_memory_types is None
                else set(allowed_memory_types)
            )
            allowed_memory_types = effective_types - AGENT_EVOLUTION_MEMORY_TYPES

        message_list = list(messages)
        task = _training_case_from_first_message(message_list)
        cases_allowed = allowed_memory_types is None or _CASES_MEMORY_TYPE in allowed_memory_types
        try:
            result = await self._extract_user_memories(
                messages=_training_conversation_messages(message_list),
                case_rubric_evidence=_case_rubric_model_evidence(
                    task, _training_evaluation_from_messages(message_list)
                )
                if cases_allowed
                else [],
                user=user,
                session_id=session_id,
                ctx=ctx,
                strict_extract_errors=strict_extract_errors,
                latest_archive_overview=latest_archive_overview,
                archive_uri=archive_uri,
                allowed_memory_types=allowed_memory_types,
                allow_self_memory=allow_self_memory,
                peer_memory_enabled=peer_memory_enabled,
                allowed_peer_ids=allowed_peer_ids,
                event_search_tags=event_search_tags,
                extraction_context=extraction_context,
                extract_max_iterations=extract_max_iterations,
                extract_thinking=extract_thinking,
            )
            agent_memory_types = _allowed_agent_memory_types(allowed_memory_types)
            session_skills_enabled = not minimal_mode and self._session_skill_extraction_enabled()
            if (
                agent_evolution_enabled
                and cases_allowed
                and _TRAJECTORIES_MEMORY_TYPE in agent_memory_types
            ):
                train_result = await self.train_from_extracted_cases(
                    cases=result.cases,
                    task=task,
                    messages=message_list,
                    ctx=ctx,
                    case_uri_by_name=getattr(result, "case_uri_by_name", {}),
                    session_id=session_id,
                    archive_uri=archive_uri or "",
                    strict_extract_errors=strict_extract_errors,
                    collect_memory_diff=True,
                    allowed_memory_types=agent_memory_types,
                )
            elif not agent_evolution_enabled and allow_self_memory and session_skills_enabled:
                train_result = await self.extract_session_skills(
                    messages=message_list,
                    ctx=ctx,
                    archive_uri=archive_uri or "",
                    strict_extract_errors=strict_extract_errors,
                )
            else:
                train_result = {
                    "case_count": len(result.cases),
                    "submitted": 0,
                    "reason": (
                        "agent_evolution_disabled"
                        if not agent_evolution_enabled
                        else "memory_types_filtered"
                    ),
                }
            await self._write_final_memory_diff(
                archive_uri=archive_uri or "",
                ctx=ctx,
                memory_diffs=[
                    getattr(result, "memory_diff", None),
                    train_result.get("memory_diff"),
                ],
            )
            return _v3_extraction_response(
                contexts=result.contexts,
                train_result=train_result,
                archive_uri=archive_uri or "",
            )
        except Exception as exc:
            if (
                minimal_mode
                or strict_extract_errors
                or isinstance(exc, (ConflictError, EventRangePaddingError))
            ):
                raise
            tracer.error(
                "V3 memory extraction failed; returning empty result", e=exc, console=False
            )
            return {"contexts": [], "session_skills": []}

    async def _extract_user_memories(
        self,
        messages: List[Message],
        user: Optional[Any] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: Optional[str] = None,
        allowed_memory_types: Optional[set[str]] = None,
        allow_self_memory: bool = True,
        peer_memory_enabled: bool = True,
        allowed_peer_ids: Optional[set[str]] = None,
        event_search_tags: Optional[List[str]] = None,
        extraction_context: str = "",
        case_rubric_evidence: list[str] | None = None,
        *,
        extract_max_iterations: int = DEFAULT_EXTRACT_MAX_ITERATIONS,
        extract_thinking: bool = False,
    ) -> "_V3ExtractionResult":
        del user
        if not messages:
            return _V3ExtractionResult()
        if not ctx:
            logger.warning("No RequestContext provided, skipping v3 memory extraction")
            return _V3ExtractionResult()

        _initialize_extraction_telemetry()

        try:
            viking_fs = get_viking_fs()
        except Exception:
            logger.warning("VikingFS unavailable, skipping v3 memory extraction", exc_info=True)
            return _V3ExtractionResult()

        from openviking.session.memory.account_templates import resolve_account_memory_registry

        registry = await resolve_account_memory_registry(
            viking_fs, ctx.account_id, get_default_registry()
        )
        updater_registry = registry  # Keep mode-local templates out of the shared updater.
        memory_config = get_openviking_config().memory
        minimal_mode = getattr(memory_config, "minimal_mode", False)
        tracer.set("memory.extraction.backend", "llm_event_ranges" if minimal_mode else "llm")
        provider_type = SessionExtractContextProvider
        provider_options = {}
        if minimal_mode:
            from openviking.session.memory.minimal_mode_context_provider import (
                MinimalModeContextProvider,
                make_minimal_mode_registry,
            )

            allowed_memory_types = {"events"} & (
                allowed_memory_types if allowed_memory_types is not None else {"events"}
            )
            schema = registry.get("events")
            if schema is None or not schema.enabled or not allowed_memory_types:
                return _V3ExtractionResult()
            registry = make_minimal_mode_registry(registry)
            provider_type = MinimalModeContextProvider
            provider_options["source_uri"] = archive_uri or session_id or ""
        if allow_self_memory:
            await registry.initialize_memory_files(
                ctx,
                allowed_memory_types=allowed_memory_types,
            )

        if self.vlm_resolver is None:
            raise RuntimeError("SessionCompressorV3 requires a VLM resolver for account-owned work")
        vlm_config = await self.vlm_resolver.get_vlm(ctx.account_id)
        context_provider = provider_type(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            extraction_context=extraction_context,
            case_rubric_evidence=case_rubric_evidence,
            isolation_handler=None,
            ctx=ctx,
            viking_fs=viking_fs,
            transaction_handle=None,
            memory_registry=registry,
            vlm_config=vlm_config,
            **provider_options,
        )
        await context_provider.prepare_extraction_messages()
        extract_context = context_provider.get_extract_context()
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types=allowed_memory_types,
            allow_self=allow_self_memory,
            allowed_peer_ids=allowed_peer_ids,
            peer_memory_enabled=peer_memory_enabled,
        )
        isolation_handler.prepare_messages()
        context_provider._isolation_handler = isolation_handler

        orchestrator = self._get_or_create_react(
            ctx=ctx,
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            extraction_context=extraction_context,
            isolation_handler=isolation_handler,
            transaction_handle=None,
            context_provider=context_provider,
            vlm_config=vlm_config,
            max_iterations=extract_max_iterations,
            thinking=extract_thinking,
        )
        operations, _tools_used = await orchestrator.run()
        # Keep template ranges aligned with preprocessing, including vision descriptions.
        messages = context_provider.messages
        if operations is None:
            tracer.info("[v3_patch_merge] No memory operations generated")
            return _V3ExtractionResult()

        # Attach caller-provided custom scalar tags to event memories so they
        # ride the same first write into the vector index (人填标量).
        _apply_event_search_tags(operations, event_search_tags)

        extraction_id = uuid4().hex
        extracted_at = datetime.now(timezone.utc).isoformat()

        updater = await get_streaming_memory_updater(
            key=make_streaming_memory_updater_key(request_context=ctx),
            registry=updater_registry,
            vikingdb=self.vikingdb,
            config=self.streaming_memory_updater_config,
            vlm_resolver=self.vlm_resolver,
        )
        update_result = await updater.submit(
            MemoryUpdateRequest(
                operations=operations,
                messages=list(messages),
                ctx=ctx,
                strict_extract_errors=strict_extract_errors,
                memory_registry=registry,
                isolation_options={
                    "allowed_memory_types": allowed_memory_types,
                    "allow_self": allow_self_memory,
                    "allowed_peer_ids": allowed_peer_ids,
                    "peer_memory_enabled": peer_memory_enabled,
                },
                metadata={
                    "source_extraction_id": extraction_id,
                    "session_id": session_id,
                    "archive_uri": archive_uri,
                    "trace_id": tracer.get_trace_id(),
                    "extracted_at": extracted_at,
                },
            )
        )

        result = update_result.apply_result
        patch_operations = update_result.operations
        _report_extraction_telemetry(result, patch_operations)

        memory_diff = None
        if archive_uri and viking_fs and result is not None:
            memory_diff = await self._build_memory_diff(
                result=result,
                operations=patch_operations,
                viking_fs=viking_fs,
                ctx=ctx,
                archive_uri=archive_uri,
            )

        contexts = _contexts_from_update_result(result)
        canonical_cases = await _canonical_cases_from_update_result(
            operations=patch_operations,
            requested_operations=operations,
            result=result,
            viking_fs=viking_fs,
            ctx=ctx,
        )
        return _V3ExtractionResult(
            contexts=contexts,
            cases=canonical_cases,
            memory_diff=memory_diff,
            case_uri_by_name=_case_uri_by_name(canonical_cases, patch_operations, result),
            skipped_operations=_serialize_skipped_operations(
                getattr(result, "skipped_operations", [])
            ),
        )

    def _session_skill_extraction_enabled(self) -> bool:
        config = get_openviking_config()
        return bool(
            config.memory.session_skill_extraction_enabled and self.skill_processor is not None
        )

    async def extract_session_skills(
        self,
        *,
        messages: list[Message],
        ctx: Optional[RequestContext],
        archive_uri: str = "",
        strict_extract_errors: bool = False,
    ) -> dict[str, Any]:
        """Extract reusable skills without producing Agent Evolution memories."""
        if not messages or ctx is None:
            return {"case_count": 0, "submitted": 0, "reason": "missing_messages_or_ctx"}
        if not self._session_skill_extraction_enabled():
            return {"case_count": 0, "submitted": 0, "reason": "session_skills_disabled"}

        extract = getattr(self.rollout_analyzer, "extract_session_skills", None)
        if not callable(extract):
            return {
                "case_count": 0,
                "submitted": 0,
                "reason": "session_skill_extractor_unavailable",
            }

        try:
            result = await extract(
                messages=list(messages),
                ctx=ctx,
                strict_extract_errors=strict_extract_errors,
                source_archive_uri=archive_uri,
            )
            skill_gradients = [
                gradient
                for gradient in list((result or {}).get("skill_gradients", []))
                if _gradient_memory_type(gradient) == "skills"
            ]
            if not skill_gradients:
                return {
                    "case_count": 0,
                    "submitted": 0,
                    "skill_submitted": 0,
                    "skill_uris": [],
                }

            viking_fs = get_viking_fs()
            skill_trainer = await self._get_session_skill_trainer(
                viking_fs=viking_fs,
                ctx=ctx,
                messages=messages,
                strict_extract_errors=strict_extract_errors,
                archive_uri=archive_uri,
            )
            training_result = await skill_trainer.submit_gradients(skill_gradients)
            apply_result = getattr(training_result, "apply_result", None)
            skill_uris = [
                str(uri) for uri in list(getattr(apply_result, "written_uris", []) or []) if uri
            ]
            return {
                "case_count": 0,
                "submitted": 0,
                "skill_submitted": 1,
                "skill_uris": skill_uris,
            }
        except Exception as exc:
            logger.warning("Session skill extraction failed: %s", exc, exc_info=True)
            if strict_extract_errors:
                raise
            return {"case_count": 0, "submitted": 0, "error": str(exc)}

    async def _get_session_skill_trainer(
        self,
        *,
        viking_fs: Any,
        ctx: RequestContext,
        messages: list[Message],
        strict_extract_errors: bool,
        archive_uri: str,
    ) -> Any:
        skill_root_uri = _skill_root_uri(ctx)
        skill_policy_set = await SkillSetLoader(viking_fs=viking_fs).load(
            skill_root_uri,
            ctx=ctx,
        )
        analysis_context = SessionAnalyzerContext(
            request_context=ctx,
            strict_extract_errors=strict_extract_errors,
            include_session_skills=True,
            source_archive_uri=archive_uri,
        )
        return await get_streaming_policy_trainer(
            key=_skill_trainer_key(ctx),
            policy_set=skill_policy_set,
            rollout_analyzer=self.rollout_analyzer,
            gradient_estimator=_NoopGradientEstimator(),
            policy_optimizer=PatchMergePolicyOptimizer(
                viking_fs=viking_fs,
                memory_type=SESSION_SKILL_MEMORY_TYPE,
                memory_registry=load_skill_extract_registry(),
                vlm_resolver=self.vlm_resolver,
            ),
            policy_updater=SkillPolicyUpdater(
                skill_processor=self.skill_processor,
                viking_fs=viking_fs,
                vikingdb=self.vikingdb,
                memory_type="skills",
            ),
            context=PipelineContext(
                analysis_context=analysis_context,
                gradient_context=ExperienceGradientContext(
                    request_context=ctx,
                    messages=list(messages),
                    strict_extract_errors=strict_extract_errors,
                ),
                optimization_context=PatchMergePolicyOptimizerContext(
                    request_context=ctx,
                ),
                apply_context=ctx,
            ),
            config=self.streaming_trainer_config,
        )

    @tracer("train.compressor_v3.train_from_extracted_cases", ignore_result=True, ignore_args=True)
    async def train_from_extracted_cases(
        self,
        *,
        cases: list[Case],
        task: Case | None = None,
        messages: list[Message],
        ctx: Optional[RequestContext],
        case_uri_by_name: dict[str, str] | None = None,
        session_id: Optional[str] = None,
        archive_uri: str = "",
        strict_extract_errors: bool = False,
        collect_memory_diff: bool = False,
        allowed_memory_types: Optional[set[str]] = None,
    ) -> dict[str, Any]:
        if not messages or ctx is None:
            return {"case_count": 0, "submitted": 0, "reason": "missing_messages_or_ctx"}
        if not cases:
            tracer.info("No commit training case memories extracted; skipping streaming train")
            return {"case_count": 0, "submitted": 0}

        agent_memory_types = _allowed_agent_memory_types(allowed_memory_types)
        if _TRAJECTORIES_MEMORY_TYPE not in agent_memory_types:
            return {
                "case_count": len(cases),
                "submitted": 0,
                "reason": "memory_types_filtered",
            }

        skill_enabled = self._session_skill_extraction_enabled()
        rollout_evaluation, experience_execution = _training_outcome_from_messages(messages)
        conversation_messages = _training_conversation_messages(messages)

        try:
            viking_fs = get_viking_fs()
            from openviking.session.memory.account_templates import resolve_account_memory_registry

            registry = await resolve_account_memory_registry(
                viking_fs, ctx.account_id, get_default_registry()
            )

            # Phase 1 persists one immutable observation per Case/Session. Experience
            # synthesis is an explicit Phase 2 operation after the desired Sessions arrive.
            analysis_context = SessionAnalyzerContext(
                request_context=ctx,
                strict_extract_errors=strict_extract_errors,
                include_session_skills=False,
                source_archive_uri=archive_uri or "",
                memory_registry=registry,
            )
            # Skills remain an independent Session extraction, once per archive.
            skill_result = (
                await self.extract_session_skills(
                    messages=conversation_messages,
                    ctx=ctx,
                    archive_uri=archive_uri,
                    strict_extract_errors=strict_extract_errors,
                )
                if skill_enabled
                else {}
            )
            submitted = 0
            skill_submitted = int(skill_result.get("skill_submitted", 0))
            skill_uris = list(skill_result.get("skill_uris", []))
            trajectory_uris: list[str] = []
            memory_diffs: list[dict[str, Any]] = []
            policy_snapshot_id = _commit_policy_snapshot_id(
                session_id=session_id,
                archive_uri=archive_uri,
            )

            case_uri_map = dict(case_uri_by_name or {})
            rollouts = []
            for case in cases:
                case_uri = _case_uri_for_case(case, case_uri_map)
                rollout = Rollout(
                    case=case,
                    messages=list(conversation_messages),
                    policy_snapshot_id=policy_snapshot_id,
                    evaluation=rollout_evaluation,
                    metadata={
                        "source_session_uri": archive_uri,
                        "case_uri": case_uri,
                        "task_spec": asdict(task) if task is not None else None,
                        "experience_execution": experience_execution,
                    },
                )
                rollouts.append(rollout)
            # One joint extraction sees all Cases and the shared conversation, then
            # persists exactly one new Trajectory per Case. Learning remains deferred.
            analyses = await self.rollout_analyzer.analyze_many(rollouts, analysis_context)
            for analysis in analyses:
                trajectory = analysis.metadata.get("trajectory_memory")
                if trajectory is not None:
                    trajectory_uris.append(trajectory.uri)
                if collect_memory_diff and trajectory is not None:
                    memory_diffs.append(
                        _make_memory_diff(
                            archive_uri=archive_uri,
                            adds=[
                                {
                                    "uri": trajectory.uri,
                                    "memory_type": "trajectories",
                                    "after": trajectory.content,
                                }
                            ],
                            updates=[],
                            deletes=[],
                            skipped_operations=[],
                        )
                    )
                submitted += len(analysis.trajectories)

            tracer.info(
                "Persisted commit Case Trajectories for deferred Experience learning: "
                f"case_count={len(cases)} submitted={submitted} "
                f"skill_submitted={skill_submitted}",
                console=self.streaming_trainer_config.trace_console,
            )
            response: dict[str, Any] = {
                "case_count": len(cases),
                "submitted": submitted,
                "trajectory_uris": list(dict.fromkeys(trajectory_uris)),
                "skill_submitted": skill_submitted,
                "skill_uris": skill_uris,
                "persisted_experience_count": 0,
                "experience_learning_deferred": True,
            }
            if collect_memory_diff:
                response["memory_diff"] = _merge_memory_diffs(
                    memory_diffs,
                    archive_uri=archive_uri,
                )
            return response
        except Exception as exc:
            raise ExperienceTrainingError(str(exc)) from exc

    @tracer.errors_to_trace()
    @tracer("train.compressor_v3.learn_pending_experiences", ignore_result=True, ignore_args=True)
    async def learn_pending_experiences(
        self,
        *,
        ctx: RequestContext,
        trajectory_uris: list[str] | None = None,
        case_names: list[str] | None = None,
        max_cases: int = 100,
        retry_failed: bool = False,
        lease_seconds: int = 900,
        on_progress: Callable[[dict[str, int]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Consume selected Case inboxes; trajectory_uris identify Cases, not a file whitelist."""
        if max_cases <= 0:
            raise ValueError("max_cases must be > 0")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        async with self._experience_learning_lock:
            viking_fs = get_viking_fs()
            from openviking.session.memory.account_templates import resolve_account_memory_registry

            registry = await resolve_account_memory_registry(
                viking_fs, ctx.account_id, get_default_registry()
            )
            candidates = await self._load_pending_trajectory_memories(
                viking_fs=viking_fs,
                ctx=ctx,
                registry=registry,
                trajectory_uris=trajectory_uris,
                case_names=case_names,
                retry_failed=retry_failed,
            )
            grouped: dict[str, list[MemoryFile]] = {}
            for memory in candidates:
                case_name = str(memory.extra_fields.get("case_name") or "")
                if case_name:
                    grouped.setdefault(case_name, []).append(memory)

            finalize_lock = asyncio.Lock()

            async def learn_case(case_name: str) -> dict[str, Any]:
                group = grouped[case_name]
                chunk_size = self.streaming_trainer_config.max_gradients_per_update
                if chunk_size <= 0:
                    raise ValueError("max_gradients_per_update must be > 0")
                target_uri = _experience_uri_for_case(registry, case_name=case_name, ctx=ctx)
                result: dict[str, Any] = {
                    "case_name": case_name,
                    "trajectory_uris": [],
                    "experience_uri": target_uri,
                    "written_experience_uris": [],
                    "plan_metadata": {"chunk_count": 0, "chunk_item_counts": [], "chunks": []},
                }
                for offset in range(0, len(group), chunk_size):
                    # Claim only the next synthesis batch. A failure stops this Case,
                    # but later observations have never left their pending state.
                    batch_id = uuid4().hex
                    claimed = []
                    try:
                        lease_until = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                        for memory in group[offset : offset + chunk_size]:
                            claimed.append(
                                await self._write_trajectory_update_state(
                                    viking_fs=viking_fs,
                                    memory=memory,
                                    ctx=ctx,
                                    state="processing",
                                    batch_id=batch_id,
                                    lease_until=lease_until,
                                )
                            )
                        tracer.info(
                            f"Experience learning chunk claimed case={case_name} "
                            f"chunk_index={offset // chunk_size} batch_id={batch_id} "
                            f"trajectory_count={len(claimed)} "
                            f"unclaimed={len(group) - offset - len(claimed)}"
                        )
                        chunk_result = await self._learn_one_trajectory_case(
                            case_name=case_name,
                            memories=claimed,
                            batch_id=batch_id,
                            ctx=ctx,
                            viking_fs=viking_fs,
                            registry=registry,
                            finalize_lock=finalize_lock,
                        )
                    except Exception as exc:
                        tracer.error(
                            f"Experience learning stopped Case {case_name} "
                            f"at chunk {offset // chunk_size}; claimed={len(claimed)} "
                            f"unclaimed={len(group) - offset - len(claimed)}",
                            e=exc,
                            console=False,
                        )
                        for memory in claimed:
                            try:
                                updated = await self._write_trajectory_update_state(
                                    viking_fs=viking_fs,
                                    memory=memory,
                                    ctx=ctx,
                                    state="failed",
                                    batch_id=batch_id,
                                    error=str(exc),
                                )
                                memory.uri = updated.uri
                                # Archival can fail after learning completed. Preserve
                                # terminal states and retry only archival in that case.
                                if _trajectory_update_state(updated)["state"] == "applied":
                                    result["written_experience_uris"].append(target_uri)
                            except Exception as state_exc:
                                tracer.info(
                                    "Failed to persist Trajectory failure state: "
                                    f"trajectory={memory.uri} error={state_exc}"
                                )
                        result["trajectory_uris"].extend(memory.uri for memory in claimed)
                        result["written_experience_uris"] = list(
                            dict.fromkeys(result["written_experience_uris"])
                        )
                        return {**result, "state": "failed", "error": str(exc)}

                    result["trajectory_uris"].extend(chunk_result["trajectory_uris"])
                    result["written_experience_uris"].extend(
                        chunk_result["written_experience_uris"]
                    )
                    metadata = result["plan_metadata"]
                    chunk_metadata = chunk_result["plan_metadata"]
                    metadata["chunk_count"] += chunk_metadata["chunk_count"]
                    metadata["chunk_item_counts"].extend(chunk_metadata["chunk_item_counts"])
                    metadata["chunks"].extend(chunk_metadata["chunks"])
                result["written_experience_uris"] = list(
                    dict.fromkeys(result["written_experience_uris"])
                )
                terminal_state = "applied" if result["written_experience_uris"] else "no_op"
                return {**result, "state": terminal_state}

            selected_cases = sorted(grouped)[:max_cases]
            progress = {
                "total": len(selected_cases),
                "completed": 0,
                "running": 0,
                "applied": 0,
                "no_op": 0,
                "failed": 0,
            }
            progress_lock = asyncio.Lock()

            async def report_progress(*, started: bool = False, state: str | None = None) -> None:
                # Serialize publication so a slower persistence write cannot regress counts.
                async with progress_lock:
                    if started:
                        progress["running"] += 1
                    if state is not None:
                        progress["running"] -= 1
                        progress["completed"] += 1
                        progress[state] += 1
                    if on_progress is not None:
                        await on_progress(dict(progress))

            async def learn_with_progress(case_name: str) -> dict[str, Any]:
                await report_progress(started=True)
                result = await learn_case(case_name)
                # Count completion only after terminal Trajectory states are persisted.
                await report_progress(state=result["state"])
                return result

            await report_progress()
            results = await bounded_map(
                selected_cases,
                learn_with_progress,
                concurrency=self.streaming_trainer_config.max_concurrent_cases,
            )
            return {
                "case_count": len(results),
                "trajectory_count": sum(len(item["trajectory_uris"]) for item in results),
                "applied_count": sum(item["state"] == "applied" for item in results),
                "no_op_count": sum(item["state"] == "no_op" for item in results),
                "failed_count": sum(item["state"] == "failed" for item in results),
                "written_experience_uris": list(
                    dict.fromkeys(
                        uri for item in results for uri in item["written_experience_uris"]
                    )
                ),
                "results": results,
            }

    async def _learn_one_trajectory_case(
        self,
        *,
        case_name: str,
        memories: list[MemoryFile],
        batch_id: str,
        ctx: RequestContext,
        viking_fs: Any,
        registry: Any,
        finalize_lock: asyncio.Lock,
    ) -> dict[str, Any]:
        """Learn one already-claimed, bounded Trajectory batch for a Case."""
        if not memories or len(memories) > self.streaming_trainer_config.max_gradients_per_update:
            raise ValueError("Trajectory learning requires one non-empty bounded batch")
        case_uri = _case_uri_for_name(registry, case_name=case_name, ctx=ctx)
        case_memory = MemoryFileUtils.read(
            await viking_fs.read_file(case_uri, ctx=ctx),
            uri=case_uri,
        )
        case = _memory_file_to_case(case_memory)
        if case is None or case.name != case_name:
            raise ValueError(f"Invalid canonical Case for pending Trajectories: {case_name}")

        exp_root_uri = _experience_root_uri(ctx)
        policy_set = ExperienceSet(
            root_uri=exp_root_uri, policies=[], viking_fs=viking_fs, request_context=ctx
        )
        target_uri = _experience_uri_for_case(registry, case_name=case_name, ctx=ctx)
        signals = []
        for memory in memories:
            source_session_uri = trajectory_source_session_uri(memory)
            metadata = dict(memory.extra_fields)
            metadata["source_session_uri"] = source_session_uri
            # Opaque text chunks fit the Gate's existing evidence field, not parsed steps.
            metadata["gate_evidence"] = [
                {
                    "id": f"trajectory:content:{offset}",
                    "kind": "trajectory_content",
                    "summary": memory.content[offset : offset + MAX_EVIDENCE_SUMMARY_CHARS],
                }
                for offset in range(0, len(memory.content), MAX_EVIDENCE_SUMMARY_CHARS)
            ]
            evaluation = _trajectory_evaluation(metadata.get("evaluation"))
            trajectory = Trajectory(
                name=str(metadata["trajectory_name"]),
                uri=memory.uri,
                content=memory.content,
                outcome=str(metadata["outcome"]),
                metadata=metadata,
            )
            rollout = Rollout(
                case=case,
                messages=[],
                policy_snapshot_id=f"trajectory-batch:{batch_id}",
                evaluation=evaluation,
                metadata={"source_session_uri": source_session_uri},
            )
            analysis = RolloutAnalysis(
                evaluation=evaluation,
                trajectories=[trajectory],
                metadata={
                    "source_session_uri": source_session_uri,
                    "case_uri": case_uri,
                    "experience_execution": metadata.get("experience_execution") or {},
                },
                rollout=rollout,
            )
            signals.append(
                ObservationGradient(
                    analysis=analysis,
                    target_name=case_name,
                    target_uri=target_uri,
                    after_file=memory,
                    links=[
                        StoredLink(
                            from_uri=target_uri,
                            to_uri=trajectory_archive_uri(memory.uri),
                            link_type="derived_from",
                            weight=1.0,
                        )
                    ],
                )
            )

        optimizer = TrajectoryExperienceOptimizer(
            viking_fs=viking_fs,
            estimator=ExperienceGradientEstimator(
                viking_fs=viking_fs,
                vlm_resolver=self.vlm_resolver,
            ),
            memory_registry=registry,
        )

        async def finalize(training_result: RolloutTrainingResult) -> None:
            plan = training_result.plan
            apply_result = training_result.apply_result
            if apply_result.errors:
                raise ExperienceTrainingError("; ".join(apply_result.errors))
            batch_signals = training_result.gradients
            visible_uris = _visible_experience_snapshot_uris(plan=plan, apply_result=apply_result)
            source_session_uris = [
                signal.analysis.metadata["source_session_uri"] for signal in batch_signals
            ]
            async with finalize_lock:
                await _commit_experience_snapshot(
                    viking_fs,
                    ctx=ctx,
                    experience_uris=visible_uris,
                    archive_uri=f"trajectory-batch:{batch_id}",
                    experience_session_map={target_uri: source_session_uris},
                )
            completed = []
            for signal in batch_signals:
                updated = await self._write_trajectory_update_state(
                    viking_fs=viking_fs,
                    memory=signal.after_file,
                    ctx=ctx,
                    state="applied" if visible_uris else "no_op",
                    batch_id=batch_id,
                )
                completed.append((signal, updated))
            # All signals in this applied chunk have already contributed. An archive
            # failure must not send the remaining signals through learning again.
            for signal, updated in completed:
                archived = await archive_trajectory(
                    updated,
                    viking_fs=viking_fs,
                    ctx=ctx,
                    registry=registry,
                    vikingdb=self.vikingdb,
                )
                signal.after_file.uri = archived.uri
                signal.after_file.extra_fields = archived.extra_fields

        # Use the same count/window batching and serial updates as other gradients.
        trainer = StreamingPolicyTrainer(
            policy_set=policy_set,
            rollout_analyzer=self.rollout_analyzer,
            gradient_estimator=_NoopGradientEstimator(),
            policy_optimizer=optimizer,
            policy_updater=MemoryFilePolicyUpdater(viking_fs=viking_fs, vikingdb=self.vikingdb),
            context=PipelineContext(
                gradient_context=ExperienceGradientContext(ctx, [], memory_registry=registry),
                optimization_context=PatchMergePolicyOptimizerContext(request_context=ctx),
                apply_context=ctx,
            ),
            # This is a complete, bounded batch, not a stream waiting for more
            # observations. Flush a short final batch immediately as well.
            config=replace(self.streaming_trainer_config, max_gradients_per_update=len(signals)),
        )
        try:
            training_result = await trainer.submit_gradients(signals, batch_finalizer=finalize)
        finally:
            await trainer.close()
        plan = training_result.plan
        visible_uris = _visible_experience_snapshot_uris(
            plan=plan, apply_result=training_result.apply_result
        )
        return {
            "case_name": case_name,
            "trajectory_uris": [memory.uri for memory in memories],
            "experience_uri": target_uri,
            "written_experience_uris": visible_uris,
            "plan_metadata": plan.metadata,
        }

    async def _load_pending_trajectory_memories(
        self,
        *,
        viking_fs: Any,
        ctx: RequestContext,
        registry: Any,
        trajectory_uris: list[str] | None,
        case_names: list[str] | None,
        retry_failed: bool,
    ) -> list[MemoryFile]:
        """Snapshot eligible work from inboxes selected by Case names or trigger URIs."""
        root = f"viking://user/{ctx.user.user_id}/memories/trajectories"
        trigger_uris = list(dict.fromkeys(str(uri) for uri in trajectory_uris or [] if uri))
        selected_cases = dict.fromkeys(str(name) for name in case_names or [] if name)
        read_cache: dict[str, MemoryFile] = {}
        case_directories: dict[str, str] = {}

        async def read_trajectory(uri: str) -> MemoryFile:
            if not uri.startswith(root + "/") or not uri.endswith(".md"):
                raise ValueError(f"Trajectory URI is outside the current user's root: {uri}")
            if uri in read_cache:
                return read_cache[uri]
            requested_uri = uri
            # A notification can retain its inbox address after a preceding job
            # archived it. Resolve that single file, never scan archival history.
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx)
            except Exception as exc:
                if not is_storage_not_found(exc):
                    raise
                final_uri = trajectory_archive_uri(uri)
                if final_uri == uri:
                    raise
                uri = final_uri
                raw = await viking_fs.read_file(uri, ctx=ctx)
            memory = MemoryFileUtils.read(raw, uri=uri)
            if memory.memory_type != "trajectories":
                raise ValueError(f"Not a Trajectory memory: {uri}")
            read_cache[requested_uri] = read_cache[uri] = memory
            return memory

        trajectory_schema = registry.get(_TRAJECTORIES_MEMORY_TYPE)

        def case_directory(case_name: str) -> str:
            if trajectory_schema is None or not trajectory_schema.enabled:
                raise ValueError("Trajectory schema is required for inbox learning")
            if case_name not in case_directories:
                case_directories[case_name] = generate_uri(
                    trajectory_schema.model_copy(update={"filename_template": ""}),
                    {"case_name": case_name},
                    user_space=ctx.user.user_id,
                ).rstrip("/")
            return case_directories[case_name]

        for uri in trigger_uris:
            memory = await read_trajectory(uri)
            case_name = str(memory.extra_fields.get("case_name") or "")
            if not case_name or not memory.uri.startswith(case_directory(case_name) + "/"):
                raise ValueError(f"Trajectory Case identity does not match its directory: {uri}")
            selected_cases[case_name] = None

        directories = [case_directory(name) + "/inbox" for name in selected_cases]
        if not selected_cases and trajectory_uris is None and not case_names:
            # An explicitly unfiltered request discovers Case directories only.
            # An empty trigger list must never expand into a global scan.
            offset = 0
            while True:
                try:
                    entries = await viking_fs.ls(
                        root, output="original", node_limit=1000, offset=offset, ctx=ctx
                    )
                except Exception as exc:
                    if is_storage_not_found(exc):
                        break
                    raise
                for entry in entries:
                    uri = str(entry.get("uri") or "").rstrip("/")
                    if (
                        (entry.get("isDir") or entry.get("is_dir"))
                        and uri.rsplit("/", 1)[0] == root
                        and not uri.rsplit("/", 1)[-1].startswith(".")
                    ):
                        directories.append(uri + "/inbox")
                if len(entries) < 1000:
                    break
                offset += len(entries)
        uris = []
        for directory in directories:
            uris.extend(await list_trajectory_uris(viking_fs, ctx, directory))

        memories = []
        for uri in dict.fromkeys(uris):
            memory = await read_trajectory(uri)
            uri = memory.uri
            case_name = str(memory.extra_fields.get("case_name") or "")
            if selected_cases and case_name not in selected_cases:
                continue
            raw_update_state = memory.extra_fields.get(_EXPERIENCE_UPDATE_FIELD)
            if raw_update_state is None:
                # Historical Trajectories remain comparison evidence, but Phase 2 must not
                # reinterpret records created before the state machine as new pending work.
                continue
            if isinstance(raw_update_state, str):
                try:
                    raw_update_state = json.loads(raw_update_state)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid Trajectory experience_update metadata: {uri}"
                    ) from exc
            if not isinstance(raw_update_state, dict) or not raw_update_state.get("state"):
                continue
            if memory.extra_fields.get("trajectory_format") != TRAJECTORY_FORMAT:
                continue
            final_uri = trajectory_archive_uri(uri)
            state = _trajectory_update_state(memory)
            if state["state"] not in {
                "pending",
                "processing",
                "applied",
                "no_op",
                "failed",
            }:
                raise ValueError(f"Invalid Trajectory experience_update state: {uri}")
            if state["state"] in _EXPERIENCE_UPDATE_TERMINAL_STATES:
                if uri != final_uri:
                    await archive_trajectory(
                        memory,
                        viking_fs=viking_fs,
                        ctx=ctx,
                        registry=registry,
                        vikingdb=self.vikingdb,
                    )
                continue
            if uri == final_uri:
                raise ValueError(f"Unfinished Trajectory in archive: {uri}")
            if state["state"] == "failed" and not retry_failed:
                continue
            if state["state"] == "processing" and not _trajectory_lease_expired(state):
                continue
            trajectory_source_session_uri(memory)
            memories.append(memory)

        def creation_order(memory: MemoryFile) -> tuple[datetime, str]:
            try:
                created = parse_iso_datetime(str(memory.extra_fields.get("created_at")))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid Trajectory created_at: {memory.uri}") from exc
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return created, memory.uri

        # Notifications do not jump ahead of older pending work in the same Case.
        memories.sort(key=creation_order)
        return memories

    async def _write_trajectory_update_state(
        self,
        *,
        viking_fs: Any,
        memory: MemoryFile,
        ctx: RequestContext,
        state: str,
        batch_id: str,
        lease_until: datetime | None = None,
        error: str | None = None,
    ) -> MemoryFile:
        path = viking_fs._uri_to_path(memory.uri, ctx=ctx)
        lock = await viking_fs._async_agfs.pathlock_acquire_exact(path, timeout_secs=30)
        try:
            uri = memory.uri
            if not await viking_fs.exists(uri, ctx=ctx):
                uri = trajectory_archive_uri(uri)
            latest = MemoryFileUtils.read(
                await viking_fs.read_file(uri, ctx=ctx),
                uri=uri,
            )
            previous = _trajectory_update_state(latest)
            # A later chunk failure must not undo a completed chunk's terminal state.
            if state == "failed" and previous["state"] in _EXPERIENCE_UPDATE_TERMINAL_STATES:
                return latest
            if previous["state"] in _EXPERIENCE_UPDATE_TERMINAL_STATES:
                raise ValueError(f"Trajectory learning is already complete: {uri}")
            if state == "processing":
                if previous["state"] == "processing" and not _trajectory_lease_expired(previous):
                    raise ValueError(f"Trajectory is already being learned: {uri}")
            elif previous.get("batch_id") != batch_id:
                raise ValueError(f"Trajectory learning lease changed: {uri}")
            latest.extra_fields[_EXPERIENCE_UPDATE_FIELD] = {
                **previous,
                "state": state,
                "attempts": int(previous.get("attempts") or 0)
                + (1 if state == "processing" else 0),
                "batch_id": batch_id,
                "lease_until": lease_until.isoformat() if lease_until is not None else None,
                "processed_at": (
                    datetime.now(timezone.utc).isoformat()
                    if state in _EXPERIENCE_UPDATE_TERMINAL_STATES | {"failed"}
                    else None
                ),
                "error": str(error or "")[:2_000] or None,
            }
            await viking_fs.write_file(
                latest.uri,
                MemoryFileUtils.write(latest),
                ctx=ctx,
                lease_ref=lock,
            )
            return latest
        finally:
            await viking_fs._async_agfs.pathlock_release(lock)

    async def _build_training_memory_diff(
        self,
        *,
        training_result: RolloutTrainingResult,
        viking_fs: Any,
        ctx: RequestContext,
        archive_uri: str,
    ) -> dict[str, Any]:
        adds: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        deletes: list[dict[str, Any]] = []

        source_session_uris = {
            str(analysis.metadata["source_session_uri"])
            for analysis in training_result.analyses
            if analysis.metadata.get("source_session_uri")
        }

        applied_uris = set(training_result.apply_result.written_uris)
        deleted_uris = set(training_result.apply_result.deleted_uris)
        root_uri = training_result.apply_result.updated_policy_set.root_uri
        if not root_uri:
            raise ValueError(
                "PolicyApplyResult.updated_policy_set.root_uri is required for training memory diff"
            )

        for item in training_result.plan.items:
            if item.memory_type != "experiences":
                continue
            if source_session_uris and not _plan_item_has_source_session(
                item,
                source_session_uris,
            ):
                continue
            uri = _experience_plan_item_uri(item, root_uri)
            if not uri:
                continue
            if item.kind == "delete":
                if uri in deleted_uris:
                    deletes.append(
                        {
                            "uri": uri,
                            "memory_type": "experiences",
                            "deleted_content": item.before_content or "",
                        }
                    )
                continue
            if item.kind != "upsert" or uri not in applied_uris:
                continue
            after = await _read_plain_memory_content(
                viking_fs,
                uri=uri,
                ctx=ctx,
                fallback=item.after_content or "",
            )
            before = item.before_content
            if before is None:
                adds.append({"uri": uri, "memory_type": "experiences", "after": after})
            else:
                # Filter no-op experience updates the same way as user-memory
                # updates: a patch that re-serializes to identical content should
                # not appear in memory_diff.json.
                try:
                    old_file = MemoryFileUtils.read(before, uri=uri) if before else None
                    new_file = MemoryFileUtils.read(after, uri=uri) if after else None
                except Exception:
                    old_file, new_file = None, None
                if old_file is not None and _same_memory_file(old_file, new_file):
                    logger.info("Skipping unchanged experience memory in memory_diff.json: %s", uri)
                    continue
                updates.append(
                    {
                        "uri": uri,
                        "memory_type": "experiences",
                        "before": before,
                        "after": after,
                    }
                )

        return _make_memory_diff(
            archive_uri=archive_uri,
            adds=adds,
            updates=updates,
            deletes=deletes,
            skipped_operations=[],
        )

    async def _write_final_memory_diff(
        self,
        *,
        archive_uri: str,
        ctx: Optional[RequestContext],
        memory_diffs: list[Any],
    ) -> None:
        if not archive_uri or ctx is None:
            return
        merged = _merge_memory_diffs(
            [diff for diff in memory_diffs if isinstance(diff, dict)],
            archive_uri=archive_uri,
        )
        viking_fs = get_viking_fs()
        if viking_fs is None:
            return
        await viking_fs.write_file(
            uri=f"{archive_uri.rstrip('/')}/memory_diff.json",
            content=json.dumps(merged, ensure_ascii=False, indent=4),
            ctx=ctx,
        )


@dataclass(slots=True)
class _V3ExtractionResult:
    contexts: list[Context] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    memory_diff: dict[str, Any] | None = None
    case_uri_by_name: dict[str, str] = field(default_factory=dict)
    skipped_operations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _V3AppliedMemory:
    result: Any
    operations: ResolvedOperations
    memory_diff: dict[str, Any] | None = None


def _contexts_from_update_result(result: Any) -> list[Context]:
    contexts = []
    for uri in result.written_uris:
        contexts.append(Context(uri=uri, category="memory_write", context_type="memory"))
    for uri in result.edited_uris:
        contexts.append(Context(uri=uri, category="memory_edit", context_type="memory"))
    for uri in result.deleted_uris:
        contexts.append(Context(uri=uri, category="memory_delete", context_type="memory"))
    return contexts


def _training_case_from_first_message(messages: list[Message]) -> Case | None:
    """Read the concrete task envelope, never use it as a memory Case identity."""
    if not messages:
        return None
    payload = _training_case_spec_payload_from_message(messages[0])
    return _case_from_payload(payload) if payload is not None else None


def _allowed_agent_memory_types(allowed_memory_types: Optional[set[str]]) -> set[str]:
    if allowed_memory_types is None:
        return set(_AGENT_MEMORY_TYPES)
    return set(allowed_memory_types) & _AGENT_MEMORY_TYPES


def _training_case_spec_payload_from_message(message: Message) -> dict[str, Any] | None:
    text = _message_text(message).strip()
    if not text.startswith(_TRAINING_CASE_SPEC_HEADER):
        return None
    return _parse_training_case_spec_payload(text)


def _case_rubric_model_evidence(
    task: Case | None, evaluation: RubricEvaluation | None
) -> list[str]:
    """Project rubric evidence without exposing the concrete task's control-plane identity."""
    documents = []
    descriptions = {}
    if task is not None:
        descriptions = {item.name: item.description for item in task.rubric.criteria}
        documents.append(
            {
                # These are the business fields carried by the CaseSpec transport.
                # Split/task/trial identifiers remain in the original envelope, not this view.
                "task_context": {
                    key: task.input[key] for key in ("domain", "user_query") if key in task.input
                },
                "rubric": {
                    "description": task.rubric.description,
                    "criteria": [
                        {
                            "description": item.description,
                            "required": item.required,
                            "weight": item.weight,
                        }
                        for item in task.rubric.criteria
                    ],
                },
            }
        )
    if evaluation is not None:
        report = asdict(evaluation)
        for result in report["criterion_results"]:
            # Bind reports to the criterion text, not potentially task-derived labels.
            name = result.pop("criterion_name")
            if name in descriptions:
                result["description"] = descriptions[name]
        documents.append({"external_evaluation": report})
    return [json.dumps(document, ensure_ascii=False) for document in documents]


def _training_evaluation_from_messages(
    messages: list[Message],
) -> RubricEvaluation | None:
    evaluation, _ = _training_outcome_from_messages(messages)
    return evaluation


def _training_outcome_from_messages(
    messages: list[Message],
) -> tuple[RubricEvaluation | None, dict[str, dict[str, Any]]]:
    for message in reversed(messages):
        text = _message_text(message).strip()
        if not text.startswith(_TRAINING_EVALUATION_HEADER):
            continue
        match = _JSON_FENCE_RE.search(text)
        raw_payload = (
            match.group(1).strip()
            if match
            else text.removeprefix(_TRAINING_EVALUATION_HEADER).strip()
        )
        if not raw_payload:
            raise ValueError("Training OutcomeEvaluation payload is empty")
        try:
            payload = JsonUtils.loads(raw_payload)
        except Exception as exc:
            raise ValueError("Training OutcomeEvaluation payload is not valid JSON") from exc
        raw_evaluation = payload.get("evaluation") if isinstance(payload, dict) else None
        raw_execution = payload.get("experience_execution") if isinstance(payload, dict) else None
        if raw_execution is None:
            experience_execution: dict[str, dict[str, Any]] = {}
        elif not isinstance(raw_execution, dict) or any(
            not isinstance(uri, str) or not uri or not isinstance(snapshot, dict)
            for uri, snapshot in raw_execution.items()
        ):
            raise ValueError(
                "Training OutcomeEvaluation experience_execution must be an object of snapshots"
            )
        else:
            experience_execution = {
                str(uri): dict(snapshot) for uri, snapshot in raw_execution.items()
            }
        if raw_evaluation is None:
            return None, experience_execution
        if not isinstance(raw_evaluation, dict):
            raise ValueError("Training OutcomeEvaluation evaluation must be an object or null")
        criterion_results: list[CriterionResult] = []
        raw_criteria = raw_evaluation.get("criterion_results")
        for index, raw_criterion in enumerate(
            raw_criteria if isinstance(raw_criteria, list) else []
        ):
            if not isinstance(raw_criterion, dict):
                raise ValueError("Training OutcomeEvaluation criteria must be objects")
            criterion_results.append(
                CriterionResult(
                    criterion_name=str(
                        raw_criterion.get("criterion_name") or f"criterion_{index + 1}"
                    ),
                    passed=bool(raw_criterion.get("passed", False)),
                    score=float(raw_criterion.get("score") or 0.0),
                    feedback=_string_list(raw_criterion.get("feedback")),
                    evidence=_string_list(raw_criterion.get("evidence")),
                    metadata=(
                        dict(raw_criterion.get("metadata") or {})
                        if isinstance(raw_criterion.get("metadata"), dict)
                        else {}
                    ),
                )
            )
        return (
            RubricEvaluation(
                passed=bool(raw_evaluation.get("passed", False)),
                score=float(raw_evaluation.get("score") or 0.0),
                criterion_results=criterion_results,
                feedback=_string_list(raw_evaluation.get("feedback")),
                metadata=(
                    dict(raw_evaluation.get("metadata") or {})
                    if isinstance(raw_evaluation.get("metadata"), dict)
                    else {}
                ),
            ),
            experience_execution,
        )
    return None, {}


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _message_text(message: Message) -> str:
    content = getattr(message, "content", "")
    if content:
        return str(content)
    texts: list[str] = []
    for part in getattr(message, "parts", []) or []:
        text = getattr(part, "text", None)
        if text:
            texts.append(str(text))
    return "\n".join(texts)


def _training_conversation_messages(messages: list[Message]) -> list[Message]:
    """Remove batch-training control envelopes from the business conversation."""
    return [
        message
        for message in messages
        if not _message_text(message)
        .strip()
        .startswith((_TRAINING_CASE_SPEC_HEADER, _TRAINING_EVALUATION_HEADER))
    ]


def _parse_training_case_spec_payload(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    raw_payload = (
        match.group(1).strip() if match else text.removeprefix(_TRAINING_CASE_SPEC_HEADER).strip()
    )
    if not raw_payload:
        raise ValueError("Training CaseSpec payload is empty")
    try:
        payload = JsonUtils.loads(raw_payload)
    except Exception as exc:
        raise ValueError("Training CaseSpec payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Training CaseSpec payload must be a JSON object")
    protocol = str(payload.get("protocol") or "")
    if protocol != _TRAINING_CASE_SPEC_PROTOCOL:
        raise ValueError(
            "Training CaseSpec protocol mismatch: "
            f"expected {_TRAINING_CASE_SPEC_PROTOCOL!r}, got {protocol!r}"
        )
    if not isinstance(payload.get("case"), dict):
        raise ValueError("Training CaseSpec payload must contain a case object")
    return payload


def _case_from_payload(payload: dict[str, Any]) -> Case:
    raw_case = payload["case"]
    name = str(raw_case.get("name") or "").strip()
    task_signature = str(raw_case.get("task_signature") or "").strip()
    if not name:
        raise ValueError("Training CaseSpec case.name is required")
    if not task_signature:
        raise ValueError("Training CaseSpec case.task_signature is required")
    case_input = raw_case.get("input")
    if not isinstance(case_input, dict):
        raise ValueError("Training CaseSpec case.input must be an object")
    rubric = _rubric_from_payload(raw_case.get("rubric"), fallback_name=f"{name}_rubric")
    metadata = dict(raw_case.get("metadata")) if isinstance(raw_case.get("metadata"), dict) else {}
    metadata.pop("experience_execution", None)
    return Case(
        name=name,
        task_signature=task_signature,
        input=dict(case_input),
        rubric=rubric,
        metadata={
            "source": "batch_training_case_spec",
            **metadata,
        },
    )


def _rubric_from_payload(raw_rubric: Any, *, fallback_name: str) -> Rubric:
    if not isinstance(raw_rubric, dict):
        raise ValueError("Training CaseSpec case.rubric must be an object")
    raw_criteria = raw_rubric.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ValueError("Training CaseSpec case.rubric.criteria must be a non-empty list")

    criteria: list[RubricCriterion] = []
    for index, item in enumerate(raw_criteria):
        if not isinstance(item, dict):
            raise ValueError("Training CaseSpec rubric criteria must be objects")
        name = str(item.get("name") or f"criterion_{index + 1}").strip()
        description = str(item.get("description") or "").strip()
        if not description:
            raise ValueError("Training CaseSpec rubric criterion.description is required")
        criteria.append(
            RubricCriterion(
                name=name,
                description=description,
                required=bool(item.get("required", True)),
                weight=_safe_weight(item.get("weight"), default=1.0),
                metadata=dict(item.get("metadata") or {})
                if isinstance(item.get("metadata"), dict)
                else {},
            )
        )

    return Rubric(
        name=str(raw_rubric.get("name") or fallback_name),
        description=str(
            raw_rubric.get("description") or "Defines what good means for this batch training case."
        ),
        criteria=criteria,
        metadata=dict(raw_rubric.get("metadata") or {})
        if isinstance(raw_rubric.get("metadata"), dict)
        else {},
    )


async def _canonical_cases_from_update_result(
    *,
    operations: ResolvedOperations,
    result: Any,
    viking_fs: Any,
    ctx: RequestContext,
    requested_operations: ResolvedOperations,
) -> list[Case]:
    """Resolve applied or unchanged Case contributions to their persisted scenario.

    Use the merge result for canonicalized names. An explicitly proposed existing
    target still contributes a trajectory when its immutable fields need no edit.
    Explicit references associate an execution without any Case write. Prefetched/search-hit
    memories alone are never evidence of association.
    """
    selected = {
        uri
        for ref in [*requested_operations.case_references, *operations.case_references]
        for uri in ref.uris
        if ref.memory_type == _CASES_MEMORY_TYPE
    }
    touched = set(_case_result_touched_uris(result))
    uris = [
        uri
        for op in operations.upsert_operations
        if op.memory_type == _CASES_MEMORY_TYPE
        for uri in op.uris
        if uri in touched
    ]
    deleted = set(getattr(result, "deleted_uris", []) or [])
    skipped = {item.uri for item in getattr(result, "skipped_operations", []) or [] if item.uri}
    canonical_uris = selected | {
        uri
        for op in operations.upsert_operations
        if op.memory_type == _CASES_MEMORY_TYPE
        for uri in op.uris
    }
    if not getattr(result, "errors", None) and not operations.errors:
        # Unchanged existing Cases may be omitted from the merge's write set.
        # A policy-filtered request must not acquire an association through this path.
        for op in requested_operations.upsert_operations:
            if op.memory_type != _CASES_MEMORY_TYPE:
                continue
            if any(
                item.memory_type == _CASES_MEMORY_TYPE and (item.uri in op.uris or item.uri is None)
                for item in getattr(result, "skipped_operations", []) or []
            ):
                continue
            # A final Case selection supersedes the original proposal, even if the
            # proposed alias happens to exist on disk as another Case.
            uris.extend(uri for uri in op.uris if not canonical_uris or uri in canonical_uris)
    cases = []
    for uri in dict.fromkeys([*uris, *sorted(selected)]):
        if uri in deleted or uri in skipped or not _uri_is_case_memory_file(uri):
            if uri in selected:
                raise ValueError(f"Selected Case is not available after the memory update: {uri}")
            continue
        case = await _case_from_persisted_memory_file(uri=uri, viking_fs=viking_fs, ctx=ctx)
        if case is not None:
            cases.append(case)
        elif uri in selected:
            raise ValueError(f"Selected Case no longer exists: {uri}")
    return cases


def _case_result_touched_uris(result: Any) -> list[str]:
    uris = list(getattr(result, "written_uris", []) or []) + list(
        getattr(result, "edited_uris", []) or []
    )
    return [
        uri
        for uri in dict.fromkeys(str(uri) for uri in uris if uri)
        if _uri_is_case_memory_file(uri)
    ]


def _uri_is_case_memory_file(uri: str) -> bool:
    return "/memories/cases/" in str(uri or "") and not str(uri).rstrip("/").endswith(
        ("/.overview.md", "/.abstract.md")
    )


async def _case_from_persisted_memory_file(
    *,
    uri: str,
    viking_fs: Any,
    ctx: RequestContext,
) -> Case | None:
    try:
        raw = await viking_fs.read_file(uri, ctx=ctx)
    except Exception as exc:
        if is_storage_not_found(exc):
            return None
        raise
    return _memory_file_to_case(MemoryFileUtils.read(raw, uri=uri))


def _memory_file_to_case(memory_file: MemoryFile) -> Case | None:
    if memory_file.memory_type != _CASES_MEMORY_TYPE:
        return None
    fields = dict(getattr(memory_file, "extra_fields", {}) or {})
    fields.pop("evidence", None)
    uri = str(getattr(memory_file, "uri", "") or "")
    removed_fields = sorted({"task_signature", "input", "domain_scope"} & fields.keys())
    if removed_fields:
        raise ValueError(
            f"Case memory {uri!r} uses removed fields {removed_fields}; migrate task_signature "
            "or domain_scope to domain, and input to description before loading it."
        )
    name = str(fields.get("case_name") or fields.get("name") or _basename_case_name(uri)).strip()
    domain = str(fields.get("domain") or name).strip()
    if not name or not domain:
        return None
    memory_fields = dict(fields)
    if uri:
        memory_fields.setdefault("uri", uri)
    return Case(
        name=name,
        task_signature=domain,
        input={"summary": str(fields.get("description") or "")},
        rubric=Rubric(
            name=f"{name}_rubric",
            description="",
            criteria=[
                RubricCriterion(
                    name=item["id"], description=item["description"], required=True, weight=1.0
                )
                for item in parse_case_rubric(fields.get("rubric", "[]"))
            ],
        ),
        metadata={
            "source": "session_commit_case_memory",
            "case_uris": [uri] if uri else [],
            "memory_fields": memory_fields,
        },
    )


def _safe_weight(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _basename_case_name(uri: str | None) -> str:
    if uri:
        return uri.rstrip("/").split("/")[-1].removesuffix(".md")
    return ""


def _user_space_from_ctx(ctx: RequestContext, *, purpose: str) -> str:
    user_space = getattr(getattr(ctx, "user", None), "user_id", None)
    if not user_space:
        raise ValueError(f"RequestContext.user.user_id is required for {purpose}")
    return str(user_space)


def _experience_root_uri(ctx: RequestContext) -> str:
    user_space = _user_space_from_ctx(ctx, purpose="experience memory root URI")
    return f"viking://user/{user_space}/memories/experiences"


def _skill_root_uri(ctx: RequestContext) -> str:
    user_space = _user_space_from_ctx(ctx, purpose="skill root URI")
    return f"viking://user/{user_space}/skills"


def _skill_trainer_key(ctx: RequestContext) -> tuple[str, str, str]:
    """Registry key for the skill streaming trainer (separate from exp trainer)."""
    from openviking.session.train.components.policy_trainer import (
        make_streaming_policy_trainer_key,
    )

    return make_streaming_policy_trainer_key(
        policy_root_uri=_skill_root_uri(ctx),
        request_context=ctx,
    )


@dataclass(slots=True)
class _NoopGradientEstimator:
    """GradientEstimator that returns empty gradients.

    Skills and persisted Trajectory observations are supplied directly through
    ``submit_gradients``. That path bypasses estimation,
    but ``StreamingPolicyTrainer`` still requires this component.
    """

    async def estimate(
        self,
        analysis: Any,
        policy_set: Any,
        context: Any = None,
    ) -> list[Any]:
        return []


def _case_uri_for_case(case: Case, case_uri_by_name: dict[str, str]) -> str:
    if case.name in case_uri_by_name:
        return case_uri_by_name[case.name]
    uris = (case.metadata or {}).get("case_uris")
    if isinstance(uris, list) and uris:
        return str(uris[0])
    fields = (case.metadata or {}).get("memory_fields")
    if isinstance(fields, dict):
        uri = fields.get("uri")
        if uri:
            return str(uri)
    return ""


def _case_uri_by_name(
    cases: list[Case],
    operations: ResolvedOperations,
    result: Any,
) -> dict[str, str]:
    candidates = set(
        (getattr(result, "written_uris", []) or []) + (getattr(result, "edited_uris", []) or [])
    )
    mapping: dict[str, str] = {}
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != _CASES_MEMORY_TYPE:
            continue
        fields = dict(getattr(op, "memory_fields", {}) or {})
        name = str(fields.get("case_name") or fields.get("name") or "").strip()
        if not name:
            continue
        for uri in getattr(op, "uris", []) or []:
            if not candidates or uri in candidates:
                mapping[name] = uri
                break
    for case in cases:
        if case.name not in mapping:
            uri = _case_uri_for_case(case, {})
            if uri:
                mapping[case.name] = uri
    return mapping


def _plan_item_has_source_session(item: PolicyPlanItem, session_uris: set[str]) -> bool:
    return bool(_plan_item_source_session_uris(item, session_uris))


def _plan_item_source_session_uris(
    item: PolicyPlanItem,
    session_uris: set[str],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for source in item.metadata.get("source_sessions", []):
        if not isinstance(source, dict):
            continue
        uri = str(source.get("source_session_uri") or "")
        if uri in session_uris and uri not in seen:
            seen.add(uri)
            result.append(uri)
    for link in getattr(item, "links", []) or []:
        try:
            stored = link if isinstance(link, StoredLink) else StoredLink(**dict(link))
        except Exception:
            continue
        if stored.link_type == "derived_from" and stored.to_uri in session_uris:
            uri = str(stored.to_uri)
            if uri not in seen:
                seen.add(uri)
                result.append(uri)
    return result


def _experience_session_map(
    *,
    plan: PolicyUpdatePlan,
    apply_result: PolicyApplyResult,
    session_uris: set[str],
) -> dict[str, list[str]]:
    root_uri = getattr(getattr(apply_result, "updated_policy_set", None), "root_uri", "")
    if not root_uri:
        return {}
    written_uris = set(getattr(apply_result, "written_uris", []) or [])
    result: dict[str, list[str]] = {}
    for item in getattr(plan, "items", []) or []:
        if item.memory_type != "experiences" or item.kind != "upsert":
            continue
        experience_uri = _experience_plan_item_uri(item, root_uri)
        if not experience_uri or experience_uri not in written_uris:
            continue
        source_uris = _plan_item_source_session_uris(item, session_uris)
        if not source_uris:
            continue
        existing = result.setdefault(experience_uri, [])
        existing.extend(uri for uri in source_uris if uri not in existing)
    return result


def _visible_experience_snapshot_uris(
    *,
    plan: PolicyUpdatePlan,
    apply_result: PolicyApplyResult,
) -> list[str]:
    """Return successfully applied Experience paths whose visible content changed."""
    root_uri = getattr(getattr(apply_result, "updated_policy_set", None), "root_uri", "")
    written_uris = set(getattr(apply_result, "written_uris", []) or [])
    deleted_uris = set(getattr(apply_result, "deleted_uris", []) or [])
    result: list[str] = []
    seen: set[str] = set()

    for item in getattr(plan, "items", []) or []:
        if item.memory_type != _EXPERIENCES_MEMORY_TYPE:
            continue
        if item.before_content == item.after_content:
            continue

        uri = _experience_plan_item_uri(item, root_uri)
        if not uri or uri in seen:
            continue
        if item.kind == "upsert":
            applied = uri in written_uris
        elif item.kind == "delete":
            applied = uri in deleted_uris
        else:
            applied = False
        if not applied:
            continue

        seen.add(uri)
        result.append(uri)

    return result


def _experience_snapshot_provenance(
    training_result: Any,
    *,
    fallback_session_uris: Optional[set[str]] = None,
) -> tuple[PolicyApplyResult, dict[str, list[str]]]:
    """Return provenance for the complete update that reached storage.

    Concurrent submitters can share one streaming trainer flush. Their
    top-level result is scoped to one submitter, while ``batch_result`` owns
    the complete plan and apply result that were written atomically. Snapshot
    history must describe that complete persisted update, regardless of which
    waiter reaches the snapshot commit first.
    """
    persisted_result = getattr(training_result, "batch_result", None) or training_result
    apply_result = persisted_result.apply_result
    session_uris = {
        str(analysis.metadata["source_session_uri"])
        for analysis in getattr(persisted_result, "analyses", []) or []
        if analysis.metadata.get("source_session_uri")
    }
    if not session_uris:
        session_uris = set(fallback_session_uris or set())
    return apply_result, _experience_session_map(
        plan=persisted_result.plan,
        apply_result=apply_result,
        session_uris=session_uris,
    )


def _gradient_memory_type(gradient: Any) -> str:
    """Extract memory_type from a semantic gradient."""
    after_file = getattr(gradient, "after_file", None)
    if after_file is not None:
        mt = getattr(after_file, "memory_type", None)
        if mt:
            return str(mt)
    metadata = dict(getattr(gradient, "metadata", {}) or {})
    if metadata.get("memory_type"):
        return str(metadata["memory_type"])
    before_file = getattr(gradient, "before_file", None)
    if before_file is not None:
        mt = getattr(before_file, "memory_type", None)
        if mt:
            return str(mt)
    return "experiences"


def _applied_memory_result(value: Any) -> Any:
    if isinstance(value, _V3AppliedMemory):
        return value.result
    result = getattr(value, "result", None)
    return result if result is not None else value


def _applied_memory_diff(value: Any) -> dict[str, Any] | None:
    if isinstance(value, _V3AppliedMemory):
        return value.memory_diff
    memory_diff = getattr(value, "memory_diff", None)
    return memory_diff if isinstance(memory_diff, dict) else None


def _same_memory_file(before: Optional[MemoryFile], after: Optional[MemoryFile]) -> bool:
    """Return whether two parsed memory files represent the same stored memory."""
    if before is None or after is None:
        return False
    # memory_type is commonly known from the operation/URI even when the raw
    # memory file does not serialize it, so do not treat that metadata-only
    # representation difference as a real file update.
    return before.model_dump(exclude={"uri", "memory_type"}) == after.model_dump(
        exclude={"uri", "memory_type"}
    )


def _serialize_skipped_operations(items: Any) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in list(items or []):
        if isinstance(item, dict):
            payload = dict(item)
        else:
            model_dump = getattr(item, "model_dump", None)
            if not callable(model_dump):
                continue
            payload = model_dump(mode="json", exclude_none=True)
        if isinstance(payload, dict):
            payload.pop("source", None)
            serialized.append(payload)
    return serialized


def _v3_extraction_response(
    *,
    contexts: list[Context],
    train_result: Any,
    archive_uri: str,
) -> list[Context] | dict[str, Any]:
    """Build the extraction response.

    Historically ``extract_long_term_memories`` returned ``list[Context]`` and
    a number of direct callers still index/compare the return value as a list.
    Commit orchestration also understands a structured response for session
    skills and deferred Trajectories. Preserve the old list shape unless one
    of those server-owned outputs exists.
    """
    skill_dicts: list[dict[str, Any]] = []
    trajectory_uris: list[str] = []
    seen: set[str] = set()
    if isinstance(train_result, dict):
        for uri in train_result.get("skill_uris", []) or []:
            uri_str = str(uri or "")
            if uri_str and uri_str not in seen:
                seen.add(uri_str)
                skill_dicts.append({"uri": uri_str, "archive_uri": archive_uri})
        trajectory_uris = list(
            dict.fromkeys(
                str(uri) for uri in train_result.get("trajectory_uris", []) or [] if str(uri)
            )
        )
    if not skill_dicts and not trajectory_uris:
        return contexts
    return {
        "contexts": contexts,
        "session_skills": skill_dicts,
        "trajectory_uris": trajectory_uris,
    }


def _make_memory_diff(
    *,
    archive_uri: str,
    adds: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    deletes: list[dict[str, Any]],
    skipped_operations: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    skipped = list(skipped_operations or [])
    return {
        "archive_uri": archive_uri,
        "trace_id": tracer.get_trace_id() or None,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
        "operations": {
            "adds": list(adds),
            "updates": list(updates),
            "deletes": list(deletes),
        },
        "skipped_operations": skipped,
        "summary": {
            "total_adds": len(adds),
            "total_updates": len(updates),
            "total_deletes": len(deletes),
            "total_skipped": len(skipped),
        },
    }


def _merge_memory_diffs(
    diffs: list[dict[str, Any]],
    *,
    archive_uri: str,
) -> dict[str, Any]:
    adds: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    deletes: list[dict[str, Any]] = []
    skipped_operations: list[dict[str, Any]] = []
    trace_id = tracer.get_trace_id() or None
    for diff in diffs:
        if not isinstance(diff, dict):
            continue
        if trace_id is None and diff.get("trace_id"):
            trace_id = str(diff.get("trace_id"))
        skipped_operations.extend(
            item for item in diff.get("skipped_operations", []) if isinstance(item, dict)
        )
        operations = diff.get("operations")
        if not isinstance(operations, dict):
            continue
        adds.extend([item for item in operations.get("adds", []) if isinstance(item, dict)])
        updates.extend([item for item in operations.get("updates", []) if isinstance(item, dict)])
        deletes.extend([item for item in operations.get("deletes", []) if isinstance(item, dict)])
    merged = _make_memory_diff(
        archive_uri=archive_uri,
        adds=adds,
        updates=updates,
        deletes=deletes,
        skipped_operations=skipped_operations,
    )
    merged["trace_id"] = trace_id
    return merged


async def _read_plain_memory_content(
    viking_fs: Any,
    *,
    uri: str,
    ctx: RequestContext,
    fallback: str,
) -> str:
    try:
        raw = await viking_fs.read_file(uri, ctx=ctx)
        return MemoryFileUtils.read(raw, uri=uri).content
    except Exception:
        return fallback


def _experience_plan_item_uri(item: PolicyPlanItem, root_uri: str) -> str:
    if item.target_uri:
        return item.target_uri
    name = item.target_name or "new_experience"
    return f"{root_uri.rstrip('/')}/{_safe_experience_filename(name)}.md"


_EXPERIENCE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_experience_filename(name: str) -> str:
    filename = _EXPERIENCE_NAME_RE.sub("_", name.strip()).strip("._-")
    return filename or "new_experience"


def _commit_policy_snapshot_id(*, session_id: Optional[str], archive_uri: str) -> str:
    if archive_uri:
        return f"session-commit:{archive_uri.rstrip('/').rsplit('/', 1)[-1]}"
    if session_id:
        return f"session-commit:{session_id}"
    return f"session-commit:{uuid4().hex}"
