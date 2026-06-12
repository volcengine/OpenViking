# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session training framework for trajectory/experience policy optimization."""

from openviking.session.train.components.case_loader import ListCaseLoader
from openviking.session.train.components.gradient_estimator import (
    ExperienceGradientContext,
    ExperienceGradientEstimator,
)
from openviking.session.train.components.memory_store import ExperienceSetLoader
from openviking.session.train.components.policy_optimizer import (
    PatchMergePolicyOptimizer,
    PatchMergePolicyOptimizerContext,
)
from openviking.session.train.components.policy_trainer import (
    BatchPolicyTrainer,
    StreamingPolicyTrainer,
    StreamingPolicyTrainerConfig,
    StreamingPolicyTrainerKey,
    get_streaming_policy_trainer,
    make_streaming_policy_trainer_key,
)
from openviking.session.train.components.policy_updater import (
    DryRunPolicyUpdater,
    MemoryFilePolicyUpdater,
)
from openviking.session.train.components.rollout_executor import (
    SingleTurnLLMRolloutExecutor,
    default_single_turn_prompt,
)
from openviking.session.train.components.session_commit import SessionCommitPolicyTrainer
from openviking.session.train.components.snapshotter import ContentHashPolicySnapshotter
from openviking.session.train.components.trajectory_analyzer import (
    TrajectoryAnalyzerContext,
    TrajectoryRolloutAnalyzer,
)
from openviking.session.train.context import ExecutionContext, PipelineContext
from openviking.session.train.domain import (
    Case,
    CriterionResult,
    Experience,
    ExperienceSet,
    PipelineEpochResult,
    PipelineEvaluationResult,
    PipelineResult,
    PolicyApplyResult,
    PolicyPlanItem,
    PolicyPlanItemKind,
    PolicyStatus,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
    Trajectory,
    TrajectoryOutcome,
)
from openviking.session.train.gradients import PatchSemanticGradient
from openviking.session.train.interfaces import (
    CaseLoader,
    GradientEstimator,
    PolicyOptimizationPipeline,
    PolicyOptimizer,
    PolicySnapshotter,
    PolicyTrainer,
    PolicyUpdater,
    RolloutAnalyzer,
    RolloutEvaluator,
    RolloutExecutor,
    SemanticGradient,
)
from openviking.session.train.pipeline import OfflinePolicyOptimizationPipeline

__all__ = [
    "make_streaming_policy_trainer_key",
    "get_streaming_policy_trainer",
    "StreamingPolicyTrainerKey",
    "StreamingPolicyTrainerConfig",
    "StreamingPolicyTrainer",
    "BatchPolicyTrainer",
    "ExperienceGradientEstimator",
    "ExperienceGradientContext",
    "TrajectoryRolloutAnalyzer",
    "TrajectoryAnalyzerContext",
    "PatchMergePolicyOptimizer",
    "PatchMergePolicyOptimizerContext",
    "PolicyTrainer",
    "SessionCommitPolicyTrainer",
    "ExperienceSetLoader",
    "DryRunPolicyUpdater",
    "MemoryFilePolicyUpdater",
    "SingleTurnLLMRolloutExecutor",
    "default_single_turn_prompt",
    "ContentHashPolicySnapshotter",
    "PolicyApplyResult",
    "Case",
    "CaseLoader",
    "CriterionResult",
    "OfflinePolicyOptimizationPipeline",
    "ExecutionContext",
    "Experience",
    "ExperienceSet",
    "GradientEstimator",
    "ListCaseLoader",
    "PatchSemanticGradient",
    "PipelineContext",
    "PipelineEvaluationResult",
    "PipelineEpochResult",
    "PipelineResult",
    "PolicyPlanItem",
    "PolicyPlanItemKind",
    "PolicyOptimizationPipeline",
    "PolicyOptimizer",
    "PolicySnapshotter",
    "PolicyStatus",
    "PolicyUpdatePlan",
    "PolicyUpdater",
    "Rollout",
    "RolloutAnalysis",
    "RolloutAnalyzer",
    "RolloutExecutor",
    "RolloutEvaluator",
    "RolloutTrainingResult",
    "Rubric",
    "RubricCriterion",
    "RubricEvaluation",
    "SemanticGradient",
    "Trajectory",
    "TrajectoryOutcome",
]
