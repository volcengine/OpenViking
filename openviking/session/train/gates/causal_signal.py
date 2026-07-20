# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperienceCausalSignalGate implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openviking.session.train.domain import Trajectory

from ._shared import _field_from_section, _first_match
from .models import GateDecision, GateMode, GateTarget


@dataclass(slots=True)
class ExperienceCausalSignalGate:
    mode: GateMode = "enforce"
    name: str = "experience_causal_signal"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.target_kind in {
            "gradient",
            "plan_item",
        }

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        signals = _source_signals(target)
        if not signals:
            merge_gradient_count = int(
                (getattr(target.plan_item, "metadata", {}) or {}).get("merge_gradient_count") or 0
            )
            if target.stage == "post_plan" and merge_gradient_count > 0:
                return GateDecision(
                    gate_name=self.name,
                    action="warn",
                    reason="source provenance could not be resolved after merge rename",
                    evidence={
                        "target_name": target.target_name,
                        "merge_gradient_count": merge_gradient_count,
                    },
                )
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="missing source trajectory repair signal",
                evidence={"target_name": target.target_name},
            )
        eligible = [signal for signal in signals if _signal_allows_experience_update(signal)]
        if eligible:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="no non-success source trajectory supports experience learning",
            evidence={
                "target_name": target.target_name,
                "signals": [signal.to_dict() for signal in signals],
            },
        )


@dataclass(slots=True)
class TrajectoryRepairSignal:
    uri: str
    outcome: str
    repair_action: str
    first_wrong_tool: str
    trigger_boundary: str
    selected_candidate: str = ""
    first_wrong_step: str = ""
    failure_kind: str = ""
    db_check_passed: bool | None = None
    action_checks_passed: bool | None = None
    communicate_checks_passed: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "outcome": self.outcome,
            "repair_action": self.repair_action,
            "first_wrong_tool": self.first_wrong_tool,
            "trigger_boundary": self.trigger_boundary,
            "selected_candidate": self.selected_candidate,
            "first_wrong_step": self.first_wrong_step,
            "failure_kind": self.failure_kind,
            "db_check_passed": self.db_check_passed,
            "action_checks_passed": self.action_checks_passed,
            "communicate_checks_passed": self.communicate_checks_passed,
        }


def _source_signals(target: GateTarget) -> list[TrajectoryRepairSignal]:
    trajectories: list[Trajectory] = []
    if target.trajectory is not None:
        trajectories.append(target.trajectory)
    elif target.analysis is not None:
        trajectories.extend(target.analysis.trajectories)
    signals = []
    for trajectory in trajectories:
        signals.append(parse_trajectory_repair_signal(trajectory))
    return signals


def parse_trajectory_repair_signal(trajectory: Trajectory) -> TrajectoryRepairSignal:
    content = trajectory.content or ""
    outcome = _first_match(content, r"(?mi)^-\s*Outcome:\s*(.+)$") or str(trajectory.outcome or "")
    first_section = _section(content, "First Wrong Tool Call")
    repair_section = _section(content, "Experience Repair Signal")
    runtime_section = _section(content, "Runtime Facts")
    first_tool = _field_from_section(first_section, "Tool")
    return TrajectoryRepairSignal(
        uri=trajectory.uri,
        outcome=_norm_token(outcome),
        repair_action=_repair_action_from_section(repair_section),
        first_wrong_tool=_norm_tool(first_tool),
        trigger_boundary=_norm_tool(_field_from_section(repair_section, "Trigger boundary")),
        selected_candidate=_norm_candidate(
            _first_match(content, r"(?mi)^\s*-\s*Selected candidate:\s*(.+)$")
        ),
        first_wrong_step=_field_from_section(first_section, "Step"),
        failure_kind=_field_from_section(first_section, "Error type"),
        db_check_passed=_bool_from_runtime(runtime_section, "db"),
        action_checks_passed=_bool_from_runtime(runtime_section, "action"),
        communicate_checks_passed=_bool_from_runtime(runtime_section, "communicate"),
    )


def _repair_action_from_section(repair_section: str) -> str:
    recommended = _norm_token(_field_from_section(repair_section, "Recommended operation"))
    if recommended:
        return recommended
    legacy = _norm_token(_field_from_section(repair_section, "Action"))
    if legacy:
        return legacy
    existing_action = _norm_token(_field_from_section(repair_section, "Existing experience action"))
    if existing_action == "update":
        return existing_action
    new_action = _norm_token(_field_from_section(repair_section, "New experience action"))
    if new_action == "create":
        return new_action
    return new_action or existing_action


def _signal_allows_experience_update(signal: TrajectoryRepairSignal) -> bool:
    if signal.outcome == "success":
        return False
    if signal.selected_candidate and signal.selected_candidate not in {"c1"}:
        return False
    # Non-success trajectories are learning candidates by default.  The analyzer's
    # Experience Repair Signal.Action is only advisory: failed trajectories may still
    # need a brand-new experience even when the trajectory writer emitted
    # Action=skip or Trigger boundary=none.
    return True


def _section(content: str, title: str) -> str:
    pattern = re.compile(
        rf"(?mi)^-\s*{re.escape(title)}:\s*$\n(?P<body>.*?)(?=^-[^\n]*:\s*(?:$|.)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(content or "")
    return match.group("body") if match else ""


def _norm_token(value: str) -> str:
    return str(value or "").strip().lower()


def _norm_tool(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    match = re.search(r"[a-zA-Z_][a-zA-Z0-9_]*", value)
    return match.group(0) if match else value.lower()


def _norm_candidate(value: str) -> str:
    value = str(value or "").strip().strip("` ").lower()
    if value in {"c1", "c2", "c3", "none"}:
        return value
    match = re.match(r"^(c[123]|none)\b", value)
    return match.group(1) if match else value


def _bool_from_runtime(section: str, key: str) -> bool | None:
    text = section or ""
    if key == "db":
        if re.search(r"db_(?:match|check)[^\n]*(?:true|passed)", text, re.I):
            return True
        if re.search(r"db_(?:match|check)[^\n]*(?:false|failed)", text, re.I):
            return False
    if key == "action":
        if re.search(r"action_checks?[^\n]*(?:true|passed|met)", text, re.I):
            return True
        if re.search(r"action_checks?[^\n]*(?:false|failed|missing)", text, re.I):
            return False
    if key == "communicate":
        if re.search(r"communicate_checks?[^\n]*(?:true|passed|met)", text, re.I):
            return True
        if re.search(r"communicate_checks?[^\n]*(?:false|failed|missing)", text, re.I):
            return False
    return None
