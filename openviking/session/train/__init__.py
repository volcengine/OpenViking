# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session training framework for trajectory/experience policy optimization."""

from openviking.session.train.adapters.gradient_estimator import (
    LegacyExperienceGradientContext,
    LegacyExperienceGradientEstimator,
)
from openviking.session.train.adapters.memory_store import ExperienceSetLoader
from openviking.session.train.adapters.policy_updater import (
    DryRunPolicyUpdater,
    MemoryFilePolicyUpdater,
)
from openviking.session.train.adapters.rollout_executor import (
    SingleTurnLLMRolloutExecutor,
    default_single_turn_prompt,
)
from openviking.session.train.adapters.trajectory_analyzer import (
    LegacyTrajectoryAnalyzerContext,
    LegacyTrajectoryRolloutAnalyzer,
)
from openviking.session.train.domain import (
    ApplyResult,
    Case,
    CriterionResult,
    ExecutionContext,
    Experience,
    ExperienceSet,
    PipelineEvaluationResult,
    PipelineIterationResult,
    PipelineResult,
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
from openviking.session.train.evaluators import (
    HeuristicRubricRolloutAnalyzer,
    LLMRubricRolloutAnalyzer,
)
from openviking.session.train.gradients import ExperienceContentPatch, PatchSemanticGradient
from openviking.session.train.interfaces import (
    CaseLoader,
    GradientEstimator,
    Policy,
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
    GroupingPolicyOptimizer,
    MergeAwarePolicyOptimizer,
    MergeAwarePolicyOptimizerContext,
)
from openviking.session.train.pipeline import DefaultPolicyOptimizationPipeline, PipelineContext
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
    "LegacyExperienceGradientEstimator",
    "LegacyExperienceGradientContext",
    "ExperienceContentPatch",
    "LegacyTrajectoryRolloutAnalyzer",
    "LegacyTrajectoryAnalyzerContext",
    "HeuristicRubricRolloutAnalyzer",
    "LLMRubricRolloutAnalyzer",
    "GroupingPolicyOptimizer",
    "MergeAwarePolicyOptimizer",
    "MergeAwarePolicyOptimizerContext",
    "ExperienceSetLoader",
    "DryRunPolicyUpdater",
    "MemoryFilePolicyUpdater",
    "SingleTurnLLMRolloutExecutor",
    "default_single_turn_prompt",
    "ContentHashPolicySnapshotter",
    "ApplyResult",
    "Case",
    "CaseLoader",
    "CriterionResult",
    "DefaultPolicyOptimizationPipeline",
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
    "Policy",
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
