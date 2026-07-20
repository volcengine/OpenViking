# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Semantic quality gate for merged experience plans."""

from __future__ import annotations

from dataclasses import dataclass

from .models import GateTarget
from .root_cause_prevention import ExperienceRootCausePreventionGate


@dataclass(slots=True)
class ExperiencePlanQualityGate(ExperienceRootCausePreventionGate):
    """Re-run semantic quality review on the merged, final experience body."""

    name: str = "experience_plan_quality"
    max_policy_chars: int = 3500

    def applies_to(self, target: GateTarget) -> bool:
        return (
            target.stage == "post_plan"
            and target.memory_type == "experiences"
            and target.target_kind == "plan_item"
            and target.plan_item is not None
            and target.after_content.strip() != ""
            and target.trajectory is not None
            and bool(target.plan_item.metadata.get("plan_quality_review_required"))
        )
