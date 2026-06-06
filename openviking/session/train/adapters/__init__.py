# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Adapters that connect the session train framework to existing OpenViking components."""

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
from openviking.session.train.optimizers import (
    GroupingPolicyOptimizer,
    MergeAwarePolicyOptimizer,
    MergeAwarePolicyOptimizerContext,
)
from openviking.session.train.snapshot import ContentHashPolicySnapshotter

__all__ = [
    "LegacyExperienceGradientEstimator",
    "LegacyExperienceGradientContext",
    "LegacyTrajectoryRolloutAnalyzer",
    "LegacyTrajectoryAnalyzerContext",
    "ContentHashPolicySnapshotter",
    "DryRunPolicyUpdater",
    "MemoryFilePolicyUpdater",
    "SingleTurnLLMRolloutExecutor",
    "default_single_turn_prompt",
    "ExperienceSetLoader",
    "GroupingPolicyOptimizer",
    "MergeAwarePolicyOptimizer",
    "MergeAwarePolicyOptimizerContext",
]
