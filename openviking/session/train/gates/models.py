# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared types for policy training gates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from openviking.session.train.domain import PolicyPlanItem, PolicySet, RolloutAnalysis, Trajectory
from openviking.session.train.interfaces import SemanticGradient

GateStage = Literal["post_gradient", "post_plan"]


GateMode = Literal["enforce", "warn", "shadow"]


GateAction = Literal["allow", "warn", "reject"]


@dataclass(slots=True)
class GateDecision:
    gate_name: str
    action: GateAction
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    retriable: bool = False
    repair_prompt: str = ""


@dataclass(slots=True)
class GateReport:
    stage: GateStage
    evaluated_count: int = 0
    allowed_count: int = 0
    rejected_count: int = 0
    warning_count: int = 0
    decisions: list[GateDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "evaluated_count": self.evaluated_count,
            "allowed_count": self.allowed_count,
            "rejected_count": self.rejected_count,
            "warning_count": self.warning_count,
            "decisions": [
                {
                    "gate_name": decision.gate_name,
                    "action": decision.action,
                    "reason": decision.reason,
                    "evidence": dict(decision.evidence),
                    "retriable": decision.retriable,
                    "repair_prompt": decision.repair_prompt,
                }
                for decision in self.decisions
            ],
        }

    def has_non_retriable_rejection(self) -> bool:
        return any(
            decision.action == "reject" and not decision.retriable for decision in self.decisions
        )

    def retry_repair_prompt(self) -> str:
        blocked_targets = {
            _decision_target_name(decision)
            for decision in self.decisions
            if decision.action == "reject" and not decision.retriable
        }
        parts: list[str] = []
        for decision in self.decisions:
            if decision.action != "reject" or not decision.retriable or not decision.repair_prompt:
                continue
            target = _decision_target_name(decision)
            if target in blocked_targets:
                continue
            diagnostics = {
                key: value for key, value in decision.evidence.items() if key != "target_name"
            }
            diagnostic_text = json.dumps(
                diagnostics,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            parts.append(
                f"- [{decision.gate_name}] target={target}: {decision.reason}\n"
                f"  Observed diagnostics: {diagnostic_text}\n"
                f"  Required repair: {decision.repair_prompt}"
            )
        if not parts:
            return ""
        return "\n".join(parts)

    def retriable_rejected_targets(self) -> list[str]:
        blocked_targets = {
            _decision_target_name(decision)
            for decision in self.decisions
            if decision.action == "reject" and not decision.retriable
        }
        return list(
            dict.fromkeys(
                _decision_target_name(decision)
                for decision in self.decisions
                if decision.action == "reject"
                and decision.retriable
                and _decision_target_name(decision) not in blocked_targets
            )
        )


@dataclass(slots=True)
class GateEvaluation:
    allowed: bool
    decisions: list[GateDecision] = field(default_factory=list)


@dataclass(slots=True)
class GateTarget:
    stage: GateStage
    memory_type: str
    target_kind: Literal["gradient", "plan_item"]
    gradient: SemanticGradient | None = None
    plan_item: PolicyPlanItem | None = None
    analysis: RolloutAnalysis | None = None
    trajectory: Trajectory | None = None
    policy_set: PolicySet | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def target_name(self) -> str:
        if self.gradient is not None:
            return self.gradient.target_name
        if self.plan_item is not None:
            return self.plan_item.target_name
        return "unknown_policy"

    @property
    def before_content(self) -> str | None:
        if self.gradient is not None:
            before_file = getattr(self.gradient, "before_file", None)
            return before_file.plain_content() if before_file is not None else None
        if self.plan_item is not None:
            return self.plan_item.before_content
        return None

    @property
    def after_content(self) -> str:
        if self.gradient is not None:
            after_file = getattr(self.gradient, "after_file", None)
            return after_file.plain_content() if after_file is not None else ""
        if self.plan_item is not None:
            return self.plan_item.after_content or ""
        return ""


class PolicyGate(Protocol):
    name: str
    mode: GateMode

    def applies_to(self, target: GateTarget) -> bool: ...

    async def evaluate(self, target: GateTarget) -> GateDecision | None: ...


def _decision_target_name(decision: GateDecision) -> str:
    return str(decision.evidence.get("target_name") or "unknown")
