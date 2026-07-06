# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy training gates.

Gates are lightweight, deterministic checks that run inside the train framework
before semantic gradients or planned policy updates are allowed to affect the
policy set.  They are intentionally framework-level rather than tied to one
extractor so future policy memory types can reuse the same interception points.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from openviking.session.memory.constraints.schema import _extract_rendered_constraint_metadata
from openviking.session.memory.constraints.trigger_sandbox import (
    TriggerSandboxError,
    smoke_test_trigger_code,
    validate_trigger_code,
)
from openviking.session.train.domain import PolicyPlanItem, PolicySet, RolloutAnalysis, Trajectory
from openviking.session.train.interfaces import SemanticGradient
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

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
        if self.has_non_retriable_rejection():
            return ""
        parts: list[str] = []
        for decision in self.decisions:
            if decision.action != "reject" or not decision.retriable or not decision.repair_prompt:
                continue
            target = decision.evidence.get("target_name") or "unknown"
            parts.append(
                f"- [{decision.gate_name}] target={target}: {decision.reason}\n"
                f"  Required repair: {decision.repair_prompt}"
            )
        if not parts:
            return ""
        return "\n".join(parts)


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
            before_file = self.gradient.before_file
            return before_file.plain_content() if before_file is not None else None
        if self.plan_item is not None:
            return self.plan_item.before_content
        return None

    @property
    def after_content(self) -> str:
        if self.gradient is not None:
            return self.gradient.after_file.plain_content()
        if self.plan_item is not None:
            return self.plan_item.after_content or ""
        return ""


class PolicyGate(Protocol):
    name: str
    mode: GateMode

    def applies_to(self, target: GateTarget) -> bool: ...

    async def evaluate(self, target: GateTarget) -> GateDecision | None: ...


@dataclass(slots=True)
class GateRunner:
    gates: list[PolicyGate]

    async def filter_gradients(
        self,
        gradients: list[SemanticGradient],
        *,
        analyses: list[RolloutAnalysis],
        policy_set: PolicySet,
    ) -> tuple[list[SemanticGradient], GateReport]:
        report = GateReport(stage="post_gradient")
        result: list[SemanticGradient] = []
        for gradient in gradients:
            target = _gradient_gate_target(
                gradient=gradient,
                analyses=analyses,
                policy_set=policy_set,
            )
            allowed, decisions = await self._evaluate_target(target)
            report.evaluated_count += 1
            report.decisions.extend(decisions)
            if allowed:
                report.allowed_count += 1
                result.append(gradient)
            else:
                report.rejected_count += 1
                _log_gate_rejection(target, decisions)
            report.warning_count += sum(1 for decision in decisions if decision.action == "warn")
        return result, report

    async def filter_plan(
        self,
        plan_items: list[PolicyPlanItem],
        *,
        analyses: list[RolloutAnalysis],
        policy_set: PolicySet,
    ) -> tuple[list[PolicyPlanItem], GateReport]:
        report = GateReport(stage="post_plan")
        result: list[PolicyPlanItem] = []
        for item in plan_items:
            target = _plan_item_gate_target(
                item=item,
                analyses=analyses,
                policy_set=policy_set,
            )
            allowed, decisions = await self._evaluate_target(target)
            report.evaluated_count += 1
            report.decisions.extend(decisions)
            if allowed:
                report.allowed_count += 1
                result.append(item)
            else:
                report.rejected_count += 1
                _log_gate_rejection(target, decisions)
            report.warning_count += sum(1 for decision in decisions if decision.action == "warn")
        return result, report

    async def _evaluate_target(self, target: GateTarget) -> tuple[bool, list[GateDecision]]:
        decisions: list[GateDecision] = []
        rejected = False
        for gate in self.gates:
            if not gate.applies_to(target):
                continue
            decision = await gate.evaluate(target)
            if decision is None:
                continue
            action = _effective_action(decision.action, gate.mode)
            if action != decision.action:
                decision = GateDecision(
                    gate_name=decision.gate_name,
                    action=action,
                    reason=decision.reason,
                    evidence={**decision.evidence, "configured_mode": gate.mode},
                    retriable=decision.retriable,
                    repair_prompt=decision.repair_prompt,
                )
            decisions.append(decision)
            if action == "reject":
                rejected = True
        return not rejected, decisions


def default_policy_gate_runner() -> GateRunner:
    """Default hard-coded gates used by session policy training."""

    return GateRunner(
        gates=[
            ExperienceContentFormatGate(mode="enforce"),
            ExperienceCausalSignalGate(mode="enforce"),
            ExperienceToolAlignmentGate(mode="enforce"),
        ]
    )


def default_experience_gate_contract() -> str:
    """Prompt-facing contract enforced by the default experience gates."""

    return """## Gate Contract (enforced)
Your experience output will be rejected unless every experience satisfies these gates:

1. Causal eligibility
- Output experiences only for trajectories whose Experience Repair Signal.Action is create/update.
- Do not output experiences for Outcome=success, Action=skip, First Wrong Tool Call.Tool=none, or Trigger boundary=none.

2. Content format
- Use exactly these headings in this order: ## Failure Pattern, ## Repair Procedure, ## Guardrails.
- Provide a valid trigger_code either in the rendered # Experience Trigger section or in schema metadata. The body may contain only the three required sections if trigger_code is stored separately.

3. Tool boundary alignment
- trigger_code must bind exactly one candidate_tool.
- The candidate_tool must match the trajectory's First Wrong Tool Call.Tool or Trigger boundary.
- Do not choose earlier setup tools, later recovery tools, or multi-tool workflow triggers.

If you cannot satisfy this contract, output no experience changes."""


def build_gate_retry_instruction(report: GateReport) -> str:
    repair = report.retry_repair_prompt()
    if not repair:
        return ""
    return "\n".join(
        [
            "Your previous experience output was rejected by training gates.",
            "Retry once. Only repair the rejected experience updates; do not add unrelated new experiences.",
            "If you cannot satisfy all gate requirements, output no experience changes.",
            "",
            "Gate repair instructions:",
            repair,
        ]
    )


@dataclass(slots=True)
class ExperienceContentFormatGate:
    mode: GateMode = "enforce"
    name: str = "experience_content_format"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content = target.after_content
        missing = [
            heading
            for heading in ("## Failure Pattern", "## Repair Procedure", "## Guardrails")
            if heading not in content
        ]
        trigger_section_count = len(re.findall(r"(?m)^#\s+Experience Trigger\s*$", content))
        _, trigger_code = _experience_constraint_and_trigger(content, target)
        reasons: list[str] = []
        if missing:
            reasons.append("missing required headings: " + ", ".join(missing))
        if trigger_section_count > 1 or (trigger_section_count == 0 and not trigger_code):
            reasons.append(
                f"expected exactly one Experience Trigger section or trigger_code metadata, got section_count={trigger_section_count}"
            )
        if not trigger_code:
            reasons.append("missing trigger_code")
        if trigger_code:
            try:
                validate_trigger_code(trigger_code)
                smoke_test_trigger_code(trigger_code)
            except TriggerSandboxError as exc:
                reasons.append(f"invalid trigger_code: {exc}")
        if not reasons:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="; ".join(reasons),
            evidence={"target_name": target.target_name},
            retriable=True,
            repair_prompt=(
                "Rewrite the experience content with exactly the required headings and provide "
                "valid trigger_code either in the Experience Trigger section or schema metadata."
            ),
        )


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
            reason="no source trajectory authorizes an experience update",
            evidence={
                "target_name": target.target_name,
                "signals": [signal.to_dict() for signal in signals],
            },
        )


@dataclass(slots=True)
class ExperienceToolAlignmentGate:
    mode: GateMode = "enforce"
    name: str = "experience_tool_alignment"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        _, trigger_code = _experience_constraint_and_trigger(target.after_content, target)
        profile = TriggerProfile.from_code(trigger_code)
        if not profile.candidate_tools:
            return None  # trigger shape gate reports the structural issue.
        if len(profile.candidate_tools) != 1:
            return None
        trigger_tool = next(iter(profile.candidate_tools))
        eligible_signals = [
            s for s in _source_signals(target) if _signal_allows_experience_update(s)
        ]
        if not eligible_signals:
            return None
        if any(_tool_matches_signal(trigger_tool, signal) for signal in eligible_signals):
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="trigger tool does not match first wrong tool or trigger boundary",
            evidence={
                "target_name": target.target_name,
                "trigger_tool": trigger_tool,
                "signals": [signal.to_dict() for signal in eligible_signals],
            },
            retriable=True,
            repair_prompt=(
                "Change trigger_code to use exactly one candidate_tool matching First Wrong "
                "Tool Call.Tool or Trigger boundary; otherwise output no changes."
            ),
        )


@dataclass(slots=True)
class ExperienceTriggerShapeGate:
    mode: GateMode = "enforce"
    name: str = "experience_trigger_shape"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        _, trigger_code = _experience_constraint_and_trigger(target.after_content, target)
        profile = TriggerProfile.from_code(trigger_code)
        reasons: list[str] = []
        if profile.parse_error:
            reasons.append(f"cannot parse trigger_code: {profile.parse_error}")
        if len(profile.candidate_tools) != 1:
            reasons.append(
                f"trigger must bind exactly one candidate_tool, got {sorted(profile.candidate_tools)}"
            )
        tool = next(iter(profile.candidate_tools)) if len(profile.candidate_tools) == 1 else ""
        if profile.is_history_only:
            reasons.append("trigger is history-only; it must inspect candidate args or content")
        if profile.direct_true_after_tool_gate:
            reasons.append("trigger returns True after only a tool gate")
        advisories: list[str] = []
        if tool == "communicate_with_user":
            if not profile.uses_candidate_content:
                reasons.append("communicate_with_user trigger must inspect candidate content")
        elif tool:
            if not profile.uses_candidate_tool_args:
                reasons.append(f"{tool} trigger must inspect candidate_tool_args")
            elif target.stage == "post_plan":
                advisory = _recommended_arg_check_advisory(tool, profile.inspected_arg_keys)
                if advisory:
                    advisories.append(advisory)
        if not reasons and not advisories:
            return None
        evidence = {
            "target_name": target.target_name,
            "trigger_profile": profile.to_dict(),
        }
        if advisories:
            evidence["advisories"] = advisories
        if not reasons:
            return GateDecision(
                gate_name=self.name,
                action="warn",
                reason="; ".join(advisories),
                evidence=evidence,
            )
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="; ".join(reasons),
            evidence=evidence,
            retriable=True,
            repair_prompt=(
                "Rewrite trigger_code as a narrow candidate-shape gate: first filter the exact "
                "candidate_tool, then inspect candidate_tool_args (or candidate_tool_args['content'] "
                "for communicate_with_user), and return False when decisive candidate fields are absent."
            ),
        )


@dataclass(slots=True)
class ExperienceUpdateNarrowingGate:
    mode: GateMode = "warn"
    name: str = "experience_update_narrowing"

    def applies_to(self, target: GateTarget) -> bool:
        return (
            target.memory_type == "experiences"
            and target.before_content is not None
            and target.after_content.strip() != ""
        )

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        _, before_trigger = _experience_constraint_and_trigger(target.before_content or "", target)
        _, after_trigger = _experience_constraint_and_trigger(target.after_content, target)
        before = TriggerProfile.from_code(before_trigger)
        after = TriggerProfile.from_code(after_trigger)
        reasons: list[str] = []
        if len(after.candidate_tools) > len(before.candidate_tools):
            reasons.append("updated trigger matches more candidate tools than before")
        if before.uses_candidate_tool_args and not after.uses_candidate_tool_args:
            reasons.append("updated trigger removed candidate_tool_args checks")
        if before.uses_candidate_content and not after.uses_candidate_content:
            reasons.append("updated trigger removed candidate content checks")
        if after.is_history_only and not before.is_history_only:
            reasons.append("updated trigger became history-only")
        if before.inspected_arg_keys and after.inspected_arg_keys < before.inspected_arg_keys:
            reasons.append("updated trigger inspects fewer candidate arg keys than before")
        if not reasons:
            return None
        return GateDecision(
            gate_name=self.name,
            action="warn",
            reason="; ".join(reasons),
            evidence={
                "target_name": target.target_name,
                "before_trigger_profile": before.to_dict(),
                "after_trigger_profile": after.to_dict(),
            },
        )


@dataclass(slots=True)
class TrajectoryRepairSignal:
    uri: str
    outcome: str
    repair_action: str
    first_wrong_tool: str
    trigger_boundary: str
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
            "first_wrong_step": self.first_wrong_step,
            "failure_kind": self.failure_kind,
            "db_check_passed": self.db_check_passed,
            "action_checks_passed": self.action_checks_passed,
            "communicate_checks_passed": self.communicate_checks_passed,
        }


@dataclass(slots=True)
class TriggerProfile:
    parse_error: str = ""
    candidate_tools: set[str] = field(default_factory=set)
    uses_candidate_tool_args: bool = False
    uses_candidate_content: bool = False
    uses_messages: bool = False
    inspected_arg_keys: set[str] = field(default_factory=set)
    direct_true_after_tool_gate: bool = False

    @property
    def is_history_only(self) -> bool:
        return (
            self.uses_messages
            and not self.uses_candidate_tool_args
            and not self.uses_candidate_content
        )

    @classmethod
    def from_code(cls, code: str | None) -> "TriggerProfile":
        text = str(code or "").strip()
        profile = cls()
        if not text:
            profile.parse_error = "empty trigger_code"
            return profile
        try:
            tree = ast.parse(text, mode="exec")
        except SyntaxError as exc:
            profile.parse_error = str(exc)
            return profile
        visitor = _TriggerProfileVisitor()
        visitor.visit(tree)
        profile.candidate_tools = visitor.candidate_tools
        profile.uses_candidate_tool_args = visitor.uses_candidate_tool_args
        profile.uses_candidate_content = visitor.uses_candidate_content
        profile.uses_messages = visitor.uses_messages
        profile.inspected_arg_keys = visitor.inspected_arg_keys
        profile.direct_true_after_tool_gate = visitor.direct_true_after_tool_gate
        return profile

    def to_dict(self) -> dict[str, Any]:
        return {
            "parse_error": self.parse_error,
            "candidate_tools": sorted(self.candidate_tools),
            "uses_candidate_tool_args": self.uses_candidate_tool_args,
            "uses_candidate_content": self.uses_candidate_content,
            "uses_messages": self.uses_messages,
            "inspected_arg_keys": sorted(self.inspected_arg_keys),
            "direct_true_after_tool_gate": self.direct_true_after_tool_gate,
            "is_history_only": self.is_history_only,
        }


class _TriggerProfileVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.candidate_tools: set[str] = set()
        self.uses_candidate_tool_args = False
        self.uses_candidate_content = False
        self.uses_messages = False
        self.inspected_arg_keys: set[str] = set()
        self.direct_true_after_tool_gate = False
        self._candidate_args_aliases: set[str] = set()
        self._candidate_content_aliases: set[str] = set()
        self._tool_gate_depth = 0

    def visit_Assign(self, node: ast.Assign) -> Any:  # noqa: ANN401, N802
        value_key = _ctx_get_constant_key(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if value_key == "candidate_tool_args":
                    self._candidate_args_aliases.add(target.id)
                    self.uses_candidate_tool_args = True
                elif value_key in {"candidate_content", "content"}:
                    self._candidate_content_aliases.add(target.id)
                    self.uses_candidate_content = True
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> Any:  # noqa: ANN401, N802
        tools = _candidate_tools_from_expr(node.test)
        if tools:
            self.candidate_tools.update(tools)
            self._tool_gate_depth += 1
            if _body_has_bare_return_true(node.body):
                self.direct_true_after_tool_gate = True
            for stmt in node.body:
                self.visit(stmt)
            self._tool_gate_depth -= 1
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> Any:  # noqa: ANN401, N802
        self.candidate_tools.update(_candidate_tools_from_expr(node))
        self._record_candidate_arg_membership_check(node)
        self.generic_visit(node)

    def _record_candidate_arg_membership_check(self, node: ast.Compare) -> None:
        if not isinstance(node.left, ast.Constant) or not isinstance(node.left.value, str):
            return
        key = node.left.value
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            if _is_candidate_args_expr(comparator, self._candidate_args_aliases):
                self.inspected_arg_keys.add(key)
                self.uses_candidate_tool_args = True
                if key == "content":
                    self.uses_candidate_content = True

    def visit_Call(self, node: ast.Call) -> Any:  # noqa: ANN401, N802
        key = _ctx_get_constant_key(node)
        if key == "candidate_tool_args":
            self.uses_candidate_tool_args = True
        elif key == "messages":
            self.uses_messages = True
        elif key in {"candidate_content", "content"}:
            self.uses_candidate_content = True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            owner = node.func.value
            if _is_candidate_args_expr(owner, self._candidate_args_aliases):
                arg_key = _first_constant_string_arg(node)
                if arg_key:
                    self.inspected_arg_keys.add(arg_key)
                    self.uses_candidate_tool_args = True
                    if arg_key == "content":
                        self.uses_candidate_content = True
            if _is_candidate_content_expr(owner, self._candidate_content_aliases):
                self.uses_candidate_content = True
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:  # noqa: ANN401, N802
        if _is_candidate_args_expr(node.value, self._candidate_args_aliases):
            key = _slice_constant_string(node.slice)
            if key:
                self.inspected_arg_keys.add(key)
                if key == "content":
                    self.uses_candidate_content = True
            self.uses_candidate_tool_args = True
        if _is_candidate_content_expr(node.value, self._candidate_content_aliases):
            self.uses_candidate_content = True
        self.generic_visit(node)


def _effective_action(action: GateAction, mode: GateMode) -> GateAction:
    if action != "reject":
        return action
    if mode == "shadow":
        return "allow"
    if mode == "warn":
        return "warn"
    return "reject"


def _gradient_gate_target(
    *,
    gradient: SemanticGradient,
    analyses: list[RolloutAnalysis],
    policy_set: PolicySet,
) -> GateTarget:
    trajectory = _source_trajectory_for_gradient(gradient, analyses)
    analysis = _analysis_for_trajectory(trajectory, analyses) if trajectory is not None else None
    memory_type = _gradient_memory_type(gradient)
    return GateTarget(
        stage="post_gradient",
        memory_type=memory_type,
        target_kind="gradient",
        gradient=gradient,
        analysis=analysis,
        trajectory=trajectory,
        policy_set=policy_set,
    )


def _plan_item_gate_target(
    *,
    item: PolicyPlanItem,
    analyses: list[RolloutAnalysis],
    policy_set: PolicySet,
) -> GateTarget:
    trajectory = _source_trajectory_for_plan_item(item, analyses)
    analysis = _analysis_for_trajectory(trajectory, analyses) if trajectory is not None else None
    return GateTarget(
        stage="post_plan",
        memory_type=item.memory_type,
        target_kind="plan_item",
        plan_item=item,
        analysis=analysis,
        trajectory=trajectory,
        policy_set=policy_set,
    )


def _source_trajectory_for_gradient(
    gradient: SemanticGradient,
    analyses: list[RolloutAnalysis],
) -> Trajectory | None:
    source_uris = {
        str(getattr(link, "to_uri", "") or "")
        for link in list(getattr(gradient, "links", []) or [])
        if str(getattr(link, "link_type", "") or "") == "derived_from"
    }
    metadata_uri = str((getattr(gradient, "metadata", {}) or {}).get("source_trajectory_uri") or "")
    if metadata_uri:
        source_uris.add(metadata_uri)
    return _first_matching_trajectory(source_uris, analyses)


def _source_trajectory_for_plan_item(
    item: PolicyPlanItem,
    analyses: list[RolloutAnalysis],
) -> Trajectory | None:
    source_uris: set[str] = set()
    for link in list(getattr(item, "links", []) or []):
        if hasattr(link, "to_uri"):
            to_uri = str(getattr(link, "to_uri", "") or "")
            link_type = str(getattr(link, "link_type", "") or "")
        elif isinstance(link, dict):
            to_uri = str(link.get("to_uri") or "")
            link_type = str(link.get("link_type") or "")
        else:
            continue
        if link_type == "derived_from" and to_uri:
            source_uris.add(to_uri)
    return _first_matching_trajectory(source_uris, analyses)


def _first_matching_trajectory(
    source_uris: set[str],
    analyses: list[RolloutAnalysis],
) -> Trajectory | None:
    if not source_uris:
        return None
    for analysis in analyses:
        for trajectory in analysis.trajectories:
            if trajectory.uri in source_uris:
                return trajectory
    return None


def _analysis_for_trajectory(
    trajectory: Trajectory | None,
    analyses: list[RolloutAnalysis],
) -> RolloutAnalysis | None:
    if trajectory is None:
        return None
    for analysis in analyses:
        if any(item.uri == trajectory.uri for item in analysis.trajectories):
            return analysis
    return None


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
        repair_action=_norm_token(_field_from_section(repair_section, "Action")),
        first_wrong_tool=_norm_tool(first_tool),
        trigger_boundary=_norm_tool(_field_from_section(repair_section, "Trigger boundary")),
        first_wrong_step=_field_from_section(first_section, "Step"),
        failure_kind=_field_from_section(first_section, "Error type"),
        db_check_passed=_bool_from_runtime(runtime_section, "db"),
        action_checks_passed=_bool_from_runtime(runtime_section, "action"),
        communicate_checks_passed=_bool_from_runtime(runtime_section, "communicate"),
    )


def _signal_allows_experience_update(signal: TrajectoryRepairSignal) -> bool:
    if signal.outcome == "success":
        return False
    if signal.repair_action.startswith("skip") or signal.repair_action in {"", "none", "无"}:
        return False
    if not (signal.repair_action.startswith("create") or signal.repair_action.startswith("update")):
        return False
    if signal.first_wrong_tool in {"", "none", "无"}:
        return False
    if signal.trigger_boundary in {"", "none", "无"}:
        return False
    return True


def _tool_matches_signal(tool: str, signal: TrajectoryRepairSignal) -> bool:
    candidates = {signal.first_wrong_tool}
    boundary = signal.trigger_boundary
    if boundary:
        candidates.update(_extract_tool_names(boundary))
        candidates.add(boundary)
    return tool in candidates


def _experience_constraint_and_trigger(
    content: str,
    target: GateTarget,
) -> tuple[str, str]:
    metadata, constraint = _extract_rendered_constraint_metadata(str(content or ""))
    trigger = str(metadata.get("trigger_code") or "").strip()
    if not trigger:
        fields: dict[str, Any] = {}
        if target.gradient is not None:
            fields = dict(getattr(target.gradient.after_file, "extra_fields", {}) or {})
        elif target.plan_item is not None and isinstance(target.plan_item.metadata, dict):
            for key in ("merge_memory_fields", "patch_metadata"):
                value = target.plan_item.metadata.get(key)
                if isinstance(value, dict):
                    fields.update(value)
        trigger = str(fields.get("trigger_code") or "").strip()
    return constraint, trigger


def _gradient_memory_type(gradient: SemanticGradient) -> str:
    fields = dict(getattr(gradient.after_file, "extra_fields", {}) or {})
    return str(
        getattr(gradient.after_file, "memory_type", "")
        or fields.get("memory_type")
        or "experiences"
    )


def _recommended_arg_check_advisory(tool: str, keys: set[str]) -> str:
    """Return non-blocking trigger-shape advice for common tool object bindings.

    These checks are intentionally advisory. A useful repair experience may be
    about a specific bad field (for example payment_id), so requiring every
    canonical object-binding field would incorrectly reject good experiences.
    """

    requirements = {
        "book_reservation": {"one_of": {"passengers", "payment_methods", "flights"}},
        "cancel_reservation": {"one_of": {"reservation_id"}},
        "update_reservation_flights": {"one_of": {"reservation_id", "flights", "payment_id"}},
        "update_reservation_baggages": {
            "one_of": {"reservation_id", "total_baggages", "nonfree_baggages", "payment_id"},
        },
        "send_certificate": {"one_of": {"user_id", "amount"}},
    }
    spec = requirements.get(tool)
    if not spec:
        return ""
    one_of = set(spec.get("one_of", set()))
    if one_of and not (one_of & keys):
        return (
            f"{tool} trigger should preferably inspect at least one stable candidate arg key "
            f"from {sorted(one_of)}, unless the failure-specific decisive field is different"
        )
    return ""


def _ctx_get_constant_key(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
        return ""
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "ctx":
        return ""
    return _first_constant_string_arg(node)


def _first_constant_string_arg(node: ast.Call) -> str:
    if not node.args:
        return ""
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return ""


def _candidate_tools_from_expr(node: ast.AST) -> set[str]:
    tools: set[str] = set()
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            tools.update(_candidate_tools_from_expr(value))
        return tools
    if not isinstance(node, ast.Compare):
        return tools
    left_is_tool = _is_ctx_candidate_tool(node.left)
    for op, comparator in zip(node.ops, node.comparators, strict=False):
        if isinstance(op, (ast.Eq, ast.NotEq)):
            if (
                left_is_tool
                and isinstance(comparator, ast.Constant)
                and isinstance(comparator.value, str)
            ):
                tools.add(comparator.value)
            elif (
                _is_ctx_candidate_tool(comparator)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
            ):
                tools.add(node.left.value)
        if isinstance(op, (ast.In, ast.NotIn)) and left_is_tool:
            if isinstance(comparator, (ast.List, ast.Tuple, ast.Set)):
                for elt in comparator.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        tools.add(elt.value)
    return tools


def _is_ctx_candidate_tool(node: ast.AST) -> bool:
    return _ctx_get_constant_key(node) == "candidate_tool"


def _is_candidate_args_expr(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    return _ctx_get_constant_key(node) == "candidate_tool_args"


def _is_candidate_content_expr(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    return _ctx_get_constant_key(node) in {"candidate_content", "content"}


def _slice_constant_string(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _body_has_bare_return_true(body: list[ast.stmt]) -> bool:
    meaningful = [stmt for stmt in body if not isinstance(stmt, ast.Pass)]
    if len(meaningful) != 1:
        return False
    stmt = meaningful[0]
    return (
        isinstance(stmt, ast.Return)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is True
    )


def _section(content: str, title: str) -> str:
    pattern = re.compile(
        rf"(?mi)^-\s*{re.escape(title)}:\s*$\n(?P<body>.*?)(?=^-[^\n]*:\s*(?:$|.)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(content or "")
    return match.group("body") if match else ""


def _field_from_section(section: str, field_name: str) -> str:
    return _first_match(section, rf"(?mi)^\s*-\s*{re.escape(field_name)}:\s*(.+)$")


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "")
    return match.group(1).strip() if match else ""


def _norm_token(value: str) -> str:
    return str(value or "").strip().lower()


def _norm_tool(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    match = re.search(r"[a-zA-Z_][a-zA-Z0-9_]*", value)
    return match.group(0) if match else value.lower()


def _extract_tool_names(value: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", value or ""))


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


def _log_gate_rejection(target: GateTarget, decisions: list[GateDecision]) -> None:
    rejected = [decision for decision in decisions if decision.action == "reject"]
    if not rejected:
        return
    summary = "; ".join(f"{d.gate_name}: {d.reason}" for d in rejected)
    logger.info(
        "Policy gate rejected %s %s: %s",
        target.target_kind,
        target.target_name,
        summary,
    )
    tracer.info(
        "policy_gate.rejected "
        f"stage={target.stage} target_kind={target.target_kind} "
        f"memory_type={target.memory_type} target_name={target.target_name} "
        f"rejections={summary}"
    )
