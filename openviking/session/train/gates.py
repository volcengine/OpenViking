# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy training gates.

Gates run inside the train framework before semantic gradients or planned
policy updates are allowed to affect the policy set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from openviking.session.train.domain import PolicyPlanItem, PolicySet, RolloutAnalysis, Trajectory
from openviking.session.train.interfaces import SemanticGradient
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.llm import parse_json_from_response

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
class ExperienceRootCausePreventionGate:
    """LLM gate for extracted experience prevention quality.

    This gate is intended for the experience extraction loop only.  It reviews
    the concrete experience draft that would become an injectable pre-tool
    reminder, rather than reviewing compact trajectory evidence or merged plan
    items. Later merge stages do not repeat this semantic LLM call.
    """

    mode: GateMode = "enforce"
    name: str = "experience_root_cause_prevention"
    vlm: Any = None
    max_policy_chars: int = 5000

    def applies_to(self, target: GateTarget) -> bool:
        return (
            target.stage == "post_gradient"
            and target.memory_type == "experiences"
            and target.target_kind == "gradient"
            and target.gradient is not None
            and target.after_content.strip() != ""
            and target.trajectory is not None
        )

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        authoritative_behavior_anchor = _authoritative_behavior_anchor(target.analysis)
        prompt = _experience_root_cause_prevention_prompt(
            target,
            max_policy_chars=self.max_policy_chars,
        )
        try:
            response = await self._get_vlm().get_completion_async(
                prompt=prompt,
                thinking=False,
            )
            parsed = parse_json_from_response(response)
        except Exception as exc:
            logger.warning(
                "experience root-cause prevention gate failed closed: %s",
                exc,
                exc_info=True,
            )
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience root-cause prevention gate failed closed",
                evidence={
                    "target_name": target.target_name,
                    "error": str(exc),
                },
            )

        if not isinstance(parsed, dict):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience root-cause prevention gate returned invalid output",
                evidence={
                    "target_name": target.target_name,
                    "raw_response_preview": _preview_text(str(response), limit=500),
                },
            )

        result = _normalize_experience_prevention_result(parsed)
        evidence = {
            "target_name": target.target_name,
            "pass": result["pass"],
            "root_cause_quality": result["root_cause_quality"],
            "reason": result["reason"],
            "expected_behavior_change": result["expected_behavior_change"],
            "risks": result["risks"],
        }
        if authoritative_behavior_anchor:
            evidence["authoritative_behavior_anchor"] = authoritative_behavior_anchor
            evidence["anchored_repair"] = not result["pass"]
            evidence["gate_model_reason"] = result["reason"]
        if result["pass"]:
            if result["risks"]:
                return GateDecision(
                    gate_name=self.name,
                    action="warn",
                    reason="experience prevention gate allowed with risks",
                    evidence=evidence,
                )
            return None

        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason=(
                "experience does not safely encode fixed authoritative behavior"
                if authoritative_behavior_anchor
                else result["reason"] or "experience does not pass counterfactual prevention review"
            ),
            evidence=evidence,
            retriable=True,
            repair_prompt=(
                _anchored_experience_repair_prompt(authoritative_behavior_anchor)
                if authoritative_behavior_anchor
                else (
                    result["repair_prompt"]
                    or "Rewrite or remove this experience. The repaired experience must be supported "
                    "by the source trajectory, trigger before the first preventable wrong decision, "
                    "state the narrow runtime rule that replaces the mistaken decision rule, preserve "
                    "any coupled communication/action-scope distinction needed to prevent recurrence, "
                    "avoid temporal `Does not apply when` clauses that block skill loading, "
                    "and explain what future behavior changes so the next similar session succeeds "
                    "without blocking nearby correct behavior."
                )
            ),
        )

    def _get_vlm(self) -> Any:
        if self.vlm is not None:
            return self.vlm
        from openviking_cli.utils.config import get_openviking_config

        self.vlm = get_openviking_config().vlm.get_vlm_instance()
        return self.vlm


def _experience_root_cause_prevention_prompt(
    target: GateTarget,
    *,
    max_policy_chars: int,
) -> str:
    trajectory = target.trajectory
    analysis = target.analysis
    evaluation_summary = _evaluation_summary(analysis) if analysis is not None else ""
    before = _preview_text(target.before_content or "", limit=max_policy_chars)
    after = _preview_text(target.after_content or "", limit=max_policy_chars)
    trajectory_content = trajectory.content if trajectory is not None else ""
    comparison_content = _comparison_trajectory_context(trajectory)
    trajectory_uri = trajectory.uri if trajectory is not None else ""
    trajectory_outcome = trajectory.outcome if trajectory is not None else ""
    authoritative_behavior_anchor = _authoritative_behavior_anchor(analysis)
    authoritative_behavior_section = (
        f"""## Fixed authoritative behavior delta

The structured evaluation has already settled the following reward-relevant behavior
for this extraction and every retry:

{authoritative_behavior_anchor}

Do not re-decide whether this behavior is correct. Base-policy wording cannot reverse,
remove, weaken, or condition away this fixed delta. Override only the smallest conflicting
policy interpretation and preserve all non-conflicting constraints. Comparison trajectories
may show how to realize the behavior, but they cannot override it.

Your responsibility is to validate how safely and narrowly the proposed experience encodes
the fixed behavior. You may reject missing behavior change, non-runtime triggers, over-broad
applicability or object scope, blocked preservation-set actions, or evaluator/case leakage.
Any repair may narrow Situation, applicability, object scope, or source binding, but must not
reverse or omit the fixed behavior delta.
"""
        if authoritative_behavior_anchor
        else ""
    )

    return f"""You are a senior counterfactual failure diagnostician for agent experience extraction.

Review ONE proposed experience update.  The proposed experience will be injected
through the skill experience loader in future sessions: the agent searches
experience candidates, reads `## Situation` snippets, and optionally loads the
full experience before acting.

Authoritative outcome evidence:
When the source context includes authoritative evaluation or outcome evidence supplied
by the training pipeline, that evidence defines the target behavior. If it conflicts
with base-policy wording, override only the smallest conflicting policy interpretation
needed to explain the required outcome; preserve non-conflicting constraints and object
boundaries. Judge the proposed experience against the reusable runtime behavior required
by that evidence. The experience itself must not mention the evaluator, evaluation
metadata, hidden checks, expected actions, or reward; it must express the lesson using
only observable user requests, tool results, runtime facts, and actions.

{authoritative_behavior_section}

Main question:
With any fixed authoritative behavior delta treated as non-negotiable, if this exact
experience had been injected before the source trajectory's first
preventable wrong decision, would it change the future agent's behavior enough
for the next similar session to succeed, without breaking nearby correct cases?

Pass only when all are true:
1. The source trajectory plus any comparison trajectories support the causal
   failure: first preventable wrong decision, mistaken runtime rule, visible
   runtime/source evidence, and success/failure contrast.
2. The experience is directly preventive: it changes a future tool call, missing
   tool call, confirmation, calculation, policy branch, write, communication, or
   final answer before or at the failing boundary.
3. The experience is injectable: it is a runtime reminder, not a case audit,
   evaluator diagnosis, broad SOP, hidden answer, or generic "check everything" rule.
4. `## Situation` is specific enough for the skill loader path: it tells a future
   agent when to read/apply the experience, when not to apply it, and which
   runtime source binding supports the rule.
5. If the same root ambiguity caused both a communication obligation and a
   write/action scope mistake, the experience preserves that coupling instead
   of splitting it into two weak partial lessons.
6. If the source failure involved agent-initiated scope expansion, the experience
   does not treat a user's later yes/confirmation to the agent's over-broad
   proposal as proof that the user independently requested the broader scope.
7. For information, aggregate, list, summary, or value obligations, the
   experience preserves the user-requested source scope from the time of the
   request. Later writes may be described as a separate post-action/current
   scope, but they do not silently replace the original requested scope. If both
   scopes are plausible from runtime wording, the experience labels both.
8. The experience does not exclude request-time records merely because they were
   later modified, canceled, upgraded, consumed, split, or otherwise changed,
   unless the user's own words explicitly excluded that semantic role from the
   earlier information request.
9. Relative wording such as "other", "remaining", "those", "其他", or "剩余" is
   not treated as an explicit exclusion by itself when writes are also being
   discussed; the experience either preserves both labeled scopes or cites the
   user's explicit exclusion wording.
10. For money, total cost, paid amount, balance, refund, or similar value
   obligations, the experience identifies the canonical source field when one
   exists. Explicit total/paid/charged/order/payment amount fields take
   precedence over reconstructed line-item sums; line items are a fallback or
   cross-check. Do not name lower-level unit/segment/item price fields as the
   primary source when a record-level total/paid/charged amount is available in
   source or comparison evidence.
11. `Does not apply when` names a real task-pattern mismatch, not a temporal
   loader stage such as still reading/writing, before final_response, or before
   writes complete. Temporal wording would make the future agent skip reading an
   experience that must be available from task start.

Fail when the proposed experience only summarizes the task, fires too late,
uses unsupported or hidden facts not present in the source/comparison
trajectories, overfits case literals, misses the root decision rule, splits a
coupled causal chain, treats agent-proposed expansion as
user-initiated scope, lets a later-write/current-state scope overwrite an
earlier information request scope, silently drops later-modified records from an
earlier request-time aggregate, uses temporal non-applicability to avoid skill
loading, uses a wrong value source field where a canonical total/payment field
exists, lacks a concrete future behavior change, or would likely harm correct
behavior.
When source, comparison, or authoritative outcome evidence demonstrates that the
agent skipped an action because an attached benefit was unavailable even though the
target behavior requires the action, require the experience to distinguish action
eligibility from benefit eligibility. Do not infer that distinction merely because
the user accepted no benefit. Preserve benefit conditions that the evidence shows are
true prerequisites for the action.

Return JSON only:
{{
  "pass": true,
  "root_cause_quality": "sufficient",
  "reason": "brief situation -> changed behavior -> success explanation",
  "expected_behavior_change": "what the future agent would do differently",
  "repair_prompt": "",
  "risks": []
}}

If failing, set "pass": false, choose root_cause_quality from:
surface_level, unsupported, not_preventive, too_late_boundary,
wrong_scope, split_causal_chain, agent_initiated_scope_expansion,
missing_source_binding, missing_behavior_change, not_injectable,
over_broad, later_write_scope_substitution, implicit_later_write_exclusion,
wrong_source_field, temporal_non_applicability, unsafe, unclear.
action_benefit_eligibility_confusion,
Set repair_prompt to one concise instruction for rewriting or removing this
specific experience. Do not ask for any output schema.

## Source trajectory
uri: {trajectory_uri}
outcome: {trajectory_outcome}

{trajectory_content}

## Comparison trajectories
{comparison_content or "(none)"}

## Evaluation summary
{evaluation_summary}

## Current experience content before update
{before or "(none/new experience)"}

## Proposed experience content after update
target: {target.target_name}

{after}
"""


def _comparison_trajectory_context(
    trajectory: Trajectory | None,
) -> str:
    if trajectory is None:
        return ""
    metadata = dict(getattr(trajectory, "metadata", {}) or {})
    items = metadata.get("comparison_trajectories")
    if not isinstance(items, list) or not items:
        return ""
    chunks: list[str] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or "")
        outcome = str(item.get("outcome") or "")
        content = str(item.get("content") or "")
        header = f"### comparison_{index}\nuri: {uri}\noutcome: {outcome}\n"
        chunk = header + content
        chunks.append(chunk)
    return "\n\n".join(chunks)


def _evaluation_summary(analysis: RolloutAnalysis | None) -> str:
    if analysis is None or analysis.evaluation is None:
        return ""
    evaluation = analysis.evaluation
    lines = [
        f"passed={evaluation.passed}",
        f"score={evaluation.score}",
    ]
    feedback = list(getattr(evaluation, "feedback", []) or [])
    if feedback:
        lines.append("feedback=" + "; ".join(str(item) for item in feedback[:5]))
    metadata = dict(getattr(evaluation, "metadata", {}) or {})
    if metadata:
        # Keep the most useful compact bits; full metadata can be huge in tau2.
        for key in ("reward", "source"):
            if key in metadata:
                lines.append(f"{key}={metadata[key]}")
        eval_result = metadata.get("evaluation_result")
        if isinstance(eval_result, dict):
            for key in ("reward", "reward_breakdown", "db_check", "communicate_checks"):
                if key in eval_result:
                    lines.append(f"{key}={_preview_text(str(eval_result[key]), limit=1000)}")
    return "\n".join(lines)


def _authoritative_behavior_anchor(analysis: RolloutAnalysis | None) -> str:
    if analysis is None or analysis.evaluation is None:
        return ""
    metadata = getattr(analysis.evaluation, "metadata", None)
    if not isinstance(metadata, dict):
        return ""
    evaluation_result = metadata.get("evaluation_result")
    if not isinstance(evaluation_result, dict):
        return ""

    missing_actions: list[str] = []
    matched_actions: list[str] = []
    action_checks = evaluation_result.get("action_checks")
    if isinstance(action_checks, list):
        for item in action_checks[:20]:
            if not isinstance(item, dict):
                continue
            action_match = item.get("action_match")
            if not isinstance(action_match, bool):
                continue
            action = item.get("action")
            if not isinstance(action, dict):
                continue
            action_name = str(action.get("name") or "").strip()
            if not action_name:
                continue
            formatted_action = _format_authoritative_action(
                action_name,
                action.get("arguments"),
            )
            if action_match:
                matched_actions.append(formatted_action)
            else:
                missing_actions.append(formatted_action)

    missing_communications: list[str] = []
    communicate_checks = evaluation_result.get("communicate_checks")
    if isinstance(communicate_checks, list):
        for item in communicate_checks[:20]:
            if not isinstance(item, dict) or item.get("met") is not False:
                continue
            info = item.get("info")
            if info is None:
                continue
            text = _preview_text(str(info).strip(), limit=500)
            if text:
                missing_communications.append(text)

    if not missing_actions and not matched_actions and not missing_communications:
        return ""

    lines = [
        *(f"- Required missing action: {action}" for action in missing_actions),
        *(f"- Preserve matched action: {action}" for action in matched_actions),
        *(
            f"- Required missing communication: {communication}"
            for communication in missing_communications
        ),
    ]
    db_check = evaluation_result.get("db_check")
    db_match = db_check.get("db_match") if isinstance(db_check, dict) else None
    if missing_actions:
        lines.append(
            "- Failure boundary: add the missing required action while preserving matched actions."
        )
    elif db_match is True and missing_communications:
        lines.append(
            "- Failure boundary: database/actions already match; repair user-visible communication only."
        )
    elif db_match is False:
        lines.append(
            "- Failure boundary: database state does not match; preserve matched actions while "
            "repairing the remaining action scope."
        )
    return _preview_text("\n".join(lines), limit=4000)


def _format_authoritative_action(name: str, arguments: Any) -> str:
    try:
        payload = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        payload = str(arguments)
    return f"{name}({_preview_text(payload, limit=500)})"


def _anchored_experience_repair_prompt(anchor: str) -> str:
    return f"""Fixed authoritative behavior delta (must preserve):
{anchor}

Rewrite only the rejected experience. It must cause the fixed behavior at the earliest
preventable decision boundary and preserve every matched action. Narrow Situation,
applicability, object scope, or runtime source binding and remove unsupported generalization
as needed. You must not remove, reverse, weaken, or condition away any fixed behavior.
Do not mention evaluation, evaluator metadata, hidden checks, expected actions, or reward in
the experience. If these constraints cannot be satisfied, output no experience change."""


def _normalize_experience_prevention_result(parsed: dict[str, Any]) -> dict[str, Any]:
    risks = parsed.get("risks") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    repair_prompt = parsed.get("repair_prompt")
    if repair_prompt is None:
        repair_prompt = parsed.get("followup_message")
    return {
        "pass": bool(parsed.get("pass")),
        "root_cause_quality": str(parsed.get("root_cause_quality") or "unclear"),
        "reason": str(parsed.get("reason") or ""),
        "expected_behavior_change": str(parsed.get("expected_behavior_change") or ""),
        "repair_prompt": str(repair_prompt or ""),
        "risks": [str(item) for item in risks if str(item)],
    }


def _preview_text(text: str, *, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


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
    fields = dict(getattr(gradient.after_file, "extra_fields", {}) or {})
    return str(
        getattr(gradient.after_file, "memory_type", "")
        or fields.get("memory_type")
        or "experiences"
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
    tracer.info(
        "policy_gate.rejected "
        f"stage={target.stage} target_kind={target.target_kind} "
        f"memory_type={target.memory_type} target_name={target.target_name} "
        f"rejections={summary}"
    )
