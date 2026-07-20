# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Gate execution and the active default gate set."""

from __future__ import annotations

from dataclasses import dataclass

from openviking.session.train.domain import PolicyPlanItem, PolicySet, RolloutAnalysis, Trajectory
from openviking.session.train.interfaces import SemanticGradient
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

from .causal_signal import ExperienceCausalSignalGate
from .evidence_safety import ExperienceEvidenceSafetyGate
from .language_binding import ExperienceLanguageBindingGate
from .models import GateAction, GateDecision, GateMode, GateReport, GateTarget, PolicyGate
from .name_polarity import ExperienceNamePolarityGate
from .plan_quality import ExperiencePlanQualityGate
from .portability import ExperiencePortabilityGate
from .skill_readability import ExperienceSkillReadabilityGate
from .specificity import ExperienceSpecificityGate

_EXPERIENCE_GATE_VALIDATION_KEY = "experience_gate_validation"


_EXPERIENCE_GATE_VALIDATION_VALUE = "post_validation_hook"


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
                _trace_gate_result(
                    target,
                    gate_name=gate.name,
                    action="allow",
                    reason="passed",
                )
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
            _trace_gate_result(
                target,
                gate_name=decision.gate_name,
                action=decision.action,
                reason=decision.reason,
            )
            decisions.append(decision)
            if action == "reject":
                rejected = True
        return not rejected, decisions


def default_policy_gate_runner() -> GateRunner:
    """Default hard-coded deterministic gates used by session policy training."""

    return GateRunner(
        gates=[
            ExperienceCausalSignalGate(mode="enforce"),
            ExperienceSkillReadabilityGate(mode="enforce"),
            ExperienceNamePolarityGate(mode="enforce"),
            ExperienceSpecificityGate(mode="enforce"),
            ExperienceLanguageBindingGate(mode="enforce"),
            ExperienceEvidenceSafetyGate(mode="enforce"),
            ExperiencePortabilityGate(mode="enforce"),
            ExperiencePlanQualityGate(mode="enforce"),
        ]
    )


def mark_experience_gradients_post_validated(
    gradients: list[SemanticGradient],
) -> None:
    """Mark experience gradients accepted by their extraction post-validation hook."""

    for gradient in gradients:
        if not _is_experience_gradient(gradient):
            continue
        metadata = getattr(gradient, "metadata", None)
        if not isinstance(metadata, dict):
            raise RuntimeError("experience gradient metadata must be a dictionary")
        metadata[_EXPERIENCE_GATE_VALIDATION_KEY] = _EXPERIENCE_GATE_VALIDATION_VALUE


def require_experience_gradients_post_validated(
    gradients: list[SemanticGradient],
) -> None:
    """Fail when an experience gradient bypassed extraction post-validation."""

    unvalidated = [
        str(getattr(gradient, "target_name", "unknown"))
        for gradient in gradients
        if _is_experience_gradient(gradient)
        and dict(getattr(gradient, "metadata", {}) or {}).get(_EXPERIENCE_GATE_VALIDATION_KEY)
        != _EXPERIENCE_GATE_VALIDATION_VALUE
    ]
    if unvalidated:
        raise RuntimeError(
            "experience gradients must be validated by the extraction post-validation hook: "
            + ", ".join(unvalidated)
        )


logger = get_logger(__name__)


logger = get_logger(__name__)


def _is_experience_gradient(gradient: SemanticGradient) -> bool:
    return _gradient_memory_type(gradient) == "experiences"


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


def _gradient_memory_type(gradient: SemanticGradient) -> str:
    after_file = getattr(gradient, "after_file", None)
    fields = dict(getattr(after_file, "extra_fields", {}) or {})
    metadata = dict(getattr(gradient, "metadata", {}) or {})
    return str(
        getattr(after_file, "memory_type", "")
        or fields.get("memory_type")
        or metadata.get("memory_type")
        or "unknown"
    )


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


def _trace_gate_result(
    target: GateTarget,
    *,
    gate_name: str,
    action: GateAction,
    reason: str,
) -> None:
    compact_reason = " ".join(str(reason or "").split())
    tracer.info(
        "policy_gate.result "
        f"stage={target.stage} target_kind={target.target_kind} "
        f"memory_type={target.memory_type} target_name={target.target_name} "
        f"gate={gate_name} action={action} reason={compact_reason}"
    )
