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
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openviking.core.context import Context
from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater, StreamingMemoryUpdaterConfig
from openviking.session.memory.dataclass import (
    MemoryOperationSource,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import create_default_registry
from openviking.session.memory.memory_updater import ExtractContext
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
from openviking.session.skill import (
    SkillOperationUpdater,
    dedup_session_skill_operations,
)
from openviking.session.skill.session_skill_context_provider import (
    SESSION_SKILL_MEMORY_TYPE,
    SessionSkillContextProvider,
)
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
    SkillSetLoader,
    SkillPolicyUpdater,
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
_TRAINING_ORACLE_SUMMARY_HEADER = "# OpenViking Training Oracle Summary v1"
_TRAINING_FAST_PATH_MEMORY_TYPES = frozenset({"cases", "trajectories", "experiences"})
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_EXPERIENCE_CONFLICT_TERMS = (
    "不要",
    "不得",
    "不能",
    "不应",
    "严禁",
    "禁止",
    "拒绝",
    "转人工",
    "transfer",
    "human agent",
    "refuse",
    "deny",
    "do not",
    "don't",
    "must not",
    "should not",
    "cannot",
)


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
            memory_type = op.memory_type if op else _get_memory_type_from_uri(uri)
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
            memory_type = op.memory_type if op else _get_memory_type_from_uri(uri)
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

        for item in adds + updates:
            try:
                content = await viking_fs.read_file(uri=item["uri"], ctx=ctx)
                item["after"] = MemoryFileUtils.read(content).content
            except Exception:
                pass

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
            contexts = await self._commit_training_case_fast_path(
                case=fast_path_case,
                messages=message_list,
                ctx=ctx,
                session_id=session_id,
                archive_uri=archive_uri or "",
                strict_extract_errors=strict_extract_errors,
            )
            return contexts

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
                _dict_value(train_result, "memory_diff"),
            ],
        )
        return result.contexts

    async def _commit_training_case_fast_path(
        self,
        *,
        case: Case,
        messages: list[Message],
        ctx: Optional[RequestContext],
        session_id: Optional[str],
        archive_uri: str,
        strict_extract_errors: bool,
    ) -> list[Context]:
        if ctx is None:
            logger.warning("No RequestContext provided, skipping training case fast path")
            return []
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
                _dict_value(train_result, "memory_diff"),
            ],
        )
        return contexts

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

    @tracer(
        "train.compressor_v3.extract_user_memories", ignore_result=True, ignore_args=True
    )
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
        extracted_cases = _operations_to_cases(operations)

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
        return _V3ExtractionResult(
            contexts=contexts,
            cases=extracted_cases,
            memory_diff=memory_diff,
        )

    @tracer("train.compressor_v3.train_from_extracted_cases", ignore_result=True, ignore_args=True)
    async def train_from_extracted_cases(
        self,
        *,
        cases: list[Case],
        messages: list[Message],
        ctx: Optional[RequestContext],
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
            config.memory.session_skill_extraction_enabled
            and self.skill_processor is not None
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
                policy_updater=MemoryFilePolicyUpdater(
                    viking_fs=viking_fs, vikingdb=self.vikingdb
                ),
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
            filtered_exp_gradient_count = 0
            memory_diffs: list[dict[str, Any]] = []
            policy_snapshot_id = _commit_policy_snapshot_id(
                session_id=session_id,
                archive_uri=archive_uri,
            )

            for case in cases:
                rollout = Rollout(
                    case=case,
                    messages=list(messages),
                    policy_snapshot_id=policy_snapshot_id,
                )
                # Analyze once — trajectories + skill patches co-extracted
                analysis = await self.rollout_analyzer.analyze(
                    rollout, analysis_context
                )

                # Experience path: estimate gradients, then submit to exp trainer
                exp_gradients = await _estimate_exp_gradients(
                    analysis=analysis,
                    policy_set=exp_trainer.policy_set,
                    context=gradient_context,
                    viking_fs=viking_fs,
                )
                filtered_exp_gradients = _filter_oracle_conflicting_experience_gradients(
                    gradients=exp_gradients,
                    messages=messages,
                )
                filtered_exp_gradient_count += len(exp_gradients) - len(filtered_exp_gradients)
                exp_gradients = filtered_exp_gradients
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

                # Skill path: co-extracted skill gradients go directly to skill trainer
                if skill_trainer is not None and analysis.gradients:
                    skill_gradients = [
                        g for g in analysis.gradients
                        if _gradient_memory_type(g) == "skills"
                    ]
                    if skill_gradients:
                        await skill_trainer.submit_gradients(
                            skill_gradients,
                            analysis=analysis,
                            rollout=rollout,
                        )
                        skill_submitted += 1

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
        root_uri = (
            training_result.apply_result.updated_policy_set.root_uri
            or _experience_root_uri(ctx)
        )

        for item in training_result.plan.items:
            if item.memory_type != "experiences":
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
        if not _memory_diff_has_changes(merged):
            return
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
    """Return commit messages after CaseSpec, ensuring an oracle summary exists."""
    trailing = list(messages[1:])
    if trailing and _message_text(trailing[0]).strip().startswith(_TRAINING_ORACLE_SUMMARY_HEADER):
        return trailing
    payload = _training_case_spec_payload_from_message(messages[0]) if messages else None
    if payload is None:
        return trailing
    return [_oracle_summary_message_from_case_payload(payload)] + trailing


def _oracle_summary_message_from_case_payload(payload: dict[str, Any]) -> Message:
    raw_case = payload.get("case") if isinstance(payload.get("case"), dict) else {}
    raw_input = raw_case.get("input") if isinstance(raw_case.get("input"), dict) else {}
    oracle = _parse_ground_truth_oracle(str(raw_input.get("ground_truth") or ""))
    expected_names = [action["name"] for action in oracle["actions"] if action.get("name")]
    expected_write_names = [
        name for name in expected_names if _is_state_changing_action_name(name)
    ]
    summary = {
        "protocol": "openviking.batch_train.oracle_summary.v1",
        "case": {
            "name": str(raw_case.get("name") or ""),
            "task_signature": str(raw_case.get("task_signature") or ""),
        },
        "expected": {
            "actions": oracle["actions"],
            "action_names": expected_names,
            "state_changing_action_names": expected_write_names,
            "communicate_info": oracle["communicate_info"],
            "nl_assertions": oracle["nl_assertions"],
        },
        "training_guidance": [
            "Ground-truth expected actions are the oracle for this training example.",
            "Do not learn an experience that forbids, refuses, transfers instead of, or finishes before a required state-changing action.",
        ],
    }
    text = (
        f"{_TRAINING_ORACLE_SUMMARY_HEADER}\n\n"
        "Deterministic training-only summary derived from CaseSpec. "
        "Preserve required actions and communication when extracting memories.\n\n"
        f"```json\n{json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)}\n```"
    )
    return Message(
        id="openviking-training-oracle-summary",
        role="user",
        parts=[TextPart(text=text)],
    )


def _parse_training_case_spec_payload(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    raw_payload = (
        match.group(1).strip()
        if match
        else text.removeprefix(_TRAINING_CASE_SPEC_HEADER).strip()
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
            raw_rubric.get("description")
            or "Defines what good means for this batch training case."
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
        "rubric": json.dumps(_rubric_to_payload(case.rubric), ensure_ascii=False, sort_keys=True),
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


def _operations_to_cases(operations: ResolvedOperations) -> list[Case]:
    cases: list[Case] = []
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != _CASES_MEMORY_TYPE:
            continue
        case = _operation_to_case(op)
        if case is not None:
            cases.append(case)
    return cases


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
    if uri:
        return uri.rstrip("/").split("/")[-1].removesuffix(".md")
    return "commit_case"


def _get_memory_type_from_uri(uri: str) -> str:
    parts = uri.split("/")
    for part in parts:
        if part.endswith(".md"):
            return part.removesuffix(".md")
    return "unknown"


def _experience_root_uri(ctx: RequestContext) -> str:
    user_space = getattr(getattr(ctx, "user", None), "user_id", None) or "default"
    return f"viking://user/{user_space}/memories/experiences"


def _skill_root_uri(ctx: RequestContext) -> str:
    user_space = getattr(getattr(ctx, "user", None), "user_id", None) or "default"
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


async def _estimate_exp_gradients(
    *,
    analysis: RolloutAnalysis,
    policy_set: Any,
    context: ExperienceGradientContext,
    viking_fs: Any = None,
) -> list[Any]:
    """Estimate experience gradients from a rollout analysis.

    Thin wrapper around ExperienceGradientEstimator that reuses the
    trajectory content from the analysis instead of running a full
    second extraction pass.
    """
    estimator = ExperienceGradientEstimator(viking_fs=viking_fs)
    return await estimator.estimate(analysis, policy_set, context)


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

def _dict_value(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data.get(key)
    return None


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
        int(summary.get(key) or 0) > 0
        for key in ("total_adds", "total_updates", "total_deletes")
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


def _trajectory_content_from_rollout(rollout: Rollout) -> str:
    conversation = "\n".join(
        f"- {message.role}: {message.content}" for message in rollout.messages if message.content
    )
    return "\n".join(
        [
            f"# {rollout.case.name}",
            f"- Task Signature: {rollout.case.task_signature}",
            "- Commit Case: extracted as a case memory from a real session commit.",
            "- Rubric:",
            *[
                f"  - {criterion.name}: {criterion.description}"
                for criterion in rollout.case.rubric.criteria
            ],
            "- Conversation Evidence:",
            conversation,
        ]
    )


def _filter_oracle_conflicting_experience_gradients(
    *,
    gradients: list[Any],
    messages: list[Message],
) -> list[Any]:
    """Drop experience gradients that conflict with CaseSpec-required writes.

    The guard is intentionally generic: it reads the training oracle summary or
    CaseSpec, finds required state-changing tool names, and blocks broad
    refusal/transfer/skip guidance that mentions those tools or the current
    task family.  This prevents one failed rollout from teaching the agent to
    avoid actions that the evaluator explicitly requires.
    """
    required_writes = set(_required_write_action_names_from_messages(messages))
    if not required_writes:
        return list(gradients)
    kept: list[Any] = []
    for gradient in gradients:
        content = _gradient_after_content(gradient)
        if _experience_content_conflicts_with_required_writes(content, required_writes):
            metadata = dict(getattr(gradient, "metadata", {}) or {})
            metadata["oracle_conflict_filtered"] = True
            try:
                gradient.metadata = metadata
            except Exception:
                pass
            logger.info(
                "Filtered oracle-conflicting experience gradient target=%s required_writes=%s",
                getattr(gradient, "target_name", "<unknown>"),
                sorted(required_writes),
            )
            continue
        kept.append(gradient)
    return kept


def _required_write_action_names_from_messages(messages: list[Message]) -> list[str]:
    for message in messages:
        text = _message_text(message).strip()
        if not text.startswith(_TRAINING_ORACLE_SUMMARY_HEADER):
            continue
        payload = _json_payload_from_fenced_text(text)
        expected = payload.get("expected") if isinstance(payload, dict) else None
        if isinstance(expected, dict):
            names = expected.get("state_changing_action_names")
            if isinstance(names, list):
                return [str(name) for name in names if str(name).strip()]

    for message in messages:
        text = _message_text(message).strip()
        if not text.startswith(_TRAINING_CASE_SPEC_HEADER):
            continue
        payload = _parse_training_case_spec_payload(text)
        raw_case = payload.get("case") if isinstance(payload.get("case"), dict) else {}
        raw_input = raw_case.get("input") if isinstance(raw_case.get("input"), dict) else {}
        oracle = _parse_ground_truth_oracle(str(raw_input.get("ground_truth") or ""))
        return [
            action["name"]
            for action in oracle["actions"]
            if action.get("name") and _is_state_changing_action_name(action["name"])
        ]
    return []


def _json_payload_from_fenced_text(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    raw_payload = match.group(1).strip() if match else text
    try:
        value = JsonUtils.loads(raw_payload)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _gradient_after_content(gradient: Any) -> str:
    after_file = getattr(gradient, "after_file", None)
    return str(getattr(after_file, "content", "") or "")


def _experience_content_conflicts_with_required_writes(
    content: str,
    required_writes: set[str],
) -> bool:
    lowered = str(content or "").lower()
    if not lowered.strip():
        return False
    has_conflict_term = any(term in lowered for term in _EXPERIENCE_CONFLICT_TERMS)
    if not has_conflict_term:
        return False
    if "done" in lowered and any(term in lowered for term in ("before", "先", "提前")):
        has_conflict_term = True
    mentioned_required = any(name.lower() in lowered for name in required_writes)
    mentions_terminal_replacement = any(
        term in lowered
        for term in (
            "transfer_to_human_agents",
            "转人工",
            "human agent",
            "done",
            "拒绝",
            "refuse",
            "deny",
        )
    )
    return mentioned_required or mentions_terminal_replacement


def _parse_ground_truth_oracle(text: str) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    communicate_info: list[str] = []
    nl_assertions: list[str] = []
    current: dict[str, Any] | None = None
    mode: str | None = None
    arg_lines: list[str] = []

    def finish_current() -> None:
        nonlocal current, arg_lines
        if current is None:
            return
        raw_arguments = "\n".join(arg_lines).strip()
        if raw_arguments:
            current["arguments"] = _loads_json_object_or_raw(raw_arguments)
        actions.append(current)
        current = None
        arg_lines = []

    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if mode == "arguments" and current is not None:
                arg_lines.append(raw_line)
            continue
        if stripped.startswith("Action ID:"):
            finish_current()
            current = {"action_id": stripped.split(":", 1)[1].strip()}
            mode = "action"
            continue
        if stripped.startswith("Communicate Info:"):
            finish_current()
            mode = "communicate"
            trailing = stripped.split(":", 1)[1].strip()
            if trailing:
                communicate_info.append(trailing)
            continue
        if stripped.startswith("NL Assertions:"):
            finish_current()
            mode = "nl_assertions"
            trailing = stripped.split(":", 1)[1].strip()
            if trailing:
                nl_assertions.append(trailing)
            continue
        if current is not None:
            if stripped.startswith("Requestor:"):
                current["requestor"] = stripped.split(":", 1)[1].strip()
                mode = "action"
                continue
            if stripped.startswith("Name:"):
                current["name"] = stripped.split(":", 1)[1].strip()
                mode = "action"
                continue
            if stripped.startswith("Arguments:"):
                mode = "arguments"
                trailing = stripped.split(":", 1)[1].strip()
                if trailing:
                    arg_lines.append(trailing)
                continue
            if mode == "arguments":
                arg_lines.append(raw_line)
                continue
        if mode == "communicate":
            communicate_info.append(stripped)
        elif mode == "nl_assertions":
            nl_assertions.append(stripped)
    finish_current()
    return {
        "actions": actions,
        "communicate_info": communicate_info,
        "nl_assertions": nl_assertions,
    }


def _loads_json_object_or_raw(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _is_state_changing_action_name(name: str) -> bool:
    lowered = str(name or "").lower()
    return lowered.startswith(
        (
            "book_",
            "cancel_",
            "create_",
            "delete_",
            "modify_",
            "pay_",
            "purchase_",
            "refund_",
            "remove_",
            "send_",
            "submit_",
            "transfer_",
            "update_",
        )
    )
