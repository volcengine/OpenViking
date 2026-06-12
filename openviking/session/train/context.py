# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Context models for session policy training pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


@dataclass(slots=True)
class ExecutionContext:
    """Runtime context passed to RolloutExecutor."""

    policy_snapshot_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
