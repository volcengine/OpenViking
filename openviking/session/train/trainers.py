# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Batch and streaming trainers for session policy optimization.

The trainers expose rollout-driven training primitives shared by offline and
realtime collection paths.  They intentionally reuse the same downstream stages
as ``DefaultPolicyOptimizationPipeline`` while separating trainer concerns from
case rollout execution.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Hashable

from openviking.session.train.domain import (
    ApplyResult,
    ExperienceSet,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
)
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

if TYPE_CHECKING:
    from openviking.session.train.pipeline import PipelineContext


@dataclass(slots=True)
class BatchPolicyTrainer:
    """Train a policy from an explicit batch of rollout records."""

    rollout_analyzer: RolloutAnalyzer
    gradient_estimator: GradientEstimator
    policy_optimizer: PolicyOptimizer
    policy_updater: PolicyUpdater

    @tracer("train.batch_policy_trainer.train_rollouts", ignore_result=True, ignore_args=True)
    async def train_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        context: PipelineContext | Any = None,
    ) -> RolloutTrainingResult:
        ctx = _coerce_pipeline_context(context)
        rollout_list = list(rollouts)
        _validate_rollouts_have_cases(rollout_list)
        helper = _PolicyTrainerCore(
            rollout_analyzer=self.rollout_analyzer,
            gradient_estimator=self.gradient_estimator,
            policy_optimizer=self.policy_optimizer,
            policy_updater=self.policy_updater,
        )
        analyses, gradients, plan, apply_result = await helper.analyze_estimate_plan_apply(
            rollouts=rollout_list,
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
    max_wait_seconds: float = 30.0
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
    _core: _PolicyTrainerCore = field(init=False, repr=False)
    _buffer: list[_BufferedGradient] = field(init=False, repr=False)
    _buffer_lock: asyncio.Lock = field(init=False, repr=False)
    _flush_lock: asyncio.Lock = field(init=False, repr=False)
    _timer_task: asyncio.Task[Any] | None = field(init=False, default=None, repr=False)
    _last_apply_result: ApplyResult | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.context = _coerce_pipeline_context(self.context)
        self._core = _PolicyTrainerCore(
            rollout_analyzer=self.rollout_analyzer,
            gradient_estimator=self.gradient_estimator,
            policy_optimizer=self.policy_optimizer,
            policy_updater=self.policy_updater,
        )
        self._buffer: list[_BufferedGradient] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_lock = asyncio.Lock()
        self._timer_task: asyncio.Task[Any] | None = None
        self._last_apply_result: ApplyResult | None = None
        self._closed = False

    @property
    def last_apply_result(self) -> ApplyResult | None:
        return self._last_apply_result

    async def get_buffered_gradient_count(self) -> int:
        """Return the current buffered gradient count under the buffer lock."""

        async with self._buffer_lock:
            return len(self._buffer)

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
        await self._stop_timer_task()
        return await self._flush_ready_batch(reason="close")

    @tracer("train.streaming_policy_trainer.submit_rollout", ignore_result=True, ignore_args=True)
    async def submit_rollout(self, rollout: Rollout) -> RolloutTrainingResult | None:
        """Submit one realtime rollout and maybe trigger an automatic update.

        Returns a ``RolloutTrainingResult`` only when this submission triggers a
        count-based flush.  Otherwise it returns ``None`` after buffering the
        estimated gradients; a later submit or the timer loop will flush them.
        """

        if self._closed:
            raise RuntimeError("StreamingPolicyTrainer is closed")
        _validate_rollouts_have_cases([rollout])
        self._ensure_timer_task()
        analysis = await self.rollout_analyzer.analyze(rollout, self.context.analysis_context)
        gradients = await self.gradient_estimator.estimate(
            analysis,
            self.policy_set,
            self.context.gradient_context,
        )
        should_flush = False
        async with self._buffer_lock:
            now = time.monotonic()
            self._buffer.extend(
                _BufferedGradient(
                    gradient=gradient,
                    analysis=analysis,
                    rollout=rollout,
                    submitted_at=now,
                )
                for gradient in gradients
            )
            should_flush = len(self._buffer) >= self.config.max_gradients_per_update
            tracer.info(
                "StreamingPolicyTrainer buffered rollout "
                f"rollout_case={rollout.case.name} "
                f"new_gradients={len(gradients)} "
                f"buffered_gradients={len(self._buffer)} "
                f"should_flush={should_flush}",
                console=self.config.trace_console,
            )

        if should_flush:
            return await self._flush_ready_batch(reason="count")
        return None

    def _ensure_timer_task(self) -> None:
        if self._timer_task is not None and not self._timer_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "[StreamingPolicyTrainer] timer loop not started: reason=no running event loop"
            )
            self._timer_task = None
            return
        self._timer_task = loop.create_task(
            self._run_timer_loop(),
            name="openviking-streaming-policy-trainer-flush-loop",
        )

    async def _stop_timer_task(self) -> None:
        task = self._timer_task
        if task is None:
            return
        self._timer_task = None
        if task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run_timer_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.config.timer_check_interval_seconds)
                if await self._should_flush_by_time():
                    await self._flush_ready_batch(reason="time")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[StreamingPolicyTrainer] timer flush loop iteration failed: {exc}")

    async def _should_flush_by_time(self) -> bool:
        async with self._buffer_lock:
            if not self._buffer:
                return False
            oldest_submitted_at = min(item.submitted_at for item in self._buffer)
            return (time.monotonic() - oldest_submitted_at) >= self.config.max_wait_seconds

    async def _flush_ready_batch(self, *, reason: str) -> RolloutTrainingResult | None:
        async with self._flush_lock:
            async with self._buffer_lock:
                if not self._buffer:
                    return None
                items = self._buffer
                self._buffer = []

            gradients = [item.gradient for item in items]
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
            try:
                plan, apply_result = await self._core.plan_and_apply(
                    gradients=gradients,
                    policy_set=self.policy_set,
                    ctx=self.context,
                )
            except Exception:
                await self._restore_front(items)
                raise

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

    async def _restore_front(self, items: list[_BufferedGradient]) -> None:
        async with self._buffer_lock:
            self._buffer = [*items, *self._buffer]


@dataclass(slots=True)
class _PolicyTrainerCore:
    rollout_analyzer: RolloutAnalyzer
    gradient_estimator: GradientEstimator
    policy_optimizer: PolicyOptimizer
    policy_updater: PolicyUpdater

    async def analyze_estimate_plan_apply(
        self,
        *,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> tuple[list[RolloutAnalysis], list[SemanticGradient], PolicyUpdatePlan, ApplyResult]:
        analyses = await self.analyze_rollouts(rollouts, ctx)
        gradients = await self.estimate_gradients(analyses, policy_set, ctx)
        plan, apply_result = await self.plan_and_apply(
            gradients=gradients,
            policy_set=policy_set,
            ctx=ctx,
        )
        return analyses, gradients, plan, apply_result

    async def analyze_rollouts(
        self,
        rollouts: list[Rollout],
        ctx: PipelineContext,
    ) -> list[RolloutAnalysis]:
        analyses = await asyncio.gather(
            *[self.rollout_analyzer.analyze(rollout, ctx.analysis_context) for rollout in rollouts]
        )
        return list(analyses)

    async def estimate_gradients(
        self,
        analyses: list[RolloutAnalysis],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> list[SemanticGradient]:
        gradient_batches = await asyncio.gather(
            *[
                self.gradient_estimator.estimate(
                    analysis,
                    policy_set,
                    ctx.gradient_context,
                )
                for analysis in analyses
            ]
        )
        return [gradient for batch in gradient_batches for gradient in batch]

    async def plan_and_apply(
        self,
        *,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> tuple[PolicyUpdatePlan, ApplyResult]:
        async with policy_set.lock():
            latest_policy_set = await policy_set.reload()
            plan = await self.policy_optimizer.plan(
                gradients,
                latest_policy_set,
                ctx.optimization_context,
            )
            apply_result = await self.policy_updater.apply(
                plan,
                latest_policy_set,
                ctx.apply_context or latest_policy_set.request_context,
            )
        return plan, apply_result


@dataclass(slots=True)
class _BufferedGradient:
    gradient: SemanticGradient
    analysis: RolloutAnalysis
    rollout: Rollout
    submitted_at: float


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
    from openviking.session.train.pipeline import PipelineContext

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
