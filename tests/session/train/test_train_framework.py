# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from test_fakes import InMemoryAGFS, fake_request_context

from openviking.message import Message, TextPart
from openviking.session.train import (
    Case,
    Experience,
    ExperienceSet,
    ListCaseLoader,
    OfflinePolicyOptimizationPipeline,
    PipelineContext,
    PolicyApplyResult,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
    Trajectory,
)
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
    target_experience_name: str
    target_experience_uri: str | None
    base_version: int | None
    rationale: str
    evidence_trajectory_uris: list[str]
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
                target_experience_name="booking_duplicate_handling",
                target_experience_uri=experience_set.policies[0].uri,
                base_version=experience_set.policies[0].version,
                rationale="trajectory succeeded",
                evidence_trajectory_uris=[traj.uri],
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
    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: ExperienceSet,
        context: Any,
    ) -> PolicyApplyResult:
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
    assert initial_policy_set.viking_fs.reloads == 1
    assert len(result.epochs) == 1
    assert result.epochs[0].epoch == 0
    assert result.epochs[0].policy_snapshot_ids == ["snapshot-1"]


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
    from openviking.session.train import StreamingPolicyTrainer, StreamingPolicyTrainerConfig

    trainer = StreamingPolicyTrainer(
        policy_set=_policy_set(),
        rollout_analyzer=DummyAnalyzer(),
        gradient_estimator=DummyEstimator(),
        policy_optimizer=DummyOptimizer(),
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
    assert first is second
    assert second.metadata["flush_reason"] == "count"
    assert second.metadata["gradient_count"] == 2
    assert second.apply_result.updated_policy_set.policies[0].version == 2
    assert await trainer.get_buffered_gradient_count() == 0
    assert trainer.last_apply_result is second.apply_result

    assert await trainer.close() is None
    assert trainer.closed is True


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
    assert await submit_task is result
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

    async def create_session(self, *, session_id, memory_policy=None):
        self.created_sessions.append((session_id, memory_policy))

    async def batch_add_messages(self, session_id, messages):
        self.messages[session_id] = messages

    async def commit_session(self, session_id, *, keep_recent_count=0):
        self.committed_sessions.append((session_id, keep_recent_count))
        return {"task_id": f"task-{session_id}", "trace_id": f"trace-{session_id}"}

    async def get_task(self, task_id):
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
    assert commit_result["trace_id"] == f"trace-{commit_result['session_id']}"
    assert commit_result["task_status"] == "completed"
    assert client.committed_sessions == [(commit_result["session_id"], 2)]
    assert client.created_sessions == [
        (
            commit_result["session_id"],
            {"memory_types": ["cases", "trajectories", "experiences"]},
        )
    ]
