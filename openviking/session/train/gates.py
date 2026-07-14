# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Policy training gates.

Gates run inside the train framework before semantic gradients or planned
policy updates are allowed to affect the policy set.  Most gates are lightweight
deterministic checks; some gates may use an LLM for semantic reflection when the
decision is about expected behavioral impact rather than static shape.
"""

from __future__ import annotations

import ast
import re
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

_GATE_NONE_VALUES = {"", "none", "n/a", "na", "null", "无", "无。", "没有"}
_GATE_AGGREGATE_TERMS_RE = re.compile(
    r"\b(total|cost|count|list|summary|aggregate|balance|sum|subtotal|grand total|paid|refund)\b"
    r"|总(?:费用|价|额|计|数)|合计|汇总|列表|数量|退款|余额|已付",
    re.IGNORECASE,
)
_GATE_RELATIVE_SCOPE_RE = re.compile(
    r"\b(?:other|remaining|those|the\s+rest|rest\s+of|leftover)\b|其他|剩余|其余|剩下",
    re.IGNORECASE,
)
_GATE_WRITE_SCOPE_RE = re.compile(
    r"\b(?:cancel|cancell|upgrade|modify|change|update|write|book|reschedule)\b"
    r"|取消|升级|修改|变更|更改|写入|预订|改签",
    re.IGNORECASE,
)
_GATE_LINE_ITEM_MONEY_SOURCE_RE = re.compile(
    r"\b(?:line[- ]?item|itemized|reconstruct|derive|flight\s+price|fare)\b"
    r"|\b(?:unit|segment|leg|component|per[- ]?item|per[- ]?unit)\b.{0,40}\b(?:price|cost|fare|amount)\b"
    r"|\b(?:price|cost|fare)\s*(?:field|attribute|column)\b"
    r"|price.{0,40}(?:passenger|count|quantity)"
    r"|(?:passenger|count|quantity).{0,40}price"
    r"|航班价格|单价|分段价格|明细价格|价格字段|price\s*字段|price.{0,8}字段|字段.{0,8}price"
    r"|价格.{0,12}乘客|乘客.{0,12}价格|明细.{0,12}(?:求和|相加)",
    re.IGNORECASE,
)
_GATE_CANONICAL_MONEY_SOURCE_RE = re.compile(
    r"\b(?:canonical|explicit)\b.{0,30}\b(?:total|paid|charged|payment|order|amount)\b"
    r"|\b(?:payment[-_ ]?history|total[_ ]?amount|paid[_ ]?amount|charged[_ ]?amount|order[_ ]?amount)\b"
    r"|(?:付款|支付|实付|已付|收取|订单|账单).{0,12}(?:金额|总额|费用)"
    r"|(?:金额|总额|费用).{0,12}(?:付款|支付|实付|已付|收取|订单|账单)",
    re.IGNORECASE,
)


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
    """Default hard-coded deterministic gates used by session policy training."""

    return GateRunner(
        gates=[
            ExperienceCausalSignalGate(mode="enforce"),
            ExperienceSkillReadabilityGate(mode="enforce"),
        ]
    )


def default_experience_gate_contract() -> str:
    """Prompt-facing contract enforced by the default experience gates."""

    return """## Gate Contract (enforced)
Your experience output will be rejected unless every experience satisfies these gates:

1. Causal eligibility
- Non-success trajectories are eligible for experience learning by default,
  including creating new experiences.
- Treat Experience Repair Signal as advisory context, not as an authorization gate:
  legacy Action=skip, Recommended operation=skip, Existing target experience=none,
  or Trigger boundary=none must not suppress a reusable repair for a failed or
  partially failed trajectory.
- Existing target experience=none only means no existing loaded memory should be modified;
  it must not suppress creating a brand-new experience when New experience action=create
  or when the first reward-changing mistake is reusable and preventable.
- Do not output experiences for Outcome=success.

2. Skill-loader readability
- Experience content must include exactly the runtime-facing sections used by the
  skill experience loader: `## Situation`, `## Reminder`, `## Procedure`, and
  `## Anti-pattern`.
- `## Situation` must state applicability, non-applicability, and the runtime
  source binding that lets a future agent decide whether to read/apply the
  experience without executing trigger_code.
- `Does not apply when` must describe a task-pattern mismatch, not a temporal
  stage such as "still reading/writing", "before final_response", or "before
  writes complete"; the skill loader may read the experience before the later
  boundary where it becomes actionable.
- For information/aggregate/list/summary/value requests affected by later
  writes, the experience must preserve the original request-time scope and
  label any post-action/current remaining scope separately.
- Relative wording such as "other", "remaining", "those", "其他", or "剩余" is
  not an explicit exclusion by itself when writes are also being discussed.
- Monetary/value aggregates must bind to the canonical runtime value field when
  available: total/paid/charged/order/payment-history amount fields take
  precedence over reconstructed lower-level unit/segment/item price sums. Do
  not name lower-level price fields as the primary source when a record-level
  total/paid/charged amount is available in runtime evidence.

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
            reason="no non-success source trajectory supports experience learning",
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
        alignable_signals = [
            s
            for s in eligible_signals
            if s.first_wrong_tool not in {"", "none", "无"}
            or s.trigger_boundary not in {"", "none", "无"}
        ]
        if not alignable_signals:
            return None
        if any(_tool_matches_signal(trigger_tool, signal) for signal in alignable_signals):
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="trigger tool does not match first wrong tool or trigger boundary",
            evidence={
                "target_name": target.target_name,
                "trigger_tool": trigger_tool,
                "signals": [signal.to_dict() for signal in alignable_signals],
            },
            retriable=True,
            repair_prompt=(
                "Change trigger_code to use exactly one candidate_tool matching First Wrong "
                "Tool Call.Tool or Trigger boundary; otherwise output no changes."
            ),
        )


@dataclass(slots=True)
class ExperienceRuntimeWordingGate:
    """Reject runtime-facing experiences that leak evaluator/control-plane wording."""

    mode: GateMode = "enforce"
    name: str = "experience_runtime_wording"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        constraint, _ = _experience_constraint_and_trigger(target.after_content, target)
        terms = _runtime_control_plane_terms(constraint)
        if not terms:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience content leaks evaluator/control-plane wording",
            evidence={
                "target_name": target.target_name,
                "terms": terms,
                "content_preview": _preview_text(constraint, limit=500),
            },
            retriable=True,
            repair_prompt=(
                "Rewrite runtime-facing experience content using only runtime semantic sources "
                "such as user request, confirmed target, retrieved record set, source field, "
                "calculation, policy gate, or required user-visible message. Do not mention "
                "evaluation/evaluator/communicate_checks/action_checks/db_check/reward/rubric/"
                "评估/奖励. If no such rewrite is possible, output no changes."
            ),
        )


@dataclass(slots=True)
class ExperienceSkillReadabilityGate:
    """Reject experiences that the skill loader cannot safely search/read."""

    mode: GateMode = "enforce"
    name: str = "experience_skill_readability"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        missing = [
            heading
            for heading in ("Situation", "Reminder", "Procedure", "Anti-pattern")
            if not _markdown_section(content, heading)
        ]
        situation = _markdown_section(content, "Situation")
        situation_lower = situation.lower()
        source_binding_terms = (
            "source binding",
            "source-bound",
            "source field",
            "retrieved",
            "record",
            "scope",
            "policy",
            "calculation",
            "confirmed",
            "源",
            "来源",
            "范围",
            "记录",
            "字段",
            "计算",
            "确认",
            "政策",
        )
        has_source_binding = any(term in situation_lower for term in source_binding_terms)
        applicability_terms = ("applies when", "does not apply", "适用", "不适用")
        has_applicability = any(term in situation_lower for term in applicability_terms)
        temporal_non_applicability = _temporal_non_applicability_terms(situation)
        relative_scope_ambiguity = _experience_relative_write_scope_ambiguity_issue(content)
        line_item_money_source = _experience_line_item_money_source_issue(content)
        if (
            not missing
            and has_source_binding
            and has_applicability
            and not temporal_non_applicability
            and not relative_scope_ambiguity
            and not line_item_money_source
        ):
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience is not readable/applicable enough for skill loader",
            evidence={
                "target_name": target.target_name,
                "missing_sections": missing,
                "has_source_binding": has_source_binding,
                "has_applicability": has_applicability,
                "temporal_non_applicability": temporal_non_applicability,
                "relative_scope_ambiguity": relative_scope_ambiguity,
                "line_item_money_source": line_item_money_source,
                "situation_preview": _preview_text(situation, limit=500),
            },
            retriable=True,
            repair_prompt=(
                "Rewrite the experience in exactly these sections: `## Situation`, "
                "`## Reminder`, `## Procedure`, `## Anti-pattern`. In `## Situation`, "
                "state applies-when, does-not-apply-when, and the runtime source binding "
                "used to decide applicability. `Does not apply when` must be a task-pattern "
                "mismatch, not a temporal stage such as still reading/writing or before "
                "final_response. If relative wording such as other/remaining/其他/剩余 appears "
                "while writes such as cancel/upgrade/modify are also discussed, `Scope ambiguity` "
                "must label both the request-time scope and the post-action/current remaining "
                "scope instead of none. For monetary totals/paid/refund/balance values, bind "
                "the source to explicit canonical total/paid/charged/order/payment amount "
                "fields when present, and use reconstructed line-item sums only as fallback "
                "or cross-check. Put the complete four-section Markdown body in the "
                "`constraint` field (or `content` if that is the only available field); do not "
                "return a production `# name` / `## 规则` block. Do not add trigger_code."
            ),
        )


@dataclass(slots=True)
class ExperienceTriggerRuntimeGate:
    """Reject trigger_code that cannot run in VikingBot constraint mode.

    This is deliberately narrower than the removed trigger-shape gate: it does
    not judge whether a trigger is semantically broad/narrow, only whether the
    proposed trigger can compile under the same restricted Python runtime used
    by VikingBot before constraint reminders are injected.
    """

    mode: GateMode = "enforce"
    name: str = "experience_trigger_runtime"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        _, trigger_code = _experience_constraint_and_trigger(target.after_content, target)
        error = _vikingbot_trigger_runtime_error(trigger_code)
        if not error:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason=f"trigger_code is not accepted by VikingBot constraint runtime: {error}",
            evidence={
                "target_name": target.target_name,
                "trigger_code_preview": _preview_text(trigger_code, limit=500),
            },
            retriable=True,
            repair_prompt=(
                "Rewrite trigger_code so `def should_trigger(ctx): ...` compiles under "
                "the VikingBot constraint runtime. Avoid forbidden syntax such as imports, "
                "exec/eval/open, context mutation, classes/lambdas/async/try/with/while, "
                "and return a strict bool. If no compatible trigger can be written, output "
                "no experience changes."
            ),
        )


@dataclass(slots=True)
class ExperienceRootCausePreventionGate:
    """LLM gate for extracted experience prevention quality.

    This gate is intended for the experience extraction loop only.  It reviews
    the concrete experience draft that would become an injectable pre-tool
    reminder, rather than reviewing compact trajectory evidence or merged plan
    items.  Later merge stages should keep using deterministic gates to avoid
    repeated semantic LLM calls.
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
                "experience root-cause prevention gate failed open: %s",
                exc,
                exc_info=True,
            )
            return GateDecision(
                gate_name=self.name,
                action="warn",
                reason="experience root-cause prevention gate failed open",
                evidence={
                    "target_name": target.target_name,
                    "error": str(exc),
                },
            )

        if not isinstance(parsed, dict):
            return GateDecision(
                gate_name=self.name,
                action="warn",
                reason="experience root-cause prevention gate returned non-object output",
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
            reason=result["reason"] or "experience does not pass counterfactual prevention review",
            evidence=evidence,
            retriable=True,
            repair_prompt=(
                result["repair_prompt"]
                or "Rewrite or remove this experience. The repaired experience must be supported "
                "by the source trajectory, trigger before the first preventable wrong decision, "
                "state the narrow runtime rule that replaces the mistaken decision rule, preserve "
                "any coupled communication/action-scope distinction needed to prevent recurrence, "
                "avoid temporal `Does not apply when` clauses that block skill loading, "
                "and explain what future behavior changes so the next similar session succeeds "
                "without blocking nearby correct behavior."
            ),
        )

    def _get_vlm(self) -> Any:
        if self.vlm is not None:
            return self.vlm
        from openviking_cli.utils.config import get_openviking_config

        self.vlm = get_openviking_config().vlm.get_vlm_instance()
        return self.vlm


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

    return f"""You are a senior counterfactual failure diagnostician for agent experience extraction.

Review ONE proposed experience update.  The proposed experience will be injected
through the skill experience loader in future sessions: the agent searches
experience candidates, reads `## Situation` snippets, and optionally loads the
full experience before acting.

Main question:
If this exact experience had been injected before the source trajectory's first
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
Also fail when the proposed experience conflates action eligibility (whether a
write/mutation may be performed) with benefit eligibility (whether a refund,
compensation, or coverage applies after execution), especially when the user
explicitly accepted no refund/benefit. The experience must not exclude an
action on benefit-eligibility grounds when the policy permits the action itself.

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


_RUNTIME_CONTROL_PLANE_TERM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(pattern, re.IGNORECASE))
    for term, pattern in (
        ("evaluation", r"\bevaluation\b"),
        ("evaluator", r"\bevaluator\b"),
        ("communicate_checks", r"\bcommunicate_checks?\b"),
        ("action_checks", r"\baction_checks?\b"),
        ("db_check", r"\bdb_checks?\b"),
        ("reward", r"\breward\b"),
        ("rubric", r"\brubric\b"),
        ("评估", r"评估"),
        ("奖励", r"奖励"),
    )
)


def _runtime_control_plane_terms(text: str) -> list[str]:
    value = str(text or "")
    return [term for term, pattern in _RUNTIME_CONTROL_PLANE_TERM_PATTERNS if pattern.search(value)]


_TEMPORAL_NON_APPLICABILITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(pattern, re.IGNORECASE))
    for term, pattern in (
        ("before_final_response", r"\b(before|until|not yet|prior to).{0,40}final[_ -]?response\b"),
        (
            "before_final_answer",
            r"\b(before|until|not yet|prior to).{0,40}final (answer|message|reply)\b",
        ),
        (
            "before_writes_complete",
            r"\b(before|until|not yet|prior to).{0,40}(writes?|mutations?|actions?).{0,30}(complete|done|finish)",
        ),
        (
            "still_reading_or_writing",
            r"\bstill (reading|retrieving|writing|executing|processing)\b",
        ),
        ("read_write_stage", r"\b(read|write|mutation|action)[-/ ]?stage\b"),
        ("not_final_response", r"\bnot (yet )?(at )?(the )?final[_ -]?response\b"),
        ("chinese_before_final_response", r"(最终回复前|最终回答前|还没到最终回复|尚未最终回复)"),
        ("chinese_still_reading_or_writing", r"(仍在|还在|正在).{0,8}(读取|查询|写入|执行|处理)"),
        (
            "chinese_before_write_complete",
            r"(写操作|修改|取消|更新).{0,12}(完成前|尚未完成|还没完成)",
        ),
    )
)


def _temporal_non_applicability_terms(text: str) -> list[str]:
    value = str(text or "")
    return [term for term, pattern in _TEMPORAL_NON_APPLICABILITY_PATTERNS if pattern.search(value)]


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
    fields: dict[str, Any] = {}
    if target.gradient is not None:
        fields = dict(getattr(target.gradient.after_file, "extra_fields", {}) or {})
    elif target.plan_item is not None and isinstance(target.plan_item.metadata, dict):
        for key in ("merge_memory_fields", "patch_metadata"):
            value = target.plan_item.metadata.get(key)
            if isinstance(value, dict):
                fields.update(value)
    rendered_fields = _rendered_experience_trigger_fields(content)
    for key, value in rendered_fields.items():
        fields.setdefault(key, value)
    trigger = str(fields.get("trigger_code") or "").strip()
    return str(fields.get("constraint") or fields.get("content") or content or "").strip(), trigger


def _rendered_experience_trigger_fields(content: str) -> dict[str, str]:
    """Parse trigger fields from rendered experience markdown as VikingBot does."""

    text = str(content or "")
    section_match = re.search(
        r"(?ims)^#{1,6}\s*Experience\s+Trigger\s*\n(?P<section>.*?)(?=^#{1,6}\s+|\Z)",
        text,
    )
    if not section_match:
        return {}
    section = section_match.group("section")
    parsed: dict[str, str] = {}
    name_match = re.search(r"(?im)^\s*-?\s*experience_name\s*:\s*(?P<name>[^\n]+)", section)
    if name_match:
        parsed["experience_name"] = name_match.group("name").strip().strip("` ")
    trigger_match = re.search(
        r"(?is)trigger_code\s*:\s*```(?:python)?\s*(?P<code>.*?)\s*```",
        section,
    )
    if trigger_match:
        parsed["trigger_code"] = trigger_match.group("code").strip()
    constraint = (text[: section_match.start()] + text[section_match.end() :]).strip()
    if constraint:
        parsed["constraint"] = constraint
    return parsed


def _vikingbot_trigger_runtime_error(trigger_code: str) -> str:
    if not str(trigger_code or "").strip():
        return "empty trigger_code"
    try:
        from vikingbot.agent.experience_constraints import smoke_test_trigger_code

        smoke_test_trigger_code(trigger_code)
    except Exception as exc:
        return str(exc) or type(exc).__name__
    return ""


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


def _markdown_section(content: str, heading: str) -> str:
    pattern = re.compile(rf"(?ims)^##\s+{re.escape(heading)}\s*\n(?P<body>.*?)(?=^##\s+|\Z)")
    match = pattern.search(content or "")
    return match.group("body").strip() if match else ""


def _experience_relative_write_scope_ambiguity_issue(content: str) -> bool:
    text = str(content or "")
    if not (
        _GATE_AGGREGATE_TERMS_RE.search(text)
        and _GATE_RELATIVE_SCOPE_RE.search(text)
        and _GATE_WRITE_SCOPE_RE.search(text)
    ):
        return False
    situation = _markdown_section(text, "Situation")
    scope_ambiguity = _field_from_section(situation, "Scope ambiguity").strip().lower()
    return scope_ambiguity in _GATE_NONE_VALUES


def _experience_line_item_money_source_issue(content: str) -> bool:
    text = str(content or "")
    if not _GATE_AGGREGATE_TERMS_RE.search(text):
        return False
    if not _GATE_LINE_ITEM_MONEY_SOURCE_RE.search(text):
        return False
    return not bool(_GATE_CANONICAL_MONEY_SOURCE_RE.search(text))


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


def _norm_candidate(value: str) -> str:
    value = str(value or "").strip().strip("` ").lower()
    if value in {"c1", "c2", "c3", "none"}:
        return value
    match = re.match(r"^(c[123]|none)\b", value)
    return match.group(1) if match else value


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
