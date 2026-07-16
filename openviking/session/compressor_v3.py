# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session Compressor V3.

V3 keeps the V2 extraction interface while changing user-memory commits to a
patch-merge flow without directory-level memory locks.  Training cases are not
extracted by a separate LLM call; they are a normal user-memory ``memory_type``
(``cases``) emitted by the same ExtractLoop that extracts profile/preferences/
events/etc.  When such case memories are produced, the same commit rollout is
submitted to the process-global StreamingPolicyTrainer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import uuid4

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater, StreamingMemoryUpdaterConfig
from openviking.session.memory.dataclass import (
    MemoryFile,
    MemoryOperationSource,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import create_default_registry
from openviking.session.memory.memory_updater import ExtractContext, write_stored_links
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.streaming_memory_updater import (
    MemoryUpdateRequest,
    get_streaming_memory_updater,
    make_streaming_memory_updater_key,
    merge_link_lists,
)
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.uri import generate_uri
from openviking.session.train import (
    Case,
    ExperienceGradientContext,
    ExperienceGradientEstimator,
    ExperienceSetLoader,
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
    SkillPolicyUpdater,
    SkillSetLoader,
    StreamingPolicyTrainerConfig,
    TrajectoryAnalyzerContext,
    TrajectoryRolloutAnalyzer,
    get_streaming_policy_trainer,
    make_streaming_policy_trainer_key,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_CASES_MEMORY_TYPE = "cases"
_TRAINING_CASE_SPEC_PROTOCOL = "openviking.batch_train.case_spec.v1"
_TRAINING_CASE_SPEC_HEADER = "# OpenViking Batch Training CaseSpec v1"
_TRAINING_FAST_PATH_MEMORY_TYPES = frozenset({"cases", "trajectories", "experiences"})
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


async def _commit_experience_snapshot(
    viking_fs: Any,
    *,
    ctx: RequestContext,
    experience_uris: list[str],
    archive_uri: str = "",
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
    archive_name = archive_uri.rstrip("/").rsplit("/", 1)[-1] if archive_uri else "unknown"
    try:
        await commit(
            message=f"Update experience memories from session commit {archive_name}",
            paths=paths,
            ctx=ctx,
        )
    except Exception as exc:
        logger.warning("Failed to commit experience snapshot for %s: %s", paths, exc, exc_info=True)


class SessionCompressorV3:
    """Session compressor with lock-free patch-merge user memory extraction."""

    rollout_analyzer: TrajectoryRolloutAnalyzer | Any
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
        rollout_analyzer: TrajectoryRolloutAnalyzer | Any | None = None,
        streaming_trainer_config: StreamingPolicyTrainerConfig | None = None,
        streaming_memory_updater_config: StreamingMemoryUpdaterConfig | None = None,
    ):
        self.vikingdb = vikingdb
        self.skill_processor = skill_processor
        self.rollout_analyzer = rollout_analyzer or TrajectoryRolloutAnalyzer(
            viking_fs=get_viking_fs(),
            vikingdb=vikingdb,
        )
        self.streaming_trainer_config = streaming_trainer_config or StreamingPolicyTrainerConfig()
        self.streaming_memory_updater_config = (
            streaming_memory_updater_config or StreamingMemoryUpdaterConfig()
        )

    def _get_or_create_react(
        self,
        ctx: Optional[RequestContext] = None,
        messages: Optional[List] = None,
        latest_archive_overview: str = "",
        isolation_handler: Optional[MemoryIsolationHandler] = None,
        transaction_handle=None,
    ) -> ExtractLoop:
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()
        context_provider = SessionExtractContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            isolation_handler=isolation_handler,
            ctx=ctx,
            viking_fs=viking_fs,
            transaction_handle=transaction_handle,
        )
        return ExtractLoop(
            vlm=vlm,
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
        )

    @tracer(ignore_result=True)
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
        allow_self_memory: bool = True,
        allowed_peer_ids: Optional[set[str]] = None,
    ):
        message_list = list(messages)
        fast_path_case = _training_case_from_first_message(message_list, allowed_memory_types)
        if fast_path_case is not None:
            return await self._commit_training_case_fast_path(
                case=fast_path_case,
                messages=message_list,
                ctx=ctx,
                session_id=session_id,
                archive_uri=archive_uri or "",
                strict_extract_errors=strict_extract_errors,
            )

        result = await self._extract_user_memories(
            messages=message_list,
            user=user,
            session_id=session_id,
            ctx=ctx,
            strict_extract_errors=strict_extract_errors,
            latest_archive_overview=latest_archive_overview,
            archive_uri=archive_uri,
            allowed_memory_types=allowed_memory_types,
            allow_self_memory=allow_self_memory,
            allowed_peer_ids=allowed_peer_ids,
        )
        train_result = await self.train_from_extracted_cases(
            cases=result.cases,
            messages=message_list,
            ctx=ctx,
            case_uri_by_name=getattr(result, "case_uri_by_name", {}),
            session_id=session_id,
            archive_uri=archive_uri or "",
            strict_extract_errors=strict_extract_errors,
            collect_memory_diff=True,
        )
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

    async def _commit_training_case_fast_path(
        self,
        *,
        case: Case,
        messages: list[Message],
        ctx: Optional[RequestContext],
        session_id: Optional[str],
        archive_uri: str,
        strict_extract_errors: bool,
    ) -> dict[str, Any]:
        if ctx is None:
            logger.warning("No RequestContext provided, skipping training case fast path")
            return {"contexts": [], "session_skills": []}
        case_write = await self._write_training_case_memory(
            case=case,
            ctx=ctx,
            archive_uri=archive_uri,
        )
        case_result = _applied_memory_result(case_write)
        contexts = _contexts_from_update_result(case_result)
        train_result = await self.train_from_extracted_cases(
            cases=[case],
            messages=_training_messages_after_case_spec(messages),
            ctx=ctx,
            case_uri_by_name={case.name: _first_context_uri(contexts)},
            session_id=session_id,
            archive_uri=archive_uri,
            strict_extract_errors=strict_extract_errors,
            collect_memory_diff=True,
        )
        await self._write_final_memory_diff(
            archive_uri=archive_uri,
            ctx=ctx,
            memory_diffs=[
                _applied_memory_diff(case_write),
                train_result.get("memory_diff"),
            ],
        )
        return _v3_extraction_response(
            contexts=contexts,
            train_result=train_result,
            archive_uri=archive_uri,
        )

    @tracer("train.compressor_v3.fast_path.write_case", ignore_result=True, ignore_args=True)
    async def _write_training_case_memory(
        self,
        *,
        case: Case,
        ctx: RequestContext,
        archive_uri: str,
    ) -> Any:
        viking_fs = get_viking_fs()
        registry = create_default_registry()
        schema = registry.get(_CASES_MEMORY_TYPE)
        if schema is None or not schema.enabled:
            raise RuntimeError("cases memory schema is not available")

        extract_context = ExtractContext([])
        fields = _case_to_memory_fields(case)
        uri = generate_uri(
            memory_type=schema,
            fields=fields,
            user_space=getattr(getattr(ctx, "user", None), "user_id", None) or "default",
            extract_context=extract_context,
        )
        old_file = None
        try:
            raw = await viking_fs.read_file(uri, ctx=ctx)
            if raw:
                old_file = MemoryFileUtils.read(raw, uri=uri)
        except Exception:
            old_file = None

        source = MemoryOperationSource(
            extraction_id=(archive_uri.rstrip("/").rsplit("/", 1)[-1] if archive_uri else ""),
            archive_uri=archive_uri or None,
            trace_id=tracer.get_trace_id() or None,
        )
        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    old_memory_file_content=old_file,
                    memory_fields=fields,
                    memory_type=_CASES_MEMORY_TYPE,
                    uris=[uri],
                    source=source,
                )
            ],
            delete_file_contents=[],
            errors=[],
        )
        updater = self._get_or_create_updater(registry, transaction_handle=None)
        result = await updater.apply_operations(
            operations,
            ctx,
            extract_context=extract_context,
            isolation_handler=MemoryIsolationHandler(
                ctx,
                extract_context,
                allowed_memory_types={_CASES_MEMORY_TYPE},
            ),
        )
        memory_diff = None
        if archive_uri:
            memory_diff = await self._build_memory_diff(
                result=result,
                operations=operations,
                viking_fs=viking_fs,
                ctx=ctx,
                archive_uri=archive_uri,
            )
        tracer.info(
            "Training CaseSpec fast path wrote case memory: "
            f"case={case.name} uri={uri} written={result.written_uris} edited={result.edited_uris}"
        )
        return _V3AppliedMemory(result=result, operations=operations, memory_diff=memory_diff)

    @tracer("train.compressor_v3.extract_user_memories", ignore_result=True, ignore_args=True)
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
        allowed_peer_ids: Optional[set[str]] = None,
    ) -> "_V3ExtractionResult":
        del user
        if not messages:
            return _V3ExtractionResult()
        if not ctx:
            logger.warning("No RequestContext provided, skipping v3 memory extraction")
            return _V3ExtractionResult()

        try:
            viking_fs = get_viking_fs()
        except Exception:
            logger.warning("VikingFS unavailable, skipping v3 memory extraction", exc_info=True)
            return _V3ExtractionResult()

        registry = create_default_registry()
        if allow_self_memory:
            await registry.initialize_memory_files(ctx)

        extract_context = ExtractContext(messages)
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types=allowed_memory_types,
            allow_self=allow_self_memory,
            allowed_peer_ids=allowed_peer_ids,
        )
        isolation_handler.prepare_messages()

        orchestrator = self._get_or_create_react(
            ctx=ctx,
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            isolation_handler=isolation_handler,
            transaction_handle=None,
        )
        operations, _tools_used = await orchestrator.run()
        if operations is None:
            tracer.info("[v3_patch_merge] No memory operations generated")
            return _V3ExtractionResult()

        extraction_id = uuid4().hex
        extracted_at = datetime.now(timezone.utc).isoformat()

        updater = await get_streaming_memory_updater(
            key=make_streaming_memory_updater_key(request_context=ctx),
            registry=registry,
            vikingdb=self.vikingdb,
            config=self.streaming_memory_updater_config,
        )
        update_result = await updater.submit(
            MemoryUpdateRequest(
                operations=operations,
                messages=list(messages),
                ctx=ctx,
                strict_extract_errors=strict_extract_errors,
                isolation_options={
                    "allowed_memory_types": allowed_memory_types,
                    "allow_self": allow_self_memory,
                    "allowed_peer_ids": allowed_peer_ids,
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
            result=result,
            viking_fs=viking_fs,
            ctx=ctx,
        )
        return _V3ExtractionResult(
            contexts=contexts,
            cases=canonical_cases,
            memory_diff=memory_diff,
            case_uri_by_name=_case_uri_by_name(canonical_cases, patch_operations, result),
        )

    @tracer("train.compressor_v3.train_from_extracted_cases", ignore_result=True, ignore_args=True)
    async def train_from_extracted_cases(
        self,
        *,
        cases: list[Case],
        messages: list[Message],
        ctx: Optional[RequestContext],
        case_uri_by_name: dict[str, str] | None = None,
        session_id: Optional[str] = None,
        archive_uri: str = "",
        strict_extract_errors: bool = False,
        collect_memory_diff: bool = False,
    ) -> dict[str, Any]:
        if not messages or ctx is None:
            return {"case_count": 0, "submitted": 0, "reason": "missing_messages_or_ctx"}
        if not cases:
            tracer.info("No commit training case memories extracted; skipping streaming train")
            return {"case_count": 0, "submitted": 0}

        config = get_openviking_config()
        skill_enabled = (
            config.memory.session_skill_extraction_enabled and self.skill_processor is not None
        )

        try:
            viking_fs = get_viking_fs()

            # --- Experience streaming trainer ---
            exp_root_uri = _experience_root_uri(ctx)
            exp_policy_set = await ExperienceSetLoader(viking_fs=viking_fs).load(
                exp_root_uri,
                ctx=ctx,
            )
            optimizer_context = PatchMergePolicyOptimizerContext(request_context=ctx)
            gradient_context = ExperienceGradientContext(
                request_context=ctx,
                messages=list(messages),
                strict_extract_errors=strict_extract_errors,
            )
            analysis_context = TrajectoryAnalyzerContext(
                request_context=ctx,
                strict_extract_errors=strict_extract_errors,
                include_session_skills=skill_enabled,
                source_archive_uri=archive_uri or "",
            )
            exp_trainer = await get_streaming_policy_trainer(
                key=make_streaming_policy_trainer_key(
                    policy_root_uri=exp_root_uri,
                    request_context=ctx,
                ),
                policy_set=exp_policy_set,
                rollout_analyzer=self.rollout_analyzer,
                gradient_estimator=ExperienceGradientEstimator(
                    viking_fs=viking_fs,
                ),
                policy_optimizer=PatchMergePolicyOptimizer(
                    viking_fs=viking_fs,
                    memory_type="experiences",
                ),
                policy_updater=MemoryFilePolicyUpdater(viking_fs=viking_fs, vikingdb=self.vikingdb),
                context=PipelineContext(
                    analysis_context=analysis_context,
                    gradient_context=gradient_context,
                    optimization_context=optimizer_context,
                    apply_context=ctx,
                ),
                config=self.streaming_trainer_config,
            )

            # --- Skill streaming trainer ---
            skill_trainer = None
            if skill_enabled:
                skill_root_uri = _skill_root_uri(ctx)
                skill_policy_set = await SkillSetLoader(viking_fs=viking_fs).load(
                    skill_root_uri,
                    ctx=ctx,
                )
                skill_trainer = await get_streaming_policy_trainer(
                    key=_skill_trainer_key(ctx),
                    policy_set=skill_policy_set,
                    rollout_analyzer=self.rollout_analyzer,
                    gradient_estimator=_NoopGradientEstimator(),
                    policy_optimizer=PatchMergePolicyOptimizer(
                        viking_fs=viking_fs,
                        memory_type="skills",
                    ),
                    policy_updater=SkillPolicyUpdater(
                        skill_processor=self.skill_processor,
                        viking_fs=viking_fs,
                        vikingdb=self.vikingdb,
                        memory_type="skills",
                    ),
                    context=PipelineContext(
                        analysis_context=analysis_context,
                        gradient_context=gradient_context,
                        optimization_context=optimizer_context,
                        apply_context=ctx,
                    ),
                    config=self.streaming_trainer_config,
                )

            submitted = 0
            skill_submitted = 0
            skill_uris: list[str] = []
            filtered_exp_gradient_count = 0
            memory_diffs: list[dict[str, Any]] = []
            policy_snapshot_id = _commit_policy_snapshot_id(
                session_id=session_id,
                archive_uri=archive_uri,
            )

            case_uri_map = dict(case_uri_by_name or {})

            for case in cases:
                case_uri = _case_uri_for_case(case, case_uri_map)
                rollout = Rollout(
                    case=case,
                    messages=list(messages),
                    policy_snapshot_id=policy_snapshot_id,
                )
                # Analyze once — trajectories + skill patches co-extracted
                analysis = await self.rollout_analyzer.analyze(rollout, analysis_context)

                # Experience path: estimate gradients, then submit to exp trainer
                exp_gradients = await ExperienceGradientEstimator(
                    viking_fs=viking_fs,
                ).estimate(analysis, exp_trainer.policy_set, gradient_context)
                exp_training_result = _trajectory_only_training_result(
                    analysis=analysis,
                    rollout=rollout,
                    policy_set=exp_trainer.policy_set,
                )
                if exp_gradients:
                    exp_training_result = await exp_trainer.submit_gradients(
                        exp_gradients,
                        analysis=analysis,
                        rollout=rollout,
                    )
                if case_uri:
                    await self._link_case_to_training_outputs(
                        analysis=analysis,
                        case_uri=case_uri,
                        plan=exp_training_result.plan,
                        apply_result=exp_training_result.apply_result,
                        ctx=ctx,
                        viking_fs=viking_fs,
                    )
                exp_apply_result = getattr(exp_training_result, "apply_result", None)
                if exp_apply_result is not None:
                    await _commit_experience_snapshot(
                        viking_fs,
                        ctx=ctx,
                        experience_uris=[
                            *list(getattr(exp_apply_result, "written_uris", []) or []),
                            *list(getattr(exp_apply_result, "deleted_uris", []) or []),
                        ],
                        archive_uri=archive_uri,
                    )
                # Skill path: co-extracted skill gradients go directly to skill trainer
                if skill_trainer is not None and analysis.gradients:
                    skill_gradients = [
                        g for g in analysis.gradients if _gradient_memory_type(g) == "skills"
                    ]
                    if skill_gradients:
                        skill_training_result = await skill_trainer.submit_gradients(
                            skill_gradients,
                            analysis=analysis,
                            rollout=rollout,
                        )
                        skill_submitted += 1
                        apply_result = getattr(skill_training_result, "apply_result", None)
                        if apply_result is not None:
                            for uri in getattr(apply_result, "written_uris", []) or []:
                                if uri:
                                    skill_uris.append(str(uri))

                submitted += 1

                if collect_memory_diff:
                    # Build diff from the strongly typed training result returned by
                    # submit_gradients.  Do not use exp_trainer.last_apply_result here:
                    # it is only a PolicyApplyResult and does not carry analyses/plan,
                    # so trajectory and experience diffs would be lost.
                    memory_diff = await self._build_training_memory_diff(
                        training_result=exp_training_result,
                        viking_fs=viking_fs,
                        ctx=ctx,
                        archive_uri=archive_uri,
                    )
                    if _memory_diff_has_changes(memory_diff):
                        memory_diffs.append(memory_diff)

            tracer.info(
                "Submitted commit case memories to streaming train: "
                f"case_count={len(cases)} submitted={submitted} "
                f"skill_submitted={skill_submitted}",
                console=self.streaming_trainer_config.trace_console,
            )
            response: dict[str, Any] = {
                "case_count": len(cases),
                "submitted": submitted,
                "skill_submitted": skill_submitted,
                "skill_uris": skill_uris,
                "filtered_exp_gradient_count": filtered_exp_gradient_count,
            }
            if collect_memory_diff:
                response["memory_diff"] = _merge_memory_diffs(
                    memory_diffs,
                    archive_uri=archive_uri,
                )
            return response
        except Exception as exc:
            logger.warning("Commit streaming train failed: %s", exc, exc_info=True)
            if strict_extract_errors:
                raise
            return {"case_count": len(cases), "submitted": 0, "error": str(exc)}

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

        seen_trajectory_uris: set[str] = set()
        for analysis in training_result.analyses:
            for trajectory in analysis.trajectories:
                uri = trajectory.uri
                if not uri or uri in seen_trajectory_uris:
                    continue
                seen_trajectory_uris.add(uri)
                adds.append(
                    {
                        "uri": uri,
                        "memory_type": "trajectories",
                        "after": trajectory.content,
                    }
                )

        applied_uris = set(training_result.apply_result.written_uris)
        deleted_uris = set(training_result.apply_result.deleted_uris)
        root_uri = training_result.apply_result.updated_policy_set.root_uri
        if not root_uri:
            raise ValueError(
                "PolicyApplyResult.updated_policy_set.root_uri is required for training memory diff"
            )
        source_trajectory_uris = set(seen_trajectory_uris)

        for item in training_result.plan.items:
            if item.memory_type != "experiences":
                continue
            if source_trajectory_uris and not _plan_item_has_source_trajectory(
                item,
                source_trajectory_uris,
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
        )

    async def _link_case_to_training_outputs(
        self,
        *,
        analysis: RolloutAnalysis,
        case_uri: str,
        plan: PolicyUpdatePlan,
        apply_result: PolicyApplyResult,
        ctx: RequestContext,
        viking_fs: Any,
    ) -> None:
        links = _case_training_links(
            analysis=analysis,
            case_uri=case_uri,
            plan=plan,
            apply_result=apply_result,
        )
        if not links:
            return
        await _render_case_links_from_template(
            case_uri=case_uri,
            links=links,
            ctx=ctx,
            viking_fs=viking_fs,
        )
        await write_stored_links(links, ctx, viking_fs, skip_uris={case_uri})

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


def _training_case_from_first_message(
    messages: list[Message],
    allowed_memory_types: Optional[set[str]],
) -> Case | None:
    """Parse a batch-training CaseSpec control message from message[0].

    The fast path is deliberately gated by the commit memory policy so normal
    user sessions never bypass user-memory extraction.  Once the protocol
    header is present, malformed payloads are treated as errors instead of
    silently falling back to LLM extraction.
    """
    if not messages or allowed_memory_types is None:
        return None
    if not set(allowed_memory_types).issubset(_TRAINING_FAST_PATH_MEMORY_TYPES):
        return None

    payload = _training_case_spec_payload_from_message(messages[0])
    if payload is None:
        return None
    return _case_from_payload(payload)


def _training_case_spec_payload_from_message(message: Message) -> dict[str, Any] | None:
    text = _message_text(message).strip()
    if not text.startswith(_TRAINING_CASE_SPEC_HEADER):
        return None
    return _parse_training_case_spec_payload(text)


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


def _training_messages_after_case_spec(messages: list[Message]) -> list[Message]:
    """Return commit messages after CaseSpec."""
    return list(messages[1:])


def _parse_training_case_spec_payload(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    raw_payload = (
        match.group(1).strip() if match else text.removeprefix(_TRAINING_CASE_SPEC_HEADER).strip()
    )
    if not raw_payload:
        raise ValueError("Training CaseSpec fast path payload is empty")
    try:
        payload = JsonUtils.loads(raw_payload)
    except Exception as exc:
        raise ValueError("Training CaseSpec fast path payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Training CaseSpec fast path payload must be a JSON object")
    protocol = str(payload.get("protocol") or "")
    if protocol != _TRAINING_CASE_SPEC_PROTOCOL:
        raise ValueError(
            "Training CaseSpec fast path protocol mismatch: "
            f"expected {_TRAINING_CASE_SPEC_PROTOCOL!r}, got {protocol!r}"
        )
    if not isinstance(payload.get("case"), dict):
        raise ValueError("Training CaseSpec fast path payload must contain a case object")
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
    metadata = raw_case.get("metadata") if isinstance(raw_case.get("metadata"), dict) else {}
    return Case(
        name=name,
        task_signature=task_signature,
        input=dict(case_input),
        rubric=rubric,
        metadata={
            "source": "batch_training_case_spec",
            **dict(metadata),
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


def _case_to_memory_fields(case: Case) -> dict[str, Any]:
    return {
        "case_name": case.name,
        "task_signature": case.task_signature,
        "input": json.dumps(case.input or {}, ensure_ascii=False, sort_keys=True),
        "rubric": json.dumps(
            _rubric_to_payload(case.rubric),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "evidence": _case_evidence(case),
    }


def _rubric_to_payload(rubric: Rubric) -> dict[str, Any]:
    return {
        "name": rubric.name,
        "description": rubric.description,
        "criteria": [
            {
                "name": criterion.name,
                "description": criterion.description,
                "required": criterion.required,
                "weight": criterion.weight,
            }
            for criterion in rubric.criteria
        ],
    }


def _case_evidence(case: Case) -> str:
    raw_evidence = (case.metadata or {}).get("evidence")
    if raw_evidence:
        return str(raw_evidence)
    return "Structured batch training CaseSpec supplied by the training pipeline."



async def _canonical_cases_from_update_result(
    *,
    operations: ResolvedOperations,
    result: Any,
    viking_fs: Any,
    ctx: RequestContext,
) -> list[Case]:
    """Build training cases from canonical case files after patch merge/apply.

    The extractor can emit multiple case proposals, but ``StreamingMemoryUpdater``
    may merge, rename, or deduplicate them before storage.  Training must follow
    the actual persisted case-first state, so only cases whose canonical URI was
    written/edited by this update are returned.
    """

    touched_uris = _case_result_touched_uris(result)
    if not touched_uris:
        return []

    case_ops_by_uri: dict[str, ResolvedOperation] = {}
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != _CASES_MEMORY_TYPE:
            continue
        for uri in getattr(op, "uris", []) or []:
            if uri in touched_uris and uri not in case_ops_by_uri:
                case_ops_by_uri[uri] = op

    cases: list[Case] = []
    for uri in touched_uris:
        if uri not in case_ops_by_uri:
            continue
        case = await _case_from_persisted_memory_file(uri=uri, viking_fs=viking_fs, ctx=ctx)
        if case is None:
            case = _operation_to_case(
                case_ops_by_uri[uri].model_copy(update={"uris": [uri]}, deep=True)
            )
        if case is not None:
            cases.append(case)
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
        tracer.info(f"Failed to read canonical case memory for training {uri}: {exc}")
        return None
    try:
        memory_file = MemoryFileUtils.read(raw or "", uri=uri)
    except Exception as exc:
        tracer.info(f"Failed to parse canonical case memory for training {uri}: {exc}")
        return None
    return _memory_file_to_case(memory_file)


def _memory_file_to_case(memory_file: MemoryFile) -> Case | None:
    fields = dict(getattr(memory_file, "extra_fields", {}) or {})
    uri = str(getattr(memory_file, "uri", "") or "")
    name = str(fields.get("case_name") or fields.get("name") or _basename_case_name(uri)).strip()
    task_signature = str(fields.get("task_signature") or name).strip()
    if not name or not task_signature:
        return None
    memory_fields = dict(fields)
    if uri:
        memory_fields.setdefault("uri", uri)
    return Case(
        name=name,
        task_signature=task_signature,
        input=_parse_case_input(fields.get("input")),
        rubric=_parse_rubric(fields.get("rubric"), fallback_name=f"{name}_rubric"),
        metadata={
            "source": "session_commit_case_memory",
            "case_uris": [uri] if uri else [],
            "evidence": str(fields.get("evidence") or ""),
            "memory_fields": memory_fields,
        },
    )


def _operation_to_case(op: ResolvedOperation) -> Case | None:
    fields = dict(getattr(op, "memory_fields", {}) or {})
    name = str(fields.get("case_name") or fields.get("name") or _fallback_case_name(op)).strip()
    task_signature = str(fields.get("task_signature") or name).strip()
    if not name or not task_signature:
        return None
    return Case(
        name=name,
        task_signature=task_signature,
        input=_parse_case_input(fields.get("input")),
        rubric=_parse_rubric(fields.get("rubric"), fallback_name=f"{name}_rubric"),
        metadata={
            "source": "session_commit_case_memory",
            "case_uris": list(getattr(op, "uris", []) or []),
            "evidence": str(fields.get("evidence") or ""),
            "memory_fields": fields,
        },
    )


def _parse_case_input(raw_input: Any) -> dict[str, Any]:
    parsed = _parse_jsonish(raw_input)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"items": parsed}
    return {"summary": str(raw_input or "")}


def _parse_rubric(raw_rubric: Any, *, fallback_name: str) -> Rubric:
    parsed = _parse_jsonish(raw_rubric)
    raw = parsed if isinstance(parsed, dict) else {}
    raw_criteria = raw.get("criteria") if isinstance(raw.get("criteria"), list) else []
    criteria: list[RubricCriterion] = []
    for index, item in enumerate(raw_criteria):
        if not isinstance(item, dict):
            continue
        criteria.append(
            RubricCriterion(
                name=str(item.get("name") or f"criterion_{index + 1}"),
                description=str(item.get("description") or "The rollout satisfies the task."),
                required=bool(item.get("required", True)),
                weight=_safe_weight(item.get("weight"), default=1.0),
            )
        )
    if not criteria:
        criteria = [
            RubricCriterion(
                name="task_completed",
                description="The assistant completed the user's task with verifiable outcome.",
                required=True,
                weight=1.0,
            )
        ]
    return Rubric(
        name=str(raw.get("name") or fallback_name),
        description=str(raw.get("description") or "Defines what good means for this commit case."),
        criteria=criteria,
    )


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return JsonUtils.loads(value)
    except Exception:
        return None


def _safe_weight(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _first_uri(uris: list[str] | None) -> str | None:
    return uris[0] if uris else None


def _fallback_case_name(op: ResolvedOperation) -> str:
    uri = _first_uri(getattr(op, "uris", []) or [])
    name = _basename_case_name(uri)
    if name:
        return name
    return "commit_case"


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

    Used for the skill trainer because skill gradients are co-extracted
    during trajectory analysis and submitted directly via
    ``submit_gradients``; the estimator is never called in practice but
    ``StreamingPolicyTrainer`` requires one.
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


def _first_context_uri(contexts: list[Context]) -> str:
    for context in contexts or []:
        uri = getattr(context, "uri", "")
        if uri:
            return str(uri)
    return ""


def _case_training_links(
    *,
    analysis: RolloutAnalysis,
    case_uri: str,
    plan: PolicyUpdatePlan,
    apply_result: PolicyApplyResult,
) -> list[StoredLink]:
    trajectory_links = _case_trajectory_links(analysis=analysis, case_uri=case_uri)
    trajectory_uris = {link.to_uri for link in trajectory_links if link.to_uri}
    experience_links = _case_experience_links_via_trajectories(
        case_uri=case_uri,
        trajectory_uris=trajectory_uris,
        plan=plan,
        apply_result=apply_result,
    )
    return merge_link_lists([*trajectory_links, *experience_links])


def _case_trajectory_links(
    *,
    analysis: RolloutAnalysis,
    case_uri: str,
) -> list[StoredLink]:
    links: list[StoredLink] = []
    for trajectory in getattr(analysis, "trajectories", []) or []:
        uri = str(getattr(trajectory, "uri", "") or "")
        if not uri or "/memories/trajectories/" not in uri:
            continue
        links.append(
            _stored_link(
                from_uri=case_uri,
                target_uri=uri,
                link_type="related_to",
                description="",
            )
        )
    return links


def _case_experience_links_via_trajectories(
    *,
    case_uri: str,
    trajectory_uris: set[str],
    plan: PolicyUpdatePlan,
    apply_result: PolicyApplyResult,
) -> list[StoredLink]:
    if not trajectory_uris:
        return []
    touched = set(getattr(apply_result, "written_uris", []) or [])
    touched.update(getattr(apply_result, "edited_uris", []) or [])
    result: list[StoredLink] = []
    seen: set[str] = set()
    root_uri = getattr(getattr(apply_result, "updated_policy_set", None), "root_uri", "")
    if not root_uri:
        raise ValueError(
            "PolicyApplyResult.updated_policy_set.root_uri is required for case-to-experience links"
        )
    for item in getattr(plan, "items", []) or []:
        if item.memory_type != "experiences" or item.kind != "upsert":
            continue
        if not _plan_item_has_source_trajectory(item, trajectory_uris):
            continue
        uri = _experience_plan_item_uri(item, root_uri)
        if touched and uri not in touched:
            continue
        if uri in seen:
            continue
        seen.add(uri)
        result.append(
            _stored_link(
                from_uri=case_uri,
                target_uri=uri,
                link_type="related_to",
                description="",
            )
        )
    return result


def _plan_item_has_source_trajectory(item: PolicyPlanItem, trajectory_uris: set[str]) -> bool:
    for link in getattr(item, "links", []) or []:
        try:
            stored = link if isinstance(link, StoredLink) else StoredLink(**dict(link))
        except Exception:
            continue
        if (
            stored.link_type == "derived_from"
            and stored.to_uri in trajectory_uris
            and "/memories/trajectories/" in str(stored.to_uri or "")
        ):
            return True
    return False


def _stored_link(
    *,
    from_uri: str,
    target_uri: str,
    link_type: str,
    description: str,
) -> StoredLink:
    return StoredLink(
        from_uri=from_uri,
        to_uri=target_uri,
        link_type=link_type,
        weight=1.0,
        match_text=None,
        description=description,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


async def _render_case_links_from_template(
    *,
    case_uri: str,
    links: list[StoredLink],
    ctx: RequestContext,
    viking_fs: Any,
) -> None:
    if not links:
        return
    try:
        raw = await viking_fs.read_file(case_uri, ctx=ctx)
    except Exception as exc:
        tracer.error(f"Failed to read case memory for link rendering {case_uri}: {exc}")
        return

    mf = MemoryFileUtils.read(raw or "", uri=case_uri)
    from openviking.session.memory.merge_op.link_merge import merge_links

    merged_links = merge_links(mf.links, [link.model_dump() for link in links])
    if merged_links != mf.links:
        mf.links = merged_links

    schema = create_default_registry().get(_CASES_MEMORY_TYPE)
    content_template = schema.content_template if schema is not None else None
    await viking_fs.write_file(
        case_uri,
        MemoryFileUtils.write(mf, content_template=content_template),
        ctx=ctx,
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


def _trajectory_only_training_result(
    *,
    analysis: RolloutAnalysis,
    rollout: Rollout,
    policy_set: Any,
) -> RolloutTrainingResult:
    """Return a typed no-op training result that still carries trajectories.

    Some rollouts produce useful trajectory memories but no experience gradients.
    The memory diff should still include those trajectory writes, so callers use
    this as the baseline result and replace it only when experience training
    returns a full RolloutTrainingResult.
    """

    return RolloutTrainingResult(
        analyses=[analysis],
        gradients=[],
        plan=PolicyUpdatePlan(items=[], metadata={"no_experience_gradients": True}),
        apply_result=PolicyApplyResult(
            updated_policy_set=policy_set,
            written_uris=[],
            errors=[],
            metadata={"no_experience_gradients": True},
        ),
        metadata={
            "source": "trajectory_only",
            "case_name": rollout.case.name,
            "trajectory_count": len(analysis.trajectories),
        },
    )


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


def _v3_extraction_response(
    *,
    contexts: list[Context],
    train_result: Any,
    archive_uri: str,
) -> list[Context] | dict[str, Any]:
    """Build the extraction response.

    Historically ``extract_long_term_memories`` returned ``list[Context]`` and
    a number of direct callers still index/compare the return value as a list.
    Commit orchestration now also understands the execution-memory style
    ``{"contexts": ..., "session_skills": ...}`` shape so it can count
    session skills.  Preserve the old list shape unless there are actual
    session skills to report.
    """
    skill_dicts: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(train_result, dict):
        for uri in train_result.get("skill_uris", []) or []:
            uri_str = str(uri or "")
            if uri_str and uri_str not in seen:
                seen.add(uri_str)
                skill_dicts.append({"uri": uri_str, "archive_uri": archive_uri})
    if not skill_dicts:
        return contexts
    return {"contexts": contexts, "session_skills": skill_dicts}


def _make_memory_diff(
    *,
    archive_uri: str,
    adds: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    deletes: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "archive_uri": archive_uri,
        "trace_id": tracer.get_trace_id() or None,
        "extracted_at": datetime.utcnow().isoformat() + "Z",
        "operations": {
            "adds": list(adds),
            "updates": list(updates),
            "deletes": list(deletes),
        },
        "summary": {
            "total_adds": len(adds),
            "total_updates": len(updates),
            "total_deletes": len(deletes),
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
    trace_id = tracer.get_trace_id() or None
    for diff in diffs:
        if not isinstance(diff, dict):
            continue
        if trace_id is None and diff.get("trace_id"):
            trace_id = str(diff.get("trace_id"))
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
    )
    merged["trace_id"] = trace_id
    return merged


def _memory_diff_has_changes(diff: Any) -> bool:
    if not isinstance(diff, dict):
        return False
    summary = diff.get("summary")
    if not isinstance(summary, dict):
        return False
    return any(
        int(summary.get(key) or 0) > 0 for key in ("total_adds", "total_updates", "total_deletes")
    )


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
