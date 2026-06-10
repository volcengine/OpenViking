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
)
from openviking.session.train.engine import PolicyTrainingEngine
from openviking.session.train.interfaces import (
    GradientEstimator,
    PolicyOptimizer,
    PolicyUpdater,
    RolloutAnalyzer,
    SemanticGradient,
)
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
    ) -> RolloutTrainingResult:
        ctx = _coerce_pipeline_context(context)
        rollout_list = list(rollouts)
        _validate_rollouts_have_cases(rollout_list)
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
                "score": _average_score(analyses),
                "source": "batch_rollouts",
            },
        )


@dataclass(slots=True)
class StreamingPolicyTrainerConfig:
    """Configuration for automatic streaming rollout training."""

    max_gradients_per_update: int = 8
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

    async def get_buffered_gradient_count(self) -> int:
        """Return the current buffered gradient count under the buffer lock."""

        return await self._batcher.get_buffered_size()

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
    async def submit_rollout(self, rollout: Rollout) -> RolloutTrainingResult:
        """Submit one realtime rollout and wait for its batch update result.

        The rollout is analyzed and converted to gradients immediately, then
        buffered by the shared count/time window.  This method returns only
        after the batch containing this rollout has been optimized and applied.
        """

        if self._closed:
            raise RuntimeError("StreamingPolicyTrainer is closed")
        _validate_rollouts_have_cases([rollout])
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
        result = await self._batcher.submit(
            _BufferedRolloutTraining(
                gradients=list(gradients),
                analysis=analysis,
                rollout=rollout,
            )
        )
        self._last_apply_result = result.apply_result
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
        return result


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
        gradients = [gradient for item in items for gradient in item.gradients]
        analyses = _unique_by_identity([item.analysis for item in items])
        rollouts = _unique_by_identity([item.rollout for item in items])
        tracer.info(
            "StreamingPolicyTrainer flush started "
            f"reason={reason} "
            f"rollout_count={len(rollouts)} "
            f"analysis_count={len(analyses)} "
            f"gradient_count={len(gradients)}",
            console=self.config.trace_console,
        )
        plan, apply_result = await self._core.plan_and_apply(
            gradients=gradients,
            policy_set=self.policy_set,
            ctx=self.context,
        )
        self.policy_set = apply_result.updated_policy_set
        self._last_apply_result = apply_result
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
                "score": _average_score(analyses),
                "source": "streaming_rollouts",
                "flush_reason": reason,
            },
        )
        tracer.info(
            "StreamingPolicyTrainer flush finished "
            f"reason={reason} "
            f"written_uris={apply_result.written_uris} "
            f"errors={apply_result.errors}",
            console=self.config.trace_console,
        )
        return result


@dataclass(slots=True)
class _BufferedRolloutTraining:
    gradients: list[SemanticGradient]
    analysis: RolloutAnalysis
    rollout: Rollout


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


def _validate_rollouts_have_cases(rollouts: list[Rollout]) -> None:
    missing = [
        idx for idx, rollout in enumerate(rollouts) if getattr(rollout, "case", None) is None
    ]
    if missing:
        raise ValueError(
            f"rollout training requires Rollout.case for all rollouts; missing indices={missing}"
        )


def _average_score(analyses: list[RolloutAnalysis]) -> float | None:
    if not analyses:
        return None
    return sum(float(analysis.evaluation.score) for analysis in analyses) / len(analyses)


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
    analyses = _unique_by_identity([analysis for result in results for analysis in result.analyses])
    gradients = [gradient for result in results for gradient in result.gradients]
    metadata = dict(last.metadata)
    metadata.update(
        {
            "source": source,
            "rollout_count": len(analyses),
            "analysis_count": len(analyses),
            "gradient_count": len(gradients),
            "score": _average_score(analyses),
        }
    )
    return RolloutTrainingResult(
        analyses=analyses,
        gradients=gradients,
        plan=last.plan,
        apply_result=last.apply_result,
        metadata=metadata,
    )
