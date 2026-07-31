# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Context models for session policy training pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openviking.session.train.components.report_builder import PipelineReportHook
from openviking.session.train.components.reporter import ConsolePipelineReporter

if TYPE_CHECKING:
    from openviking.session.train.components.report_builder import PipelineReportBuilder
    from openviking.session.train.components.reporter import PipelineLifecycleHook
    from openviking.session.train.interfaces import CaseLoader


@dataclass(slots=True)
class PipelineHookDecision:
    """Control decision returned by lifecycle hooks."""

    stop_training: bool = False
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] | None = None


@dataclass(slots=True)
class PipelineContext:
    """Context bundle for OfflinePolicyOptimizationPipeline.

    Context payloads are intentionally opaque and can be shaped by concrete
    implementations without changing the domain interfaces.
    """

    case_load_context: Any = None
    snapshot_context: Any = None
    analysis_context: Any = None
    gradient_context: Any = None
    optimization_context: Any = None
    apply_context: Any = None
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    max_epochs: int = 1
    eval_each_epoch_case_loader: CaseLoader | None = None
    eval_trials: int = 1
    train_trials: int = 1
    trial_index_key: str = "trial"
    report_builder: PipelineReportBuilder | None = None
    lifecycle_hooks: list[PipelineLifecycleHook] = field(
        default_factory=lambda: [PipelineReportHook(), ConsolePipelineReporter()]
    )


@dataclass(slots=True)
class ExecutionContext:
    """Runtime context passed to RolloutExecutor."""

    policy_snapshot_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
