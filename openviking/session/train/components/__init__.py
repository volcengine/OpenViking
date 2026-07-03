# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Default replaceable components for the session train framework."""

from openviking.session.train.components.case_loader import ListCaseLoader
from openviking.session.train.components.gradient_estimator import (
    ExperienceGradientContext,
    ExperienceGradientEstimator,
)
from openviking.session.train.components.memory_store import ExperienceSetLoader, SkillSetLoader
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
from openviking.session.train.components.remote import RemoteCaseLoader, RemoteRolloutExecutor
from openviking.session.train.components.rollout_executor import (
    SingleTurnLLMRolloutExecutor,
    default_single_turn_prompt,
)
from openviking.session.train.components.session_commit import SessionCommitPolicyTrainer
from openviking.session.train.components.skill_policy_updater import SkillPolicyUpdater
from openviking.session.train.components.snapshotter import ContentHashPolicySnapshotter
from openviking.session.train.components.trajectory_analyzer import (
    TrajectoryAnalyzerContext,
    TrajectoryRolloutAnalyzer,
)

__all__ = [
    "ContentHashPolicySnapshotter",
    "make_streaming_policy_trainer_key",
    "get_streaming_policy_trainer",
    "StreamingPolicyTrainerKey",
    "StreamingPolicyTrainerConfig",
    "StreamingPolicyTrainer",
    "BatchPolicyTrainer",
    "PatchMergePolicyOptimizerContext",
    "PatchMergePolicyOptimizer",
    "ListCaseLoader",
    "ExperienceGradientEstimator",
    "ExperienceGradientContext",
    "TrajectoryRolloutAnalyzer",
    "TrajectoryAnalyzerContext",
    "DryRunPolicyUpdater",
    "MemoryFilePolicyUpdater",
    "SingleTurnLLMRolloutExecutor",
    "default_single_turn_prompt",
    "ExperienceSetLoader",
    "SkillSetLoader",
    "SkillPolicyUpdater",
    "SessionCommitPolicyTrainer",
    "RemoteRolloutExecutor",
    "RemoteCaseLoader",
]
