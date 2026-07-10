# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from test_fakes import InMemoryAGFS, fake_request_context

from openviking.message import Message, TextPart
from openviking.session.memory.dataclass import StoredLink
from openviking.session.train import (
    Case,
    Experience,
    ExperienceSet,
    ListCaseLoader,
    NoopPipelineLifecycleHook,
    OfflinePolicyOptimizationPipeline,
    PipelineContext,
    PipelineHookDecision,
    PipelineReportHook,
    PolicyApplyResult,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.components.reporter import ConsolePipelineReporter
from openviking.storage.transaction import init_lock_manager, reset_lock_manager


@pytest.fixture(autouse=True)
def _train_lock_manager():
    reset_lock_manager()
    init_lock_manager(InMemoryAGFS(), redo_recovery_enabled=False)
    yield
    reset_lock_manager()


def _case() -> Case:
    return Case(
        name="duplicate_booking",
        task_signature="booking_duplicate",
        input={"user_request": "cancel the duplicate booking"},
        rubric=Rubric(
            name="booking_duplicate_rubric",
            description="Cancel only the verified duplicate booking.",
            criteria=[
                RubricCriterion(
                    name="verify_duplicate",
                    description="Verify duplicate status before cancellation.",
                    required=True,
                    weight=1.0,
                )
            ],
        ),
    )


class DummyVikingFS:
    def __init__(self):
        self.reloads = 0
        self.version = 1

    async def ls(self, uri: str, output: str = "original", ctx=None):
        del output, ctx
        return [
            {
                "name": "booking_duplicate_handling.md",
                "uri": f"{uri.rstrip('/')}/booking_duplicate_handling.md",
                "isDir": False,
            }
        ]

    async def read_file(self, uri: str, ctx=None):
        del uri, ctx
        self.reloads += 1
        return (
            "## Situation\n- Duplicate booking handling\n\n"
            "<!-- MEMORY_FIELDS\n"
            '{"memory_type":"experiences","experience_name":"booking_duplicate_handling",'
            f'"version":{self.version},"status":"production"}}\n'
            "-->"
        )

    def _uri_to_path(self, uri: str, ctx=None) -> str:
        account_id = getattr(ctx, "account_id", "default")
        return f"/local/{account_id}/{uri.removeprefix('viking://').strip('/')}"


def _policy_set(*, version: int = 1, viking_fs: DummyVikingFS | None = None) -> ExperienceSet:
    return ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Experience(
                name="booking_duplicate_handling",
                uri="viking://user/u/memories/experiences/booking_duplicate_handling.md",
                version=version,
                status="production",
                content="## Situation\n- Duplicate booking handling",
            )
        ],
        viking_fs=viking_fs or DummyVikingFS(),
        request_context=fake_request_context(),
    )


@dataclass
class DummyGradient:
    target_name: str
    target_uri: str | None
    base_version: int | None
    rationale: str
    links: list[StoredLink]
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


class DummySnapshotter:
    def __init__(self):
        self.calls = 0

    async def snapshot(self, policy_set: ExperienceSet, context: Any) -> str:
        assert policy_set.root_uri
        self.calls += 1
        return f"snapshot-{self.calls}"


class DummyExecutor:
    def __init__(self):
        self.calls = 0

    async def execute(self, cases: list[Case], policy_set: ExperienceSet, context) -> list[Rollout]:
        self.calls += 1
        assert context.policy_snapshot_id.startswith("snapshot-")
        epoch = int(context.policy_snapshot_id.removeprefix("snapshot-")) - 1
        return [
            Rollout(
                case=case,
                messages=[
                    Message(
                        id=f"msg-{case.name}",
                        role="user",
                        parts=[TextPart(text=str(case.input["user_request"]))],
                    )
                ],
                policy_snapshot_id=context.policy_snapshot_id,
                evaluation=RubricEvaluation(
                    passed=True,
                    score=float(epoch),
                    criterion_results=[],
                    feedback=[],
                ),
            )
            for case in cases
        ]


class DummyAnalyzer:
    def __init__(self):
        self.calls = []

    async def analyze(self, rollout: Rollout, context: Any) -> RolloutAnalysis:
        self.calls.append(rollout)
        epoch = int(rollout.policy_snapshot_id.removeprefix("snapshot-")) - 1
        return RolloutAnalysis(
            evaluation=RubricEvaluation(
                passed=True,
                score=float(epoch),
                criterion_results=[],
                feedback=[],
            ),
            trajectories=[
                Trajectory(
                    name=rollout.case.task_signature,
                    uri=f"viking://user/u/memories/trajectories/{rollout.case.name}.md",
                    content="trajectory content",
                    outcome="success",
                    retrieval_anchor="Stage: final; Capability: duplicate booking handling",
                )
            ],
        )


class DummyEstimator:
    async def estimate(
        self,
        analysis: RolloutAnalysis,
        experience_set: ExperienceSet,
        context: Any,
    ) -> list[DummyGradient]:
        traj = analysis.trajectories[0]
        return [
            DummyGradient(
                target_name="booking_duplicate_handling",
                target_uri=experience_set.policies[0].uri,
                base_version=experience_set.policies[0].version,
                rationale="trajectory succeeded",
                links=[
                    StoredLink(
                        from_uri=experience_set.policies[0].uri,
                        to_uri=traj.uri,
                        link_type="derived_from",
                        weight=1.0,
                    )
                ],
                confidence=0.9,
            )
        ]


class DummyOptimizer:
    async def plan(
        self,
        gradients: list[DummyGradient],
        policy_set: ExperienceSet,
        context: Any,
    ) -> PolicyUpdatePlan:
        return PolicyUpdatePlan(metadata={"gradient_count": len(gradients)})


class DummyUpdater:
    last_instance = None

    def __init__(self):
        self.transaction_handles = []
        DummyUpdater.last_instance = self

    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: ExperienceSet,
        context: Any,
        *,
        transaction_handle: Any = None,
    ) -> PolicyApplyResult:
        self.transaction_handles.append(transaction_handle)
        updated = ExperienceSet(
            root_uri=policy_set.root_uri,
            policies=[
                Experience(
                    name=p.name,
                    uri=p.uri,
                    version=p.version + 1,
                    status=p.status,
                    content=p.content,
                    metadata=dict(p.metadata),
                )
                for p in policy_set.policies
            ],
            metadata=dict(policy_set.metadata),
            viking_fs=policy_set.viking_fs,
            request_context=policy_set.request_context,
        )
        if hasattr(policy_set.viking_fs, "version") and updated.policies:
            policy_set.viking_fs.version = updated.policies[0].version
        return PolicyApplyResult(
            updated_policy_set=updated,
            written_uris=[p.uri for p in updated.policies],
        )


class RecordingLifecycleHook(NoopPipelineLifecycleHook):
    def __init__(self):
        self.events: list[tuple[str, Any]] = []

    def on_epoch_start(self, *, epoch: int, context: Any) -> None:
        del context
        self.events.append(("epoch_start", epoch))

    async def on_epoch_end(
        self,
        *,
        epoch_result: Any,
        policy_set: Any,
        context: Any,
    ) -> PipelineHookDecision | None:
        del policy_set, context
        self.events.append(("epoch_end", epoch_result.epoch))
        return None

    def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        self.events.append(("train_rollout_report", report["epoch"]))

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        self.events.append(("train_report", report["epoch"]))

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        self.events.append(("eval_report", label, report["epoch"]))


@pytest.mark.asyncio
async def test_default_policy_optimization_pipeline_runs_one_batch():
    snapshotter = DummySnapshotter()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=snapshotter,
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )

    initial_policy_set = _policy_set()
    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=initial_policy_set,
        context=PipelineContext(),
    )

    assert len(result.analyses) == 1
    assert len(result.gradients) == 1
    assert result.plan.metadata == {"gradient_count": 1}
    assert result.apply_result.updated_policy_set.policies[0].version == 2
    assert result.apply_result.written_uris == [
        "viking://user/u/memories/experiences/booking_duplicate_handling.md"
    ]
    assert DummyUpdater.last_instance is not None
    assert len(DummyUpdater.last_instance.transaction_handles) == 1
    assert DummyUpdater.last_instance.transaction_handles[0] is not None
    assert initial_policy_set.viking_fs.reloads == 1
    assert len(result.epochs) == 1
    assert result.epochs[0].epoch == 0
    assert result.epochs[0].policy_snapshot_ids == ["snapshot-1"]


@pytest.mark.asyncio
async def test_policy_optimization_pipeline_allows_zero_epochs_without_training():
    snapshotter = DummySnapshotter()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=snapshotter,
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )

    initial_policy_set = _policy_set()
    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=initial_policy_set,
        context=PipelineContext(max_epochs=0),
    )

    assert result.analyses == []
    assert result.gradients == []
    assert result.epochs == []
    assert result.evaluation_passes == []
    assert result.apply_result.updated_policy_set is initial_policy_set
    assert result.metadata["max_epochs"] == 0
    assert result.metadata["completed_epochs"] == 0


@pytest.mark.asyncio
async def test_offline_policy_optimization_pipeline_supports_train_and_eval():
    snapshotter = DummySnapshotter()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=snapshotter,
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )
    policy_set = _policy_set()

    before_eval = await pipeline.eval(
        case_loader=ListCaseLoader([_case()]),
        policy_set=policy_set,
        context=PipelineContext(execution_metadata={"epoch": -1}),
    )
    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=policy_set,
        context=PipelineContext(max_epochs=2),
    )
    after_eval = await pipeline.eval(
        case_loader=ListCaseLoader([_case()]),
        policy_set=result.apply_result.updated_policy_set,
        context=PipelineContext(execution_metadata={"epoch": 2}),
    )

    assert before_eval.epoch == -1
    assert before_eval.metadata["score"] == 0.0
    assert [item.epoch for item in result.epochs] == [0, 1]
    assert result.evaluation_passes == []
    assert result.epochs[0].metadata["score"] == 1.0
    assert result.epochs[1].metadata["score"] == 2.0
    assert after_eval.epoch == 2
    assert after_eval.metadata["score"] == 3.0
    assert result.metadata["first_score"] == 1.0
    assert result.metadata["final_score"] == 2.0
    assert result.metadata["score_delta"] == 1.0
    assert result.apply_result.updated_policy_set.policies[0].version == 3


@pytest.mark.asyncio
async def test_train_trials_expands_training_cases_per_epoch():
    class RecordingExecutor(DummyExecutor):
        def __init__(self):
            super().__init__()
            self.train_trials = []

        async def execute(self, cases, policy_set, context):
            self.train_trials.extend(case.input.get("train_trial") for case in cases)
            return await super().execute(cases, policy_set, context)

    executor = RecordingExecutor()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=DummySnapshotter(),
        rollout_executor=executor,
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )

    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=_policy_set(),
        context=PipelineContext(train_trials=3),
    )

    assert len(result.analyses) == 3
    assert executor.train_trials == [0, 1, 2]


@pytest.mark.asyncio
async def test_training_updates_execution_metadata_epoch_each_epoch():
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=DummySnapshotter(),
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )
    context = PipelineContext(max_epochs=2, execution_metadata={"rollout_stage": "eval_train_rollout"})

    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=_policy_set(),
        context=context,
    )

    assert [item.epoch for item in result.epochs] == [0, 1]
    assert context.execution_metadata["epoch"] == 1


@pytest.mark.asyncio
async def test_train_runs_test_eval_after_each_epoch_when_configured():
    hook = RecordingLifecycleHook()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=DummySnapshotter(),
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )

    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=_policy_set(),
        context=PipelineContext(
            max_epochs=2,
            eval_each_epoch_case_loader=ListCaseLoader([_case()]),
            lifecycle_hooks=[PipelineReportHook(), hook],
        ),
    )

    assert [item.epoch for item in result.evaluation_passes] == [0, 1]
    assert result.metadata["evaluation_pass_count"] == 2
    assert [
        item.metadata.get("rollout_stage") for item in result.evaluation_passes
    ] == ["test_rollout", "test_rollout"]
    assert [item.metadata.get("eval_split") for item in result.evaluation_passes] == [
        "test",
        "test",
    ]
    assert ("eval_report", "test_rollout", 0) in hook.events
    assert ("eval_report", "test_rollout", 1) in hook.events


@pytest.mark.asyncio
async def test_train_epoch_eval_uses_configured_split_metadata():
    hook = RecordingLifecycleHook()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=DummySnapshotter(),
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )

    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=_policy_set(),
        context=PipelineContext(
            max_epochs=1,
            eval_each_epoch_case_loader=ListCaseLoader([_case()]),
            execution_metadata={
                "rollout_stage": "eval_train_rollout",
                "eval_split": "train",
            },
            lifecycle_hooks=[PipelineReportHook(), hook],
        ),
    )

    assert len(result.evaluation_passes) == 1
    assert result.evaluation_passes[0].metadata.get("rollout_stage") == "eval_train_rollout"
    assert result.evaluation_passes[0].metadata.get("eval_split") == "train"
    assert ("eval_report", "eval_train_rollout", 0) in hook.events


@pytest.mark.asyncio
async def test_offline_policy_optimization_pipeline_epoch_hook_can_stop_training():
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=DummySnapshotter(),
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )
    class StopTrainingHook(NoopPipelineLifecycleHook):
        def __init__(self):
            self.epochs: list[int] = []

        async def on_epoch_end(
            self,
            *,
            epoch_result: Any,
            policy_set: Any,
            context: Any,
        ) -> PipelineHookDecision:
            del policy_set, context
            self.epochs.append(epoch_result.epoch)
            return PipelineHookDecision(
                stop_training=True,
                reason="unit test stop",
                metadata={"epoch": epoch_result.epoch},
            )

    hook = StopTrainingHook()

    result = await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=_policy_set(),
        context=PipelineContext(max_epochs=3, lifecycle_hooks=[hook]),
    )

    assert hook.epochs == [0]
    assert [item.epoch for item in result.epochs] == [0]
    assert result.metadata["completed_epochs"] == 1
    assert result.metadata["max_epochs"] == 3
    assert result.metadata["stopped_early"] is True
    assert result.metadata["stop_reason"] == "unit test stop"
    assert result.metadata["stop_metadata"] == {"epoch": 0}


@pytest.mark.asyncio
async def test_pipeline_lifecycle_hooks_receive_report_events():
    hook = RecordingLifecycleHook()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=DummySnapshotter(),
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )

    policy_set = _policy_set()
    await pipeline.train(
        case_loader=ListCaseLoader([_case()]),
        policy_set=policy_set,
        context=PipelineContext(
            lifecycle_hooks=[PipelineReportHook(), hook],
        ),
    )
    await pipeline.eval(
        case_loader=ListCaseLoader([_case()]),
        policy_set=policy_set,
        context=PipelineContext(
            lifecycle_hooks=[PipelineReportHook(), hook],
            execution_metadata={"epoch": 1, "rollout_stage": "final_test_rollout"},
        ),
    )

    assert hook.events == [
        ("epoch_start", 0),
        ("train_rollout_report", 0),
        ("epoch_end", 0),
        ("train_report", 0),
        ("eval_report", "final_test_rollout", 1),
    ]


@pytest.mark.asyncio
async def test_policy_optimization_pipeline_trains_from_external_rollouts_without_executor():
    snapshotter = DummySnapshotter()
    executor = DummyExecutor()
    analyzer = DummyAnalyzer()
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=snapshotter,
        rollout_executor=executor,
        rollout_analyzer=analyzer,
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )
    rollout = Rollout(
        case=_case(),
        messages=[
            Message(
                id="external-rollout-user",
                role="user",
                parts=[TextPart(text="cancel duplicate booking")],
            )
        ],
        policy_snapshot_id="snapshot-1",
    )

    result = await pipeline.train_from_rollouts(
        rollouts=[rollout],
        policy_set=_policy_set(),
        context=PipelineContext(),
    )

    assert executor.calls == 0
    assert snapshotter.calls == 0
    assert analyzer.calls == [rollout]
    assert len(result.analyses) == 1
    assert len(result.gradients) == 1
    assert result.plan.metadata == {"gradient_count": 1}
    assert result.apply_result.updated_policy_set.policies[0].version == 2
    assert result.apply_result.written_uris == [
        "viking://user/u/memories/experiences/booking_duplicate_handling.md"
    ]
    assert result.metadata["source"] == "external_rollouts"
    assert result.metadata["rollout_count"] == 1


@pytest.mark.asyncio
async def test_policy_optimization_pipeline_realtime_rollouts_require_case():
    pipeline = OfflinePolicyOptimizationPipeline(
        snapshotter=DummySnapshotter(),
        rollout_executor=DummyExecutor(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )
    rollout = Rollout(
        case=None,
        messages=[
            Message(
                id="external-rollout-user",
                role="user",
                parts=[TextPart(text="cancel duplicate booking")],
            )
        ],
        policy_snapshot_id="snapshot-1",
    )

    with pytest.raises(ValueError, match="requires Rollout.case"):
        await pipeline.train_from_rollouts(
            rollouts=[rollout],
            policy_set=_policy_set(),
            context=PipelineContext(),
        )


@pytest.mark.asyncio
async def test_list_case_loader_yields_copy():
    cases = [_case()]
    loader = ListCaseLoader(cases)
    batches = [batch async for batch in loader.batches(None)]

    assert batches == [cases]
    assert batches[0] is not cases


@pytest.mark.asyncio
async def test_batch_policy_trainer_trains_from_rollout_batch():
    from openviking.session.train import BatchPolicyTrainer

    trainer = BatchPolicyTrainer(
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
    )
    rollouts = [
        Rollout(
            case=_case(),
            messages=[
                Message(
                    id="batch-rollout-user",
                    role="user",
                    parts=[TextPart(text="cancel duplicate booking")],
                )
            ],
            policy_snapshot_id="snapshot-1",
        )
    ]

    result = await trainer.train_rollouts(
        rollouts=rollouts,
        policy_set=_policy_set(),
        context=PipelineContext(),
    )

    assert len(result.analyses) == 1
    assert len(result.gradients) == 1
    assert result.plan.metadata == {"gradient_count": 1}
    assert result.apply_result.updated_policy_set.policies[0].version == 2
    assert result.metadata["source"] == "batch_rollouts"
    assert result.metadata["rollout_count"] == 1


@pytest.mark.asyncio
async def test_streaming_policy_trainer_flushes_on_gradient_count():
    from openviking.session.train import (
        PolicyPlanItem,
        StreamingPolicyTrainer,
        StreamingPolicyTrainerConfig,
    )

    class LinkingOptimizer(DummyOptimizer):
        async def plan(self, gradients, policy_set, context):
            del policy_set, context
            return PolicyUpdatePlan(
                items=[
                    PolicyPlanItem(
                        kind="upsert",
                        memory_type="experiences",
                        target_name=f"exp_{idx}",
                        target_uri=f"viking://user/u/memories/experiences/exp_{idx}.md",
                        before_content=None,
                        after_content=f"content {idx}",
                        links=list(gradient.links),
                    )
                    for idx, gradient in enumerate(gradients)
                ],
                metadata={"gradient_count": len(gradients)},
            )

    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=LinkingOptimizer(),
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(
            max_gradients_per_update=2,
            max_wait_seconds=60.0,
            timer_check_interval_seconds=60.0,
        ),
    )
    rollout1 = Rollout(
        case=_case(),
        messages=[Message(id="s1", role="user", parts=[TextPart(text="first")])],
        policy_snapshot_id="snapshot-1",
    )
    rollout2 = Rollout(
        case=_case(),
        messages=[Message(id="s2", role="user", parts=[TextPart(text="second")])],
        policy_snapshot_id="snapshot-1",
    )

    first, second = await asyncio.gather(
        trainer.submit_rollout(rollout1),
        trainer.submit_rollout(rollout2),
    )
    assert first is not second
    assert first.batch_result is second.batch_result
    assert len(first.analyses) == 1
    assert len(second.analyses) == 1
    assert [analysis.trajectories[0].uri for analysis in first.analyses] == [
        "viking://user/u/memories/trajectories/duplicate_booking.md"
    ]
    assert len(first.plan.items) == 2
    assert len(second.plan.items) == 2
    assert second.metadata["flush_reason"] == "count"
    assert second.metadata["gradient_count"] == 1
    assert second.metadata["batch_gradient_count"] == 2
    assert second.apply_result.updated_policy_set.policies[0].version == 2
    assert await trainer.get_buffered_gradient_count() == 0
    assert trainer.last_apply_result is second.batch_result.apply_result

    assert await trainer.close() is None
    assert trainer.closed is True


@pytest.mark.asyncio
async def test_streaming_policy_trainer_scopes_concurrent_submit_results_by_source_trajectory():
    from openviking.session.train import (
        PolicyPlanItem,
        StreamingPolicyTrainer,
        StreamingPolicyTrainerConfig,
    )

    class CaseAwareAnalyzer:
        async def analyze(self, rollout, context):
            del context
            return RolloutAnalysis(
                evaluation=RubricEvaluation(
                    passed=True,
                    score=1.0,
                    criterion_results=[],
                    feedback=[],
                ),
                trajectories=[
                    Trajectory(
                        name=rollout.case.name,
                        uri=f"viking://user/u/memories/trajectories/{rollout.case.name}.md",
                        content=f"trajectory {rollout.case.name}",
                        outcome="success",
                        retrieval_anchor="",
                    )
                ],
            )

    class NewExpEstimator:
        async def estimate(self, analysis, experience_set, context):
            del context
            traj = analysis.trajectories[0]
            target_uri = f"{experience_set.root_uri}/{traj.name}.md"
            return [
                DummyGradient(
                    target_name=traj.name,
                    target_uri=target_uri,
                    base_version=None,
                    rationale="new scoped experience",
                    links=[
                        StoredLink(
                            from_uri=target_uri,
                            to_uri=traj.uri,
                            link_type="derived_from",
                            weight=1.0,
                        )
                    ],
                    confidence=0.9,
                )
            ]

    class LinkingOptimizer:
        async def plan(self, gradients, policy_set, context):
            del policy_set, context
            return PolicyUpdatePlan(
                items=[
                    PolicyPlanItem(
                        kind="upsert",
                        memory_type="experiences",
                        target_name=gradient.target_name,
                        target_uri=gradient.target_uri,
                        before_content=None,
                        after_content=f"content {gradient.target_name}",
                        links=list(gradient.links),
                    )
                    for gradient in gradients
                ],
                metadata={"gradient_count": len(gradients)},
            )

    class PassthroughUpdater:
        async def apply(self, plan, policy_set, context, *, transaction_handle=None):
            del context, transaction_handle
            return PolicyApplyResult(
                updated_policy_set=policy_set,
                written_uris=[item.target_uri for item in plan.items if item.target_uri],
                errors=[],
            )

    def make_case(name: str) -> Case:
        case = _case()
        return Case(
            name=name,
            task_signature=case.task_signature,
            input=case.input,
            rubric=case.rubric,
        )

    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=CaseAwareAnalyzer(),
        gradient_estimator=NewExpEstimator(),
        policy_optimizer=LinkingOptimizer(),
        policy_updater=PassthroughUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(
            max_gradients_per_update=2,
            max_wait_seconds=60.0,
            timer_check_interval_seconds=60.0,
        ),
    )
    rollout_a = Rollout(
        case=make_case("case_a"),
        messages=[Message(id="a", role="user", parts=[TextPart(text="a")])],
        policy_snapshot_id="snapshot-1",
    )
    rollout_b = Rollout(
        case=make_case("case_b"),
        messages=[Message(id="b", role="user", parts=[TextPart(text="b")])],
        policy_snapshot_id="snapshot-1",
    )

    first, second = await asyncio.gather(
        trainer.submit_rollout(rollout_a),
        trainer.submit_rollout(rollout_b),
    )

    assert first.batch_result is second.batch_result
    assert {item.target_name for item in first.batch_result.plan.items} == {"case_a", "case_b"}
    assert [item.target_name for item in first.plan.items] == ["case_a"]
    assert [item.target_name for item in second.plan.items] == ["case_b"]
    assert first.apply_result.written_uris == [
        "viking://user/u/memories/experiences/case_a.md"
    ]
    assert second.apply_result.written_uris == [
        "viking://user/u/memories/experiences/case_b.md"
    ]

    assert await trainer.close() is None


@pytest.mark.asyncio
async def test_streaming_policy_trainer_splits_flush_by_gradient_count():
    from openviking.session.train import StreamingPolicyTrainer, StreamingPolicyTrainerConfig

    class MultiGradientEstimator:
        async def estimate(self, analysis, experience_set, context):
            del context
            traj = analysis.trajectories[0]
            return [
                DummyGradient(
                    target_name="booking_duplicate_handling",
                    target_uri=experience_set.policies[0].uri,
                    base_version=experience_set.policies[0].version,
                    rationale=f"gradient {idx}",
                    links=[
                        StoredLink(
                            from_uri=experience_set.policies[0].uri,
                            to_uri=traj.uri,
                            link_type="derived_from",
                            weight=1.0,
                        )
                    ],
                    confidence=0.9,
                )
                for idx in range(5)
            ]

    class RecordingOptimizer:
        def __init__(self):
            self.gradient_counts = []

        async def plan(self, gradients, policy_set, context):
            del policy_set, context
            self.gradient_counts.append(len(gradients))
            return PolicyUpdatePlan(metadata={"gradient_count": len(gradients)})

    optimizer = RecordingOptimizer()
    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=MultiGradientEstimator(),
        policy_optimizer=optimizer,
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(
            max_gradients_per_update=3,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="split", role="user", parts=[TextPart(text="split")])],
        policy_snapshot_id="snapshot-1",
    )

    result = await trainer.submit_rollout(rollout)

    assert optimizer.gradient_counts == [3, 2]
    assert result.metadata["gradient_count"] == 5
    assert result.metadata["chunk_count"] == 2
    assert result.metadata["chunk_gradient_counts"] == [3, 2]
    assert result.plan.metadata["chunk_item_counts"] == [0, 0]
    assert result.apply_result.metadata["chunk_count"] == 2
    assert result.apply_result.updated_policy_set.policies[0].version == 3

    assert await trainer.close() is None


@pytest.mark.asyncio
async def test_streaming_policy_trainer_chunks_multiple_target_gradients_by_count():
    """Gradients from different targets share the same chunk pool — split only by count."""
    from openviking.session.train import StreamingPolicyTrainer, StreamingPolicyTrainerConfig

    class MultiTargetEstimator:
        async def estimate(self, analysis, experience_set, context):
            del context
            traj = analysis.trajectories[0]
            return [
                DummyGradient(
                    target_name=f"target_{idx}",
                    target_uri=f"{experience_set.root_uri}/target_{idx}.md",
                    base_version=None,
                    rationale=f"gradient {idx}",
                    links=[
                        StoredLink(
                            from_uri=f"{experience_set.root_uri}/target_{idx}.md",
                            to_uri=traj.uri,
                            link_type="derived_from",
                            weight=1.0,
                        )
                    ],
                    confidence=0.9,
                )
                for idx in range(5)
            ]

    class RecordingOptimizer:
        def __init__(self):
            self.gradient_counts = []

        async def plan(self, gradients, policy_set, context):
            del policy_set, context
            self.gradient_counts.append(len(gradients))
            return PolicyUpdatePlan(metadata={"gradient_count": len(gradients)})

    optimizer = RecordingOptimizer()
    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=MultiTargetEstimator(),
        policy_optimizer=optimizer,
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(
            max_gradients_per_update=3,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="split-targets", role="user", parts=[TextPart(text="split")])],
        policy_snapshot_id="snapshot-1",
    )

    result = await trainer.submit_rollout(rollout)

    # gradients are chunked purely by count, target boundaries don't affect chunking
    assert optimizer.gradient_counts == [3, 2]
    assert result.metadata["chunk_gradient_counts"] == [3, 2]
    assert "chunk_target_counts" not in result.metadata
    assert await trainer.close() is None


@pytest.mark.asyncio
async def test_streaming_policy_trainer_mixes_categories_in_chunks():
    """All gradients share the same chunk pool regardless of training_category."""
    from openviking.session.train import StreamingPolicyTrainer, StreamingPolicyTrainerConfig

    class CategorizedEstimator:
        async def estimate(self, analysis, experience_set, context):
            del analysis, context
            return [
                DummyGradient(
                    target_name=f"target_{idx}",
                    target_uri=f"{experience_set.root_uri}/target_{idx}.md",
                    base_version=None,
                    rationale=f"gradient {idx}",
                    links=[],
                    confidence=0.9,
                    metadata={"training_category": "category_a" if idx < 2 else "category_b"},
                )
                for idx in range(4)
            ]

    class RecordingOptimizer:
        def __init__(self):
            self.gradient_counts = []
            self.categories = []

        async def plan(self, gradients, policy_set, context):
            del policy_set, context
            self.gradient_counts.append(len(gradients))
            self.categories.append(
                [gradient.metadata["training_category"] for gradient in gradients]
            )
            return PolicyUpdatePlan(metadata={"gradient_count": len(gradients)})

    optimizer = RecordingOptimizer()
    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=CategorizedEstimator(),
        policy_optimizer=optimizer,
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(
            max_gradients_per_update=3,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="split-categories", role="user", parts=[TextPart(text="split")])],
        policy_snapshot_id="snapshot-1",
    )

    result = await trainer.submit_rollout(rollout)

    # categories are no longer kept separate — all gradients chunked purely by count
    assert optimizer.gradient_counts == [3, 1]
    assert optimizer.categories == [
        ["category_a", "category_a", "category_b"],
        ["category_b"],
    ]
    assert result.metadata["chunk_gradient_counts"] == [3, 1]
    assert "chunk_categories" not in result.metadata
    assert await trainer.close() is None


@pytest.mark.asyncio
async def test_streaming_policy_trainer_flushes_on_timer():
    from openviking.session.train import StreamingPolicyTrainer, StreamingPolicyTrainerConfig

    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(
            max_gradients_per_update=10,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="timer", role="user", parts=[TextPart(text="timer")])],
        policy_snapshot_id="snapshot-1",
    )

    result = await trainer.submit_rollout(rollout)
    assert result is not None
    assert result.metadata["flush_reason"] == "time"
    assert result.metadata["gradient_count"] == 1
    assert trainer.last_apply_result is not None
    assert trainer.last_apply_result.updated_policy_set.policies[0].version == 2
    assert await trainer.get_buffered_gradient_count() == 0

    assert await trainer.close() is None
    assert trainer.closed is True


@pytest.mark.asyncio
async def test_streaming_policy_trainer_close_flushes_buffer_and_rejects_submit():
    from openviking.session.train import StreamingPolicyTrainer, StreamingPolicyTrainerConfig

    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(
            max_gradients_per_update=10,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="close", role="user", parts=[TextPart(text="close")])],
        policy_snapshot_id="snapshot-1",
    )

    submit_task = asyncio.create_task(trainer.submit_rollout(rollout))
    await asyncio.sleep(0)
    assert await trainer.get_buffered_gradient_count() == 1

    result = await trainer.close()

    assert result is not None
    assert result.metadata["flush_reason"] == "close"
    assert result.metadata["gradient_count"] == 1
    submit_result = await submit_task
    assert submit_result.batch_result is result
    assert trainer.closed is True
    assert await trainer.get_buffered_gradient_count() == 0
    assert trainer.last_apply_result is result.apply_result
    assert await trainer.close() is None

    with pytest.raises(RuntimeError, match="closed"):
        await trainer.submit_rollout(rollout)


@pytest.mark.asyncio
async def test_get_streaming_policy_trainer_returns_process_global_instance():
    from openviking.session.train import (
        StreamingPolicyTrainerConfig,
        get_streaming_policy_trainer,
        make_streaming_policy_trainer_key,
    )

    policy_set = _policy_set()
    key = make_streaming_policy_trainer_key(
        policy_root_uri=policy_set.root_uri,
        request_context=policy_set.request_context,
    )

    first = await get_streaming_policy_trainer(
        key=key,
        policy_set=policy_set,
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(max_gradients_per_update=3),
    )
    second = await get_streaming_policy_trainer(
        key=key,
        policy_set=policy_set,
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
        policy_updater=DummyUpdater(),
        context=PipelineContext(),
        config=StreamingPolicyTrainerConfig(max_gradients_per_update=9),
    )

    assert second is first
    assert first.config.max_gradients_per_update == 3

class FakeSessionCommitClient:
    def __init__(self):
        self.created_sessions = []
        self.messages = {}
        self.committed_sessions = []
        self.task_poll_counts = {}

    async def create_session(self, *, session_id, memory_policy=None):
        self.created_sessions.append((session_id, memory_policy))

    async def batch_add_messages(self, session_id, messages):
        self.messages.setdefault(session_id, []).extend(messages)

    async def commit_session(self, session_id, telemetry=False, *, keep_recent_count=0):
        self.committed_sessions.append((session_id, keep_recent_count, telemetry))
        return {
            "task_id": f"task-{session_id}",
            "archive_uri": f"viking://user/default/sessions/{session_id}/history/archive_001",
            "trace_id": f"trace-{session_id}",
        }

    async def get_task(self, task_id):
        self.task_poll_counts[task_id] = self.task_poll_counts.get(task_id, 0) + 1
        return {"task_id": task_id, "status": "completed", "result": {}}


@pytest.mark.asyncio
async def test_session_commit_policy_trainer_records_commit_trace_id():
    from openviking.session.train import SessionCommitPolicyTrainer

    client = FakeSessionCommitClient()
    trainer = SessionCommitPolicyTrainer(
        client=client,
        run_id="run1",
        keep_recent_count=2,
        poll_interval_seconds=0.01,
    )
    case = _case()
    rollout = Rollout(
        case=case,
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
        metadata={"data_split": "unit", "task_no": 7, "execution_metadata": {"epoch": 3}},
    )

    result = await trainer.train_rollouts([rollout], _policy_set())

    commit_result = result.apply_result.metadata["commit_results"][0]
    assert commit_result["task_id"] == f"task-{commit_result['session_id']}"
    assert commit_result["archive_uri"].endswith("/history/archive_001")
    assert commit_result["trace_id"] == f"trace-{commit_result['session_id']}"
    assert commit_result["task_status"] == "completed"
    assert client.committed_sessions == [(commit_result["session_id"], 2, True)]
    assert client.created_sessions == [
        (
            commit_result["session_id"],
            {
                "memory_types": ["cases", "trajectories", "experiences"],
                "working_memory": {"enabled": False},
            },
        )
    ]


@pytest.mark.asyncio
async def test_session_commit_policy_trainer_splits_large_message_batches():
    from openviking.session.train import SessionCommitPolicyTrainer

    client = FakeSessionCommitClient()
    batch_sizes = []

    async def batch_add_messages(session_id, messages):
        batch_sizes.append(len(messages))
        client.messages.setdefault(session_id, []).extend(messages)

    client.batch_add_messages = batch_add_messages
    trainer = SessionCommitPolicyTrainer(
        client=client,
        run_id="run1",
        poll_interval_seconds=0.01,
    )
    rollout = Rollout(
        case=_case(),
        messages=[
            Message(id=f"m{i}", role="user", parts=[TextPart(text=f"hello {i}")])
            for i in range(250)
        ],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
        metadata={"data_split": "unit", "task_no": 7, "execution_metadata": {"epoch": 3}},
    )

    result = await trainer.train_rollouts([rollout], _policy_set())

    commit_result = result.apply_result.metadata["commit_results"][0]
    assert commit_result["error"] is None
    assert batch_sizes == [100, 100, 52]
    assert len(client.messages[commit_result["session_id"]]) == 252


@pytest.mark.asyncio
async def test_session_commit_policy_trainer_streams_commit_trace_events(tmp_path):
    from openviking.session.train import JsonlEventRecorder, SessionCommitPolicyTrainer

    client = FakeSessionCommitClient()
    events_path = tmp_path / "events.jsonl"
    trainer = SessionCommitPolicyTrainer(
        client=client,
        run_id="run1",
        poll_interval_seconds=0.01,
        event_recorder=JsonlEventRecorder(
            events_path,
            default_fields={"dataset": "unit", "domain": "booking", "run_id": "run1"},
        ),
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
        metadata={"data_split": "unit", "task_no": 7, "execution_metadata": {"epoch": 3}},
    )

    result = await trainer.train_rollouts([rollout], _policy_set())

    lines = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert [line["event"] for line in lines] == ["train_commit_submitted", "train_commit_done"]
    commit_result = result.apply_result.metadata["commit_results"][0]
    assert lines[0]["trace_id"] == commit_result["trace_id"]
    assert lines[1]["trace_id"] == commit_result["trace_id"]
    assert lines[1]["task_status"] == "completed"
    assert lines[1]["session_id"] == commit_result["session_id"]
    assert lines[1]["task_no"] == 7
    assert lines[1]["dataset"] == "unit"


@pytest.mark.asyncio
async def test_jsonl_pipeline_event_hook_omits_full_commit_results(tmp_path):
    from openviking.session.train import JsonlEventRecorder, JsonlPipelineEventHook

    events_path = tmp_path / "pipeline.jsonl"
    hook = JsonlPipelineEventHook(JsonlEventRecorder(events_path))
    await hook.on_train_report(
        report={
            "epoch": 0,
            "committed_rollout_count": 1,
            "errors": [],
            "commit_results": [
                {"trace_id": "trace-1", "task_id": "task-1", "telemetry_id": "telemetry-1"}
            ],
        },
        context=PipelineContext(execution_metadata={"epoch": 0, "training": True}),
    )

    data = json.loads(events_path.read_text())
    assert data["event"] == "train_result"
    assert data["commit_trace_ids"] == ["trace-1"]
    assert data["commit_task_ids"] == ["task-1"]
    assert "commit_results" not in data


@pytest.mark.asyncio
async def test_rollout_artifact_recorder_writes_train_rollouts_before_commit(tmp_path):
    from openviking.session.train import RolloutArtifactRecorder

    recorder = RolloutArtifactRecorder(run_dir=tmp_path)
    case = Case(
        name="tau2_airline_train_7",
        task_signature="tau2:airline:train:7",
        input={
            "data_split": "airline_train",
            "task_no": 7,
            "task_id": "task-7",
            "user_request": "change my seat",
        },
        rubric=Rubric(name="reward", description="reward", criteria=[]),
    )
    rollout = Rollout(
        case=case,
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=False, score=0.0, criterion_results=[], feedback=[]),
        metadata={
            "memory": "remember airline seat-change rules",
            "task_case_experience_skill": "# task_case_experience\nlinked exp content",
        },
    )

    recorder.on_train_rollout_end(
        epoch=0,
        rollouts=[rollout],
        snapshot_id="snapshot-1",
        policy_set=None,
        context=None,
    )

    rollout_dir = (
        tmp_path
        / "rollouts"
        / "airline_train_task_7_task-7"
        / "epoch_0"
        / "1.train_rollout"
        / "trial_0"
    )
    assert (rollout_dir / "status.json").exists()
    assert (rollout_dir / "rollout.json").exists()
    assert (rollout_dir / "evaluation.json").exists()
    assert (rollout_dir / "prompt_for_llm.md").exists()
    assert (rollout_dir / "memory_context.md").read_text() == "remember airline seat-change rules"
    skill_path = rollout_dir / "task_case_experience_skill.md"
    assert skill_path.read_text() == "# task_case_experience\nlinked exp content"
    assert (rollout_dir / "commit_messages.json").exists()
    assert not (rollout_dir / "commit_result.json").exists()
    status = json.loads((rollout_dir / "status.json").read_text())
    assert status["artifact_state"] == "rollout_done"
    assert status["has_task_case_experience_skill"] is True
    assert status["task_case_experience_skill_path"] == "task_case_experience_skill.md"
    index = json.loads((tmp_path / "rollouts_index.json").read_text())
    assert index["case_groups"][0]["rollouts"][0]["artifact_state"] == "rollout_done"


def test_rollout_artifact_recorder_separates_epoch_eval_dirs(tmp_path):
    from openviking.session.train import RolloutArtifactRecorder

    recorder = RolloutArtifactRecorder(run_dir=tmp_path)
    case = Case(
        name="tau2_airline_test_0_t0",
        task_signature="tau2:airline:test:2:trial:0",
        input={
            "data_split": "airline_test",
            "task_no": 0,
            "task_id": "2",
            "eval_trial": 0,
            "original_case_name": "tau2_airline_test_0",
        },
        rubric=Rubric(name="reward", description="reward", criteria=[]),
    )

    for epoch in (0, 1):
        rollout = Rollout(
            case=case,
            messages=[Message(id=f"m{epoch}", role="user", parts=[TextPart(text="hello")])],
            policy_snapshot_id=f"snapshot-{epoch}",
            evaluation=RubricEvaluation(
                passed=epoch == 1,
                score=float(epoch == 1),
                criterion_results=[],
                feedback=[],
            ),
        )
        recorder.record_eval(
            label="test_rollout",
            epoch=epoch,
            analyses=[
                RolloutAnalysis(
                    evaluation=rollout.evaluation,
                    trajectories=[],
                    metadata={"rollout": rollout},
                )
            ],
        )

    group_dir = tmp_path / "rollouts" / "airline_test_task_0_2"
    assert (group_dir / "epoch_0" / "4.test_rollout" / "trial_0" / "status.json").exists()
    assert (group_dir / "epoch_1" / "4.test_rollout" / "trial_0" / "status.json").exists()
    assert not (group_dir / "4.test_rollout" / "trial_0").exists()

    index = recorder.finalize().to_dict()
    rollout_stages = [item["stage"] for item in index["case_groups"][0]["rollouts"]]
    assert rollout_stages == ["epoch_0/4.test_rollout", "epoch_1/4.test_rollout"]


def test_rollout_artifact_recorder_uses_stage_name_dirs(tmp_path):
    from openviking.session.train import RolloutArtifactRecorder
    from openviking.session.train.context import ExecutionContext

    recorder = RolloutArtifactRecorder(run_dir=tmp_path)
    case = Case(
        name="tau2_airline_train_7",
        task_signature="tau2:airline:train:7",
        input={
            "data_split": "airline_train",
            "task_no": 7,
            "task_id": "task-7",
        },
        rubric=Rubric(name="reward", description="reward", criteria=[]),
    )
    rollout = Rollout(
        case=case,
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
    )

    recorder.record_rollout_completion(
        rollout=rollout,
        index=0,
        context=ExecutionContext(
            policy_snapshot_id="snapshot-1",
            metadata={"epoch": 0, "training": True, "rollout_stage": "eval_train_rollout"},
        ),
    )

    group_dir = tmp_path / "rollouts" / "airline_train_task_7_task-7"
    assert (group_dir / "epoch_0" / "3.eval_train_rollout" / "trial_0" / "status.json").exists()
    assert not (group_dir / "epoch_0" / "2.train" / "trial_0").exists()

    index = recorder.finalize().to_dict()
    rollout_index = index["case_groups"][0]["rollouts"][0]
    assert rollout_index["stage"] == "epoch_0/3.eval_train_rollout"
    assert rollout_index["path"].endswith("epoch_0/3.eval_train_rollout/trial_0")


def test_rollout_artifact_recorder_keeps_baseline_and_final_eval_dirs(tmp_path):
    from openviking.session.train import RolloutArtifactRecorder

    recorder = RolloutArtifactRecorder(run_dir=tmp_path)
    case = Case(
        name="tau2_airline_test_0_t0",
        task_signature="tau2:airline:test:2:trial:0",
        input={
            "data_split": "airline_test",
            "task_no": 0,
            "task_id": "2",
            "eval_trial": 0,
        },
        rubric=Rubric(name="reward", description="reward", criteria=[]),
    )

    rollout = Rollout(
        case=case,
        messages=[Message(id="m", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
    )
    analysis = RolloutAnalysis(
        evaluation=rollout.evaluation,
        trajectories=[],
        metadata={"rollout": rollout},
    )

    recorder.record_eval(label="baseline_test_rollout", epoch=-1, analyses=[analysis])
    recorder.record_eval(label="final_test_rollout", epoch=2, analyses=[analysis])

    group_dir = tmp_path / "rollouts" / "airline_test_task_0_2"
    assert (group_dir / "epoch_-1" / "0.baseline_test_rollout" / "trial_0" / "status.json").exists()
    assert (group_dir / "epoch_2" / "5.final_test_rollout" / "trial_0" / "status.json").exists()


def test_console_reporter_highlights_accuracy_and_prints_epoch_summary(capsys):
    reporter = ConsolePipelineReporter(use_rich=False)
    context = PipelineContext(eval_each_epoch_case_loader=object())

    reporter.on_train_rollout_report(
        report={
            "epoch": 1,
            "case_count": 30,
            "accuracy": 0.6,
            "passed_count": 18,
            "average_reward": 0.6,
        },
        context=context,
    )
    reporter.on_train_report(
        report={
            "epoch": 1,
            "committed_rollout_count": 30,
            "errors": [],
            "train_rollout": {
                "epoch": 1,
                "case_count": 30,
                "accuracy": 0.6,
                "passed_count": 18,
                "average_reward": 0.6,
            },
        },
        context=context,
    )
    reporter.on_eval_report(
        label="test_rollout",
        report={
            "epoch": 1,
            "rollout_stage": "test_rollout",
            "split": "test",
            "trial_count": 8,
            "case_count": 160,
            "total_rollout_count": 160,
            "case_count_per_trial": 20,
            "accuracy_mean": 0.58125,
            "accuracy_std": 0.055551,
            "average_reward_mean": 0.58125,
            "average_reward_std": 0.055551,
        },
        context=context,
    )

    output = capsys.readouterr().out

    assert "accuracy=\x1b[1;33m60.00%\x1b[0m" in output
    assert "epoch 1 summary" in output
    assert "TRAIN accuracy: \x1b[0m\x1b[1;33m60.00%\x1b[0m" in output
    assert "EVAL  accuracy: \x1b[0m\x1b[1;33m58.13%\x1b[0m" in output
    assert output.count("------------------------------------------------------------") >= 3


def test_rollout_artifact_event_recorder_enriches_commit_result(tmp_path):
    from openviking.session.train import RolloutArtifactEventRecorder, RolloutArtifactRecorder

    recorder = RolloutArtifactRecorder(run_dir=tmp_path)
    case = Case(
        name="tau2_airline_train_7",
        task_signature="tau2:airline:train:7",
        input={
            "data_split": "airline_train",
            "task_no": 7,
            "task_id": "task-7",
            "user_request": "change my seat",
        },
        rubric=Rubric(name="reward", description="reward", criteria=[]),
    )
    rollout = Rollout(
        case=case,
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
    )
    recorder.record_train_rollouts(epoch=0, rollouts=[rollout])
    event_recorder = RolloutArtifactEventRecorder(recorder)

    event_recorder.record(
        "train_commit_submitted",
        index=0,
        epoch=0,
        split="airline_train",
        task_no=7,
        case_task_id="task-7",
        case_name="tau2_airline_train_7",
        session_id="session-1",
        stage="commit_session",
        task_id="commit-task-1",
        archive_uri="viking://archive",
        trace_id="trace-1",
        telemetry_id="telemetry-1",
        task_status=None,
        score=1.0,
        error=None,
    )

    rollout_dir = (
        tmp_path
        / "rollouts"
        / "airline_train_task_7_task-7"
        / "epoch_0"
        / "1.train_rollout"
        / "trial_0"
    )
    commit_dir = (
        tmp_path
        / "rollouts"
        / "airline_train_task_7_task-7"
        / "epoch_0"
        / "2.train"
        / "trial_0"
    )
    assert not (rollout_dir / "commit_result.json").exists()
    commit_result = json.loads((commit_dir / "commit_result.json").read_text())
    status = json.loads((rollout_dir / "status.json").read_text())
    index = json.loads((tmp_path / "rollouts_index.json").read_text())
    assert commit_result["artifact_state"] == "commit_submitted"
    assert commit_result["session_id"] == "session-1"
    assert status["artifact_state"] == "commit_submitted"
    assert status["archive_uri"] == "viking://archive"
    assert status["commit_path"] == str(commit_dir)
    rollout_index = index["case_groups"][0]["rollouts"][0]
    assert rollout_index["artifact_state"] == "commit_submitted"
    assert rollout_index["path"] == str(rollout_dir)
    assert rollout_index["commit_path"] == str(commit_dir)


@pytest.mark.asyncio
async def test_rollout_artifact_recorder_writes_epoch_commit_artifacts_under_commit_dir(tmp_path):
    from openviking.session.train import RolloutArtifactRecorder

    class CommitArtifactClient:
        async def read(self, uri):
            assert uri == "viking://archive/memory_diff.json"
            return json.dumps({
                "operations": {
                    "adds": [
                        {"uri": "viking://memory/new.md", "after": "# New\nbody"}
                    ],
                    "updates": [
                        {
                            "uri": "viking://memory/old.md",
                            "before": "# Old\nbody",
                            "after": "# Old\nnew body",
                        }
                    ],
                    "deletes": [],
                },
                "summary": {"total_adds": 1, "total_updates": 1, "total_deletes": 0},
            })

    recorder = RolloutArtifactRecorder(run_dir=tmp_path, client=CommitArtifactClient())
    case = Case(
        name="tau2_airline_train_7",
        task_signature="tau2:airline:train:7",
        input={
            "data_split": "airline_train",
            "task_no": 7,
            "task_id": "task-7",
            "user_request": "change my seat",
        },
        rubric=Rubric(name="reward", description="reward", criteria=[]),
    )
    rollout = Rollout(
        case=case,
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
    )
    analysis = RolloutAnalysis(
        evaluation=rollout.evaluation,
        trajectories=[],
        metadata={"rollout": rollout},
    )

    await recorder.record_train_epoch(
        epoch=0,
        analyses=[analysis],
        commit_results=[
            {
                "index": 0,
                "archive_uri": "viking://archive",
                "task_status": "completed",
                "error": None,
            }
        ],
    )

    train_dir = (
        tmp_path
        / "rollouts"
        / "airline_train_task_7_task-7"
        / "epoch_0"
        / "1.train_rollout"
        / "trial_0"
    )
    commit_dir = (
        tmp_path
        / "rollouts"
        / "airline_train_task_7_task-7"
        / "epoch_0"
        / "2.train"
        / "trial_0"
    )
    assert (train_dir / "status.json").exists()
    assert not (train_dir / "commit_result.json").exists()
    assert not (train_dir / "memory_diff.json").exists()
    assert (commit_dir / "commit_result.json").exists()
    memory_diff_json = json.loads((commit_dir / "memory_diff.json").read_text())
    assert memory_diff_json["summary"]["total_adds"] == 1
    memory_diff_md = (commit_dir / "memory_diff.md").read_text()
    assert "--- /dev/null" in memory_diff_md
    assert "+++ viking://memory/new.md" in memory_diff_md
    assert "--- viking://memory/old.md" in memory_diff_md
    assert "+new body" in memory_diff_md

    status = json.loads((train_dir / "status.json").read_text())
    assert status["artifact_state"] == "memory_diff_done"
    assert status["commit_path"] == str(commit_dir)
    assert status["memory_diff_path"] == str(commit_dir / "memory_diff.json")
    assert status["memory_diff_markdown_path"] == str(commit_dir / "memory_diff.md")


class DelayedSessionCommitClient(FakeSessionCommitClient):
    def __init__(self, *, pending_polls: int):
        super().__init__()
        self.pending_polls = pending_polls

    async def get_task(self, task_id):
        self.task_poll_counts[task_id] = self.task_poll_counts.get(task_id, 0) + 1
        if self.task_poll_counts[task_id] <= self.pending_polls:
            return {"task_id": task_id, "status": "running", "result": {}}
        return {"task_id": task_id, "status": "completed", "result": {}}


@pytest.mark.asyncio
async def test_session_commit_policy_trainer_waits_without_default_timeout():
    from openviking.session.train import SessionCommitPolicyTrainer

    client = DelayedSessionCommitClient(pending_polls=3)
    trainer = SessionCommitPolicyTrainer(
        client=client,
        run_id="run1",
        poll_interval_seconds=0.01,
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
        metadata={"data_split": "unit", "task_no": 7, "execution_metadata": {"epoch": 3}},
    )

    result = await asyncio.wait_for(trainer.train_rollouts([rollout], _policy_set()), timeout=1.0)

    commit_result = result.apply_result.metadata["commit_results"][0]
    assert commit_result["task_status"] == "completed"
    assert commit_result["error"] is None
    assert client.task_poll_counts[commit_result["task_id"]] == 4


@pytest.mark.asyncio
async def test_session_commit_policy_trainer_can_still_use_explicit_timeout():
    from openviking.session.train import SessionCommitPolicyTrainer

    client = DelayedSessionCommitClient(pending_polls=100)
    trainer = SessionCommitPolicyTrainer(
        client=client,
        run_id="run1",
        poll_interval_seconds=0.01,
        timeout_seconds=0.02,
    )
    rollout = Rollout(
        case=_case(),
        messages=[Message(id="m1", role="user", parts=[TextPart(text="hello")])],
        policy_snapshot_id="snapshot-1",
        evaluation=RubricEvaluation(passed=True, score=1.0, criterion_results=[], feedback=[]),
        metadata={"data_split": "unit", "task_no": 7, "execution_metadata": {"epoch": 3}},
    )

    result = await trainer.train_rollouts([rollout], _policy_set())

    commit_result = result.apply_result.metadata["commit_results"][0]
    assert commit_result["task_status"] == "timeout"
    assert commit_result["error"] == "commit task timeout"


def test_tau2_case_loader_selects_exact_task_indices(tmp_path: Path, monkeypatch):
    from benchmark.tau2.train import case_loader as tau2_case_loader

    domain_dir = tmp_path / "domains" / "airline"
    domain_dir.mkdir(parents=True)
    (domain_dir / "split_tasks.json").write_text(
        json.dumps({"train": ["a", "b", "c"], "test": ["x", "y"]}),
        encoding="utf-8",
    )

    class Task:
        def __init__(self, task_id: str) -> None:
            self.id = task_id
            self.evaluation_criteria = f"criteria-{task_id}"
            self.user_scenario = f"scenario-{task_id}"

    monkeypatch.setattr(tau2_case_loader, "_load_tau2_task", lambda _domain, task_id: Task(task_id))

    loader = tau2_case_loader.Tau2CaseLoader(
        domain="airline",
        split="train",
        data_root=str(tmp_path),
        task_indices=[1],
    )

    cases = loader.load_cases()

    assert [case.input["task_id"] for case in cases] == ["b"]
    assert [case.input["task_no"] for case in cases] == [1]
    assert cases[0].name == "tau2_airline_train_1"


def test_tau2_service_filter_parses_task_indices():
    from benchmark.tau2.train.service_app import _task_indices_from_filters

    assert _task_indices_from_filters({}) is None
    assert _task_indices_from_filters({"task_indices": [0, "3"]}) == [0, 3]
    with pytest.raises(ValueError, match="task_indices filter must be a list"):
        _task_indices_from_filters({"task_indices": 2})
    with pytest.raises(ValueError, match="task index must be >= 0"):
        _task_indices_from_filters({"task_indices": [-1]})
