# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session training framework for trajectory/experience policy optimization."""

from openviking.session.train.components.gradient_estimator import (
    ExperienceGradientContext,
    ExperienceGradientEstimator,
)
from openviking.session.train.components.memory_store import ExperienceSetLoader
from openviking.session.train.components.policy_updater import (
    DryRunPolicyUpdater,
    MemoryFilePolicyUpdater,
)
from openviking.session.train.components.rollout_executor import (
    SingleTurnLLMRolloutExecutor,
    default_single_turn_prompt,
)
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
    PipelineEvaluationResult,
    PipelineIterationResult,
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
    PolicyUpdater,
    RolloutAnalyzer,
    RolloutExecutor,
    SemanticGradient,
)
from openviking.session.train.loaders import ListCaseLoader
from openviking.session.train.optimizers import (
    PatchMergePolicyOptimizer,
    PatchMergePolicyOptimizerContext,
)
from openviking.session.train.pipeline import OfflinePolicyOptimizationPipeline
from openviking.session.train.snapshot import ContentHashPolicySnapshotter
from openviking.session.train.trainers import (
    BatchPolicyTrainer,
    StreamingPolicyTrainer,
    StreamingPolicyTrainerConfig,
    StreamingPolicyTrainerKey,
    get_streaming_policy_trainer,
    make_streaming_policy_trainer_key,
)

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
    "PipelineIterationResult",
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
    "RolloutTrainingResult",
    "Rubric",
    "RubricCriterion",
    "RubricEvaluation",
    "SemanticGradient",
    "Trajectory",
    "TrajectoryOutcome",
]
