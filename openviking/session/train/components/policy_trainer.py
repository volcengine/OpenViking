# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Batch and streaming trainers for session policy optimization.

The trainers expose rollout-driven training primitives shared by offline and
realtime collection paths.  They intentionally reuse the same downstream stages
as ``OfflinePolicyOptimizationPipeline`` while separating trainer concerns from
case rollout execution.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Hashable

from openviking.session.memory.utils.streaming_batcher import (
    StreamingBatcher,
    StreamingBatcherConfig,
)
from openviking.session.train.context import PipelineContext
from openviking.session.train.domain import (
    ExperienceSet,
    PolicyApplyResult,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
    ScopedRolloutTrainingResult,
)
from openviking.session.train.engine import PolicyTrainingEngine
from openviking.session.train.interfaces import (
    GradientEstimator,
    PolicyOptimizer,
    PolicyUpdater,
    RolloutAnalyzer,
    SemanticGradient,
)
from openviking.session.train.utils import average_score, validate_rollouts_have_cases
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class BatchPolicyTrainer:
    """Train a policy from an explicit batch of rollout records."""

    rollout_analyzer: RolloutAnalyzer
    gradient_estimator: GradientEstimator
    policy_optimizer: PolicyOptimizer
    policy_updater: PolicyUpdater
    _engine: PolicyTrainingEngine = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._engine = PolicyTrainingEngine(
            rollout_analyzer=self.rollout_analyzer,
            gradient_estimator=self.gradient_estimator,
            policy_optimizer=self.policy_optimizer,
            policy_updater=self.policy_updater,
        )

    @tracer("train.batch_policy_trainer.train_rollouts", ignore_result=True, ignore_args=True)
    async def train_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        context: PipelineContext | Any = None,
        analyses: list[RolloutAnalysis] | None = None,
    ) -> RolloutTrainingResult | ScopedRolloutTrainingResult:
        ctx = _coerce_pipeline_context(context)
        rollout_list = list(rollouts)
        validate_rollouts_have_cases(rollout_list)
        if analyses is None:
            analyses = await self._engine.analyze_rollouts(rollout_list, ctx)
        else:
            analyses = list(analyses)
        gradients = await self._engine.estimate_gradients(analyses, policy_set, ctx)
        plan, apply_result = await self._engine.plan_and_apply(
            gradients=gradients,
            policy_set=policy_set,
            ctx=ctx,
        )
        return RolloutTrainingResult(
            analyses=analyses,
            gradients=gradients,
            plan=plan,
            apply_result=apply_result,
            metadata={
                "policy_set_root_uri": apply_result.updated_policy_set.root_uri,
                "rollout_count": len(rollout_list),
                "analysis_count": len(analyses),
                "gradient_count": len(gradients),
                "score": average_score(analyses),
                "source": "batch_rollouts",
            },
        )


@dataclass(slots=True)
class StreamingPolicyTrainerConfig:
    """Configuration for automatic streaming rollout training."""

    max_gradients_per_update: int = 16
    max_wait_seconds: float = 10.0
    timer_check_interval_seconds: float = 1.0
    trace_console: bool = False

    def __post_init__(self) -> None:
        if self.max_gradients_per_update <= 0:
            raise ValueError("max_gradients_per_update must be > 0")
        if self.max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be > 0")
        if self.timer_check_interval_seconds <= 0:
            raise ValueError("timer_check_interval_seconds must be > 0")


@dataclass(frozen=True, slots=True)
class StreamingPolicyTrainerKey:
    """Process-local registry key for one shared streaming trainer."""

    account_id: str
    user_id: str
    policy_root_uri: str


@dataclass(slots=True)
class StreamingPolicyTrainer:
    """Long-lived rollout trainer that batches concurrent semantic gradients.

    ``submit_rollout`` analyzes a rollout and estimates gradients immediately,
    then appends those gradients to an in-memory buffer.  A policy update is
    automatically triggered either when the buffer reaches
    ``max_gradients_per_update`` or when the oldest buffered gradient waits at
    least ``max_wait_seconds``.  If the submitting call triggers a count-based
    flush, it waits for optimizer/apply completion before returning.
    """

    policy_set: ExperienceSet
    rollout_analyzer: RolloutAnalyzer
    gradient_estimator: GradientEstimator
    policy_optimizer: PolicyOptimizer
    policy_updater: PolicyUpdater
    context: PipelineContext | Any = None
    config: StreamingPolicyTrainerConfig = field(default_factory=StreamingPolicyTrainerConfig)
    _core: PolicyTrainingEngine = field(init=False, repr=False)
    _batcher: StreamingBatcher[_BufferedRolloutTraining, RolloutTrainingResult] = field(
        init=False, repr=False
    )
    _last_apply_result: PolicyApplyResult | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.context = _coerce_pipeline_context(self.context)
        self._core = PolicyTrainingEngine(
            rollout_analyzer=self.rollout_analyzer,
            gradient_estimator=self.gradient_estimator,
            policy_optimizer=self.policy_optimizer,
            policy_updater=self.policy_updater,
        )
        self._batcher = StreamingBatcher(
            name="openviking-streaming-policy-trainer",
            process_batch=self._process_batch,
            config=StreamingBatcherConfig(
                max_items_per_batch=self.config.max_gradients_per_update,
                max_wait_seconds=self.config.max_wait_seconds,
                timer_check_interval_seconds=self.config.timer_check_interval_seconds,
            ),
            item_size=lambda item: len(item.gradients),
            result_metadata=lambda result: result.metadata,
        )
        self._last_apply_result: PolicyApplyResult | None = None
        self._closed = False

    @property
    def last_apply_result(self) -> PolicyApplyResult | None:
        return self._last_apply_result

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> RolloutTrainingResult | None:
        """Stop the timer task and flush any buffered gradients once.

        ``close`` is idempotent.  The first call cancels the background timer
        and applies any remaining buffered gradients with ``flush_reason="close"``.
        Subsequent calls are no-ops and return ``None``.
        """

        if self._closed:
            return None
        self._closed = True
        return await self._batcher.close()

    @tracer("train.streaming_policy_trainer.submit_rollout", ignore_result=True, ignore_args=True)
    async def submit_rollout(
        self,
        rollout: Rollout,
    ) -> RolloutTrainingResult | ScopedRolloutTrainingResult:
        """Submit one realtime rollout and wait for its batch update result.

        The rollout is analyzed and converted to gradients immediately, then
        buffered by the shared count/time window.  This method returns only
        after the batch containing this rollout has been optimized and applied.
        """

        if self._closed:
            raise RuntimeError("StreamingPolicyTrainer is closed")
        validate_rollouts_have_cases([rollout])
        analysis = await self.rollout_analyzer.analyze(rollout, self.context.analysis_context)
        gradients = await self.gradient_estimator.estimate(
            analysis,
            self.policy_set,
            self.context.gradient_context,
        )
        tracer.info(
            "StreamingPolicyTrainer buffered rollout "
            f"rollout_case={rollout.case.name} "
            f"new_gradients={len(gradients)}",
            console=self.config.trace_console,
        )
        buffered = _BufferedRolloutTraining(
            gradients=list(gradients),
            analysis=analysis,
            rollout=rollout,
        )
        result = await self._batcher.submit(buffered)
        self._last_apply_result = result.apply_result
        scoped_result = _scope_training_result_to_submitter(result, buffered)
        tracer.info(
            "StreamingPolicyTrainer submit finished "
            f"batch_id={result.metadata.get('batch_id')} "
            f"batch_trace_id={result.metadata.get('batch_trace_id')} "
            f"flush_reason={result.metadata.get('flush_reason')} "
            f"rollout_count={result.metadata.get('rollout_count')} "
            f"gradient_count={result.metadata.get('gradient_count')} "
            f"written_uris={result.apply_result.written_uris} "
            f"errors={result.apply_result.errors}",
            console=self.config.trace_console,
        )
        return scoped_result

    @tracer("train.streaming_policy_trainer.submit_gradients", ignore_result=True, ignore_args=True)
    async def submit_gradients(
        self,
        gradients: list[SemanticGradient],
        *,
        analysis: RolloutAnalysis | None = None,
        rollout: Rollout | None = None,
    ) -> RolloutTrainingResult | ScopedRolloutTrainingResult:
        """Submit pre-computed gradients directly to the streaming trainer.

        Unlike ``submit_rollout``, this method skips analysis and gradient
        estimation.  It is useful for memory types whose gradients are
        produced during an earlier stage (e.g. session skills co-extracted
        during trajectory analysis).
        """
        if self._closed:
            raise RuntimeError("StreamingPolicyTrainer is closed")
        if not gradients:
            # No gradients to submit — return a no-op result immediately.
            return RolloutTrainingResult(
                analyses=[analysis] if analysis is not None else [],
                gradients=[],
                plan=PolicyUpdatePlan(items=[], metadata={"no_op": True}),
                apply_result=PolicyApplyResult(
                    updated_policy_set=self.policy_set,
                    written_uris=[],
                    errors=[],
                    metadata={"no_op": True},
                ),
                metadata={"no_op": True, "gradient_count": 0},
            )
        tracer.info(
            "StreamingPolicyTrainer buffered gradients "
            f"new_gradients={len(gradients)}",
            console=self.config.trace_console,
        )
        buffered = _BufferedRolloutTraining(
            gradients=list(gradients),
            analysis=analysis,
            rollout=rollout,
        )
        result = await self._batcher.submit(buffered)
        self._last_apply_result = result.apply_result
        return _scope_training_result_to_submitter(result, buffered)


    @tracer("train.streaming_policy_trainer.train_rollouts", ignore_result=True, ignore_args=True)
    async def train_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        context: PipelineContext | Any = None,
        analyses: list[RolloutAnalysis] | None = None,
    ) -> RolloutTrainingResult:
        del policy_set, context, analyses
        results = await asyncio.gather(*[self.submit_rollout(rollout) for rollout in rollouts])
        return _combine_training_results(results, source="streaming_rollouts")

    async def _process_batch(
        self,
        items: list["_BufferedRolloutTraining"],
        reason: str,
    ) -> RolloutTrainingResult:
        chunks = _chunks_buffered_items_by_gradient_count(
            items,
            self.config.max_gradients_per_update,
        )
        gradients = [gradient for chunk in chunks for gradient in chunk.gradients]
        analyses = _unique_by_identity(
            [item.analysis for item in items if item.analysis is not None]
        )
        rollouts = _unique_by_identity(
            [item.rollout for item in items if item.rollout is not None]
        )
        tracer.info(
            "StreamingPolicyTrainer flush started "
            f"reason={reason} "
            f"rollout_count={len(rollouts)} "
            f"analysis_count={len(analyses)} "
            f"gradient_count={len(gradients)}",
            console=self.config.trace_console,
        )

        plans: list[PolicyUpdatePlan] = []
        apply_results: list[PolicyApplyResult] = []
        for chunk_index, chunk in enumerate(chunks):
            gradient_chunk = chunk.gradients
            tracer.info(
                "StreamingPolicyTrainer flush chunk started "
                f"reason={reason} "
                f"chunk_index={chunk_index} "
                f"gradient_count={len(gradient_chunk)}",
                console=self.config.trace_console,
            )
            plan, apply_result = await self._core.plan_and_apply(
                gradients=gradient_chunk,
                policy_set=self.policy_set,
                ctx=self.context,
            )
            self.policy_set = apply_result.updated_policy_set
            plans.append(plan)
            apply_results.append(apply_result)
            tracer.info(
                "StreamingPolicyTrainer flush chunk finished "
                f"reason={reason} "
                f"chunk_index={chunk_index} "
                f"written_uris={apply_result.written_uris} "
                f"errors={apply_result.errors}",
                console=self.config.trace_console,
            )

        plan = _combine_update_plans(plans)
        apply_result = _combine_apply_results(apply_results, fallback_policy_set=self.policy_set)
        self.policy_set = apply_result.updated_policy_set
        self._last_apply_result = apply_result
        chunk_gradient_counts = [len(chunk.gradients) for chunk in chunks]
        result = RolloutTrainingResult(
            analyses=analyses,
            gradients=gradients,
            plan=plan,
            apply_result=apply_result,
            metadata={
                "policy_set_root_uri": apply_result.updated_policy_set.root_uri,
                "rollout_count": len(rollouts),
                "analysis_count": len(analyses),
                "gradient_count": len(gradients),
                "chunk_count": len(chunk_gradient_counts),
                "chunk_gradient_counts": chunk_gradient_counts,
                "score": average_score(analyses),
                "source": "streaming_rollouts",
                "flush_reason": reason,
            },
        )
        tracer.info(
            "StreamingPolicyTrainer flush finished "
            f"reason={reason} "
            f"chunk_count={len(chunk_gradient_counts)} "
            f"written_uris={apply_result.written_uris} "
            f"errors={apply_result.errors}",
            console=self.config.trace_console,
        )
        return result


def _chunks_buffered_items_by_gradient_count(
    items: list["_BufferedRolloutTraining"],
    size: int,
) -> list["_BufferedRolloutTrainingChunk"]:
    if size <= 0:
        raise ValueError("chunk size must be > 0")

    all_gradients: list[SemanticGradient] = []
    for item in items:
        all_gradients.extend(item.gradients)

    if not all_gradients:
        return [_BufferedRolloutTrainingChunk(gradients=[])]

    chunks: list[_BufferedRolloutTrainingChunk] = []
    for start in range(0, len(all_gradients), size):
        chunk_gradients = all_gradients[start : start + size]
        chunks.append(_BufferedRolloutTrainingChunk(gradients=chunk_gradients))
    return chunks


def _combine_update_plans(plans: list[PolicyUpdatePlan]) -> PolicyUpdatePlan:
    if not plans:
        return PolicyUpdatePlan(items=[], metadata={"chunk_count": 0})
    items = [item for plan in plans for item in plan.items]
    return PolicyUpdatePlan(
        items=items,
        metadata={
            "chunk_count": len(plans),
            "chunk_item_counts": [len(plan.items) for plan in plans],
            "chunks": [dict(plan.metadata or {}) for plan in plans],
        },
    )


def _combine_apply_results(
    results: list[PolicyApplyResult],
    *,
    fallback_policy_set: ExperienceSet,
) -> PolicyApplyResult:
    if not results:
        return PolicyApplyResult(updated_policy_set=fallback_policy_set)
    return PolicyApplyResult(
        updated_policy_set=results[-1].updated_policy_set,
        written_uris=[uri for result in results for uri in result.written_uris],
        deleted_uris=[uri for result in results for uri in result.deleted_uris],
        errors=[error for result in results for error in result.errors],
        metadata={
            "chunk_count": len(results),
            "chunk_metadata": [dict(result.metadata or {}) for result in results],
        },
    )


def _scope_training_result_to_submitter(
    result: RolloutTrainingResult,
    submitter: "_BufferedRolloutTraining",
) -> RolloutTrainingResult | ScopedRolloutTrainingResult:
    """Return the submitting rollout's view of a shared streaming flush.

    StreamingBatcher intentionally gives every waiter the same batch result.
    For per-commit consumers (memory_diff/case links), exposing all batch plan
    items would make one trace appear to add every other concurrently flushed
    experience.  Keep the full batch result available via ``batch_result`` but
    scope the top-level fields to the submitter's analyses and source
    trajectories.
    """

    analysis = submitter.analysis
    if analysis is None:
        return result

    scoped_plan = _scope_plan_to_analysis(
        result.plan,
        analysis=analysis,
        apply_result=result.apply_result,
    )
    scoped_apply_result = _scope_apply_result_to_plan(
        result.apply_result,
        scoped_plan,
    )
    metadata = dict(result.metadata or {})
    metadata.update(
        {
            "batch_rollout_count": metadata.get("rollout_count"),
            "batch_analysis_count": metadata.get("analysis_count"),
            "batch_gradient_count": metadata.get("gradient_count"),
            "rollout_count": 1 if submitter.rollout is not None else 0,
            "analysis_count": 1,
            "gradient_count": len(submitter.gradients),
            "source": "streaming_rollouts_scoped",
            "scoped_to_submitter": True,
        }
    )
    return ScopedRolloutTrainingResult(
        analyses=[analysis],
        gradients=list(submitter.gradients),
        plan=scoped_plan,
        apply_result=scoped_apply_result,
        batch_result=result,
        metadata=metadata,
    )


def _scope_plan_to_analysis(
    plan: PolicyUpdatePlan,
    *,
    analysis: RolloutAnalysis,
    apply_result: PolicyApplyResult,
) -> PolicyUpdatePlan:
    trajectory_uris = _analysis_trajectory_uris(analysis)
    scoped_items = [
        item
        for item in list(getattr(plan, "items", []) or [])
        if _plan_item_belongs_to_trajectories(
            item,
            trajectory_uris=trajectory_uris,
        )
    ]
    metadata = dict(getattr(plan, "metadata", {}) or {})
    metadata.update(
        {
            "scoped_to_trajectory_uris": sorted(trajectory_uris),
            "unscoped_item_count": len(getattr(plan, "items", []) or []),
        }
    )
    return PolicyUpdatePlan(items=scoped_items, metadata=metadata)


def _plan_item_belongs_to_trajectories(
    item: Any,
    *,
    trajectory_uris: set[str],
) -> bool:
    if not trajectory_uris:
        return False
    for link in getattr(item, "links", []) or []:
        try:
            if hasattr(link, "to_uri"):
                to_uri = str(getattr(link, "to_uri", "") or "")
                link_type = str(getattr(link, "link_type", "") or "")
            elif isinstance(link, dict):
                to_uri = str(link.get("to_uri") or "")
                link_type = str(link.get("link_type") or "")
            else:
                continue
        except Exception:
            continue
        if link_type == "derived_from" and to_uri in trajectory_uris:
            return True
    # Deletes may not carry fresh links when a merged replacement owns the
    # source trajectory links. Keep only upserts in submitter-scoped views.
    return False


def _scope_apply_result_to_plan(
    apply_result: PolicyApplyResult,
    plan: PolicyUpdatePlan,
) -> PolicyApplyResult:
    plan_uris = {
        _plan_item_uri(item, getattr(apply_result.updated_policy_set, "root_uri", ""))
        for item in getattr(plan, "items", []) or []
    }
    metadata = dict(getattr(apply_result, "metadata", {}) or {})
    metadata.update(
        {
            "unscoped_written_uris": list(getattr(apply_result, "written_uris", []) or []),
            "unscoped_deleted_uris": list(getattr(apply_result, "deleted_uris", []) or []),
        }
    )
    return PolicyApplyResult(
        updated_policy_set=apply_result.updated_policy_set,
        written_uris=[uri for uri in getattr(apply_result, "written_uris", []) or [] if uri in plan_uris],
        deleted_uris=[uri for uri in getattr(apply_result, "deleted_uris", []) or [] if uri in plan_uris],
        errors=list(getattr(apply_result, "errors", []) or []),
        metadata=metadata,
    )


def _analysis_trajectory_uris(analysis: RolloutAnalysis) -> set[str]:
    return {
        str(getattr(trajectory, "uri", "") or "")
        for trajectory in getattr(analysis, "trajectories", []) or []
        if str(getattr(trajectory, "uri", "") or "")
    }


def _plan_item_uri(item: Any, root_uri: str) -> str:
    uri = str(getattr(item, "target_uri", "") or "")
    if uri:
        return uri
    name = str(getattr(item, "target_name", "") or "new_experience")
    return f"{root_uri.rstrip('/')}/{_safe_policy_filename(name)}.md"


def _safe_policy_filename(name: str) -> str:
    import re

    filename = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name.strip()).strip("._-")
    return filename or "new_experience"


@dataclass(slots=True)
class _BufferedRolloutTraining:
    gradients: list[SemanticGradient]
    analysis: RolloutAnalysis | None = None
    rollout: Rollout | None = None


@dataclass(slots=True)
class _BufferedRolloutTrainingChunk:
    gradients: list[SemanticGradient]


_streaming_policy_trainer_registry: dict[Hashable, StreamingPolicyTrainer] = {}
_streaming_policy_trainer_registry_lock = threading.RLock()


async def get_streaming_policy_trainer(
    *,
    key: StreamingPolicyTrainerKey | Hashable,
    policy_set: ExperienceSet,
    rollout_analyzer: RolloutAnalyzer,
    gradient_estimator: GradientEstimator,
    policy_optimizer: PolicyOptimizer,
    policy_updater: PolicyUpdater,
    context: PipelineContext | Any = None,
    config: StreamingPolicyTrainerConfig | None = None,
) -> StreamingPolicyTrainer:
    """Get or create the process-global streaming trainer for one policy key."""

    with _streaming_policy_trainer_registry_lock:
        existing = _streaming_policy_trainer_registry.get(key)
        if existing is not None:
            return existing
        trainer = StreamingPolicyTrainer(
            policy_set=policy_set,
            rollout_analyzer=rollout_analyzer,
            gradient_estimator=gradient_estimator,
            policy_optimizer=policy_optimizer,
            policy_updater=policy_updater,
            context=context,
            config=config or StreamingPolicyTrainerConfig(),
        )
        _streaming_policy_trainer_registry[key] = trainer
        return trainer


def make_streaming_policy_trainer_key(
    *,
    policy_root_uri: str,
    request_context: Any,
) -> StreamingPolicyTrainerKey:
    """Build the default registry key from policy root and request context."""

    user = getattr(request_context, "user", None)
    account_id = (
        getattr(request_context, "account_id", None)
        or getattr(user, "account_id", None)
        or "default"
    )
    user_id = getattr(request_context, "user_id", None) or getattr(user, "user_id", None) or ""
    return StreamingPolicyTrainerKey(
        account_id=str(account_id),
        user_id=str(user_id),
        policy_root_uri=policy_root_uri,
    )


def _coerce_pipeline_context(context: PipelineContext | Any = None) -> PipelineContext:
    return context if isinstance(context, PipelineContext) else PipelineContext()


def _unique_by_identity(items: list[Any]) -> list[Any]:
    seen: set[int] = set()
    unique = []
    for item in items:
        item_id = id(item)
        if item_id in seen:
            continue
        seen.add(item_id)
        unique.append(item)
    return unique


def _combine_training_results(
    results: list[RolloutTrainingResult],
    *,
    source: str,
) -> RolloutTrainingResult:
    if not results:
        return RolloutTrainingResult(
            analyses=[],
            gradients=[],
            plan=PolicyUpdatePlan(metadata={"empty": True}),
            apply_result=PolicyApplyResult(updated_policy_set=ExperienceSet(root_uri="", policies=[])),
            metadata={
                "source": source,
                "rollout_count": 0,
                "analysis_count": 0,
                "gradient_count": 0,
            },
        )

    last = results[-1]
    last_unscoped = getattr(last, "batch_result", last)
    analyses = _unique_by_identity([analysis for result in results for analysis in result.analyses])
    gradients = [gradient for result in results for gradient in result.gradients]
    metadata = dict(last_unscoped.metadata)
    metadata.update(
        {
            "source": source,
            "rollout_count": len(analyses),
            "analysis_count": len(analyses),
            "gradient_count": len(gradients),
            "score": average_score(analyses),
        }
    )
    return RolloutTrainingResult(
        analyses=analyses,
        gradients=gradients,
        plan=last_unscoped.plan,
        apply_result=last_unscoped.apply_result,
        metadata=metadata,
    )
