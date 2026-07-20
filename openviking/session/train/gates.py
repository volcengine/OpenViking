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
import json
import re
from copy import deepcopy
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
            ExperienceNamePolarityGate(mode="enforce"),
            ExperienceSpecificityGate(mode="enforce"),
            ExperienceLanguageBindingGate(mode="enforce"),
            ExperienceEvidenceSafetyGate(mode="enforce"),
            ExperiencePortabilityGate(mode="enforce"),
            ExperiencePlanQualityGate(mode="enforce"),
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
- `## Situation` must include non-empty `Applies when`, `Does not apply when`,
  `Evidence binding`, and `Decision boundary` fields. These let a future agent
  decide whether and when to apply the experience using runtime-visible facts.
- `Does not apply when` must describe a task-pattern mismatch, not a temporal
  stage such as "still reading/writing", "before final_response", or "before
  writes complete"; the skill loader may read the experience before the later
   boundary where it becomes actionable.

3. Specific behavior delta
- Reject generic reminders whose entire behavior change is only to check all
  requirements, ensure compliance, or review carefully. The experience must
  name a discriminating runtime condition and a concrete corrective action.

4. Explicit language binding
- Never infer an output language solely from an audience's geography. Follow an
  explicit language instruction; otherwise preserve the user's language choice
  or ask when the choice materially affects the deliverable.

5. Evidence-safe missing data
- Do not invent required values, use guessed placeholders, or make assumptions
  merely to fill a required schema. Only use a placeholder or assumption when
  the user explicitly permits it; otherwise ask, preserve a clearly marked
  unavailable value, or disclose the limitation.

6. Portable runtime wording
- Replace source-case literals such as example numbers, date/month ranges,
  spreadsheet tab names, and helper script filenames with semantic runtime
  bindings. Keep an exact literal only when an authoritative runtime source
  requires that same invariant across future cases.

7. Final semantic quality
- A final experience is semantically rechecked when merge planning combines
  multiple sources, changes an existing experience, or materially rewrites an
  extracted candidate. It must not turn genre conventions into hidden
  requirements, hardcode factual outputs, combine unrelated repairs, or
  prescribe behavior unsupported by runtime evidence.

If you cannot satisfy this contract, output no experience changes."""


def build_gate_retry_instruction(
    report: GateReport,
    *,
    prior_reports: list[GateReport] | None = None,
) -> str:
    repair = report.retry_repair_prompt()
    if not repair:
        return ""
    targets = report.retriable_rejected_targets()
    lines = [
        "Your previous experience output was rejected by training gates.",
        "Retry only the rejected candidates listed below. Already accepted candidates are retained "
        "outside this retry and must not be repeated or rewritten.",
        "Repair each candidate independently; do not merge candidates or add unrelated experiences.",
        "Return complete operations containing only the repaired rejected candidates. If one cannot "
        "satisfy all gate requirements, omit that candidate.",
    ]
    if targets:
        lines.extend(["", f"Retry targets: {', '.join(targets)}"])
    history = _gate_retry_history(prior_reports or [], targets=set(targets))
    if history:
        lines.extend(
            [
                "",
                "Earlier failed attempts for these candidates (avoid repeating the same defect):",
                history,
            ]
        )
    lines.extend(["", "Current gate repair instructions:", repair])
    return "\n".join(lines)


def _decision_target_name(decision: GateDecision) -> str:
    return str(decision.evidence.get("target_name") or "unknown")


def _gate_retry_history(prior_reports: list[GateReport], *, targets: set[str]) -> str:
    """Return compact candidate-local feedback from earlier failed attempts."""

    lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for attempt_index, prior in enumerate(prior_reports[-2:], start=max(1, len(prior_reports) - 1)):
        for decision in prior.decisions:
            target = _decision_target_name(decision)
            if decision.action != "reject" or (targets and target not in targets):
                continue
            key = (target, decision.gate_name, decision.reason)
            if key in seen:
                continue
            seen.add(key)
            reason = _preview_text(decision.reason, limit=300)
            repair = _preview_text(decision.repair_prompt, limit=300)
            line = f"- attempt={attempt_index} target={target} [{decision.gate_name}]: {reason}"
            if repair:
                line += f" Required repair: {repair}"
            lines.append(line)
            if len(lines) >= 12:
                return "\n".join(lines)
    return "\n".join(lines)


def candidate_retry_draft(draft: Any, *, target_names: set[str]) -> Any:
    """Keep only rejected candidates in the draft shown during a repair retry.

    ExtractLoop drafts are dynamically generated Pydantic models, while tests and
    some callers use resolved operations. This helper handles both shapes and
    fails open to the original draft when candidate names cannot be located.
    """

    if draft is None or not target_names:
        return draft
    result = deepcopy(draft)
    matched = False
    found_candidate_collection = False
    for field_name in ("experiences", "write_uris", "edit_uris", "upsert_operations"):
        values = getattr(result, field_name, None)
        if not isinstance(values, list):
            continue
        found_candidate_collection = True
        selected = [
            value for value in values if _draft_candidate_names(value).intersection(target_names)
        ]
        if selected:
            matched = True
        setattr(result, field_name, selected)
    if not found_candidate_collection or not matched:
        return draft
    for field_name in ("delete_ids", "delete_file_contents"):
        if isinstance(getattr(result, field_name, None), list):
            setattr(result, field_name, [])
    return result


def _draft_candidate_names(value: Any) -> set[str]:
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        try:
            value = dumper(mode="python")
        except TypeError:
            value = dumper()
    elif hasattr(value, "__dict__"):
        value = vars(value)
    names: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "experience_name" and nested:
                names.add(str(nested))
            elif key == "uris" and isinstance(nested, list):
                for uri in nested:
                    text = str(uri or "")
                    if text:
                        names.add(text.rstrip("/").split("/")[-1].removesuffix(".md"))
            elif isinstance(nested, (dict, list, tuple)):
                names.update(_draft_candidate_names(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            names.update(_draft_candidate_names(nested))
    return names


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
                "evaluation/evaluator/outcome_checks/review_result/communicate_checks/"
                "action_checks/db_check/reward/rubric/评估/奖励. If no such rewrite is "
                "possible, output no changes."
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
        missing_sections = [
            heading
            for heading in ("Situation", "Reminder", "Procedure", "Anti-pattern")
            if not _markdown_section(content, heading)
        ]
        situation = _markdown_section(content, "Situation")
        situation_fields = {
            "Applies when": _field_from_section(situation, "Applies when"),
            "Does not apply when": _field_from_section(situation, "Does not apply when"),
            "Evidence binding": (
                _field_from_section(situation, "Evidence binding")
                or _field_from_section(situation, "Source binding")
            ),
            "Decision boundary": _field_from_section(situation, "Decision boundary"),
        }
        missing_situation_fields = [
            field_name for field_name, value in situation_fields.items() if not value.strip()
        ]
        temporal_non_applicability = _temporal_non_applicability_terms(
            situation_fields["Does not apply when"]
        )
        if not missing_sections and not missing_situation_fields and not temporal_non_applicability:
            return None

        issues: list[str] = []
        repair_steps: list[str] = []
        if missing_sections:
            issues.append("missing sections: " + ", ".join(missing_sections))
            repair_steps.append(
                "include exactly these non-empty sections: `## Situation`, `## Reminder`, "
                "`## Procedure`, and `## Anti-pattern`"
            )
        if missing_situation_fields:
            issues.append("missing Situation fields: " + ", ".join(missing_situation_fields))
            repair_steps.append(
                "add non-empty `Applies when`, `Does not apply when`, `Evidence binding`, "
                "and `Decision boundary` bullets under `## Situation`"
            )
        if temporal_non_applicability:
            issues.append("temporal non-applicability: " + ", ".join(temporal_non_applicability))
            repair_steps.append(
                "replace `Does not apply when` with the closest task-pattern mismatch; do not "
                "describe an execution stage such as still reading/writing or before a final reply"
            )
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience readability contract failed: " + "; ".join(issues),
            evidence={
                "target_name": target.target_name,
                "missing_sections": missing_sections,
                "missing_situation_fields": missing_situation_fields,
                "temporal_non_applicability": temporal_non_applicability,
                "situation_preview": _preview_text(situation, limit=500),
            },
            retriable=True,
            repair_prompt=(
                "Repair only these observed issues: " + "; ".join(repair_steps) + ". Put only "
                "the section bodies in the corresponding `situation`, `reminder`, `procedure`, "
                "and `anti_pattern` fields; the storage template adds the Markdown headings. "
                "Preserve already-correct, evidence-backed content and do not add trigger_code."
            ),
        )


@dataclass(slots=True)
class ExperienceNamePolarityGate:
    """Reject names that describe the opposite of the runtime behavior."""

    mode: GateMode = "enforce"
    name: str = "experience_name_polarity"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        target_name = str(target.target_name or "")
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (
                _markdown_section(content, "Reminder"),
                _markdown_section(content, "Procedure"),
                _markdown_section(content, "Anti-pattern"),
            )
        )
        contradiction = _experience_name_polarity_contradiction(target_name, runtime_rule)
        if contradiction is None:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience name describes behavior that its runtime rule prohibits",
            evidence={
                "target_name": target_name,
                "contradiction": contradiction,
            },
            retriable=True,
            repair_prompt=(
                "Rename this experience so its name describes the desired runtime behavior, "
                "not the anti-pattern that the body prohibits. Preserve the evidence-backed "
                "four-section body unless a wording change is required for consistency."
            ),
        )


@dataclass(slots=True)
class ExperienceSpecificityGate:
    """Reject generic requirement-checking slogans with no reusable discriminator."""

    mode: GateMode = "enforce"
    name: str = "experience_specificity"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        independent_concerns = _independent_experience_concerns(content)
        if len(independent_concerns) > 1:
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience merges independent failure-repair concerns",
                evidence={
                    "target_name": target.target_name,
                    "independent_concerns": independent_concerns,
                },
                retriable=True,
                repair_prompt=(
                    "Keep exactly one evidenced concern in this experience. Split concerns that "
                    "use different runtime evidence or corrective actions into separate narrow "
                    "experiences, or remove unsupported concerns."
                ),
            )
        situation = _markdown_section(content, "Situation")
        reminder = _markdown_section(content, "Reminder")
        procedure = _markdown_section(content, "Procedure")
        compound_actions = _compound_repair_actions(reminder)
        if len(compound_actions) >= 4:
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience bundles several independent repairs into one reminder",
                evidence={
                    "target_name": target.target_name,
                    "repair_actions": compound_actions,
                    "reminder_preview": _preview_text(reminder, limit=400),
                },
                retriable=True,
                repair_prompt=(
                    "Keep one root failure and one behavior change. Split parallel repairs that "
                    "use different evidence or could fail independently into separate experiences. "
                    "A multi-step procedure is allowed only when every step implements the same "
                    "single corrective decision."
                ),
            )
        generic_signals = _generic_experience_signals(
            situation=situation,
            reminder=reminder,
            procedure=procedure,
            target_name=target.target_name,
        )
        if len(generic_signals) < 2:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience is a generic requirement-checking workflow",
            evidence={
                "target_name": target.target_name,
                "generic_signals": generic_signals,
                "reminder_preview": _preview_text(reminder, limit=300),
                "procedure_preview": _preview_text(procedure, limit=500),
            },
            retriable=True,
            repair_prompt=(
                "Replace the generic checklist with one evidenced failure pattern: name the "
                "runtime discriminator, the earliest decision boundary for that pattern, and "
                "the smallest corrective action. If no behavior more specific than checking "
                "all requirements is supported, output no changes."
            ),
        )


@dataclass(slots=True)
class ExperienceLanguageBindingGate:
    """Reject geography-to-language assumptions that can override user intent."""

    mode: GateMode = "enforce"
    name: str = "experience_language_binding"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (_markdown_section(content, "Reminder"), _markdown_section(content, "Procedure"))
        )
        matches: list[str] = []
        for pattern in _GEOGRAPHY_LANGUAGE_INFERENCE_PATTERNS:
            matches.extend(
                match.group(0)
                for match in pattern.finditer(runtime_rule)
                if not _language_inference_is_prohibited(runtime_rule, match.start())
            )
        target_name = str(target.target_name or "")
        if (
            _LANGUAGE_AUDIENCE_TARGET_RE.search(target_name)
            and _GEOGRAPHY_AUDIENCE_RE.search(content)
            and not _LANGUAGE_INFERENCE_PROHIBITION_RE.search(target_name)
        ):
            matches.append(target_name)
        matches.extend(
            match.group(0)
            for match in _AUDIENCE_IMPLIES_LANGUAGE_RE.finditer(content)
            if not _language_inference_is_prohibited(content, match.start())
        )
        if not matches:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience infers output language from audience geography",
            evidence={
                "target_name": target.target_name,
                "matches": matches[:5],
            },
            retriable=True,
            repair_prompt=(
                "Bind output language to an explicit user language instruction. Geography or "
                "audience locale may change examples, spelling, or conventions, but must not "
                "alone select a language. If no explicit language is available, preserve the "
                "user's language choice or ask when necessary."
            ),
        )


@dataclass(slots=True)
class ExperienceEvidenceSafetyGate:
    """Reject experience rules that turn missing evidence into invented content."""

    mode: GateMode = "enforce"
    name: str = "experience_evidence_safety"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (_markdown_section(content, "Reminder"), _markdown_section(content, "Procedure"))
        )
        unsafe_matches = [
            match.group(0)
            for pattern in _UNSUPPORTED_CONTENT_FILL_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if unsafe_matches and not _EXPLICIT_PLACEHOLDER_PERMISSION_RE.search(content):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason=(
                    "experience permits filling missing evidence with assumptions or placeholders"
                ),
                evidence={
                    "target_name": target.target_name,
                    "matches": unsafe_matches,
                },
                retriable=True,
                repair_prompt=(
                    "Remove any instruction to guess, assume, or insert placeholders for missing "
                    "required evidence unless the user explicitly permits that behavior. Prefer "
                    "an explicit unavailable value, a clarification request, or a disclosed "
                    "limitation."
                ),
            )
        restatement_matches = [
            match.group(0)
            for pattern in _UNREQUESTED_CONTEXT_RESTATEMENT_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if restatement_matches and not _EXPLICIT_CONTEXT_RESTATEMENT_REQUEST_RE.search(content):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience turns user-provided assumptions or exclusions into unrequested output",
                evidence={
                    "target_name": target.target_name,
                    "matches": restatement_matches[:5],
                },
                retriable=True,
                repair_prompt=(
                    "Use assumptions, exclusions, and pre-handled topics as reasoning constraints. "
                    "Do not require copying or restating them in the deliverable unless the runtime "
                    "request explicitly asks for that visible content."
                ),
            )
        if (
            _FIXED_CAPACITY_ARTIFACT_RE.search(content)
            and _DENSITY_AS_OVERFLOW_RE.search(runtime_rule)
            and not _EXPLICIT_OVERFLOW_RESOLUTION_RE.search(runtime_rule)
        ):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience resolves fixed-capacity conflicts by cramming content",
                evidence={
                    "target_name": target.target_name,
                    "runtime_rule_preview": _preview_text(runtime_rule, limit=500),
                },
                retriable=True,
                repair_prompt=(
                    "Keep the requirement-to-region mapping, but preserve readability. When all "
                    "required content cannot fit at a readable density, follow an explicit priority, "
                    "surface the conflict, or request a scope/layout decision instead of shrinking "
                    "text or silently packing dense nested content."
                ),
            )
        scope_matches = [
            match.group(0)
            for pattern in _UNSUPPORTED_SCOPE_EXPANSION_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if not scope_matches:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience turns an unrequested convention into a mandatory requirement",
            evidence={
                "target_name": target.target_name,
                "matches": scope_matches,
            },
            retriable=True,
            repair_prompt=(
                "Do not make content mandatory merely because it is conventional for the artifact "
                "type. Bind the requirement to the user request, an authoritative source, or an "
                "observable task schema; otherwise make it optional or output no experience."
            ),
        )


@dataclass(slots=True)
class ExperiencePortabilityGate:
    """Reject source-case literals that should be represented as runtime bindings."""

    mode: GateMode = "enforce"
    name: str = "experience_portability"

    def applies_to(self, target: GateTarget) -> bool:
        return target.memory_type == "experiences" and target.after_content.strip() != ""

    async def evaluate(self, target: GateTarget) -> GateDecision | None:
        content, _ = _experience_constraint_and_trigger(target.after_content, target)
        runtime_rule = "\n".join(
            (_markdown_section(content, "Reminder"), _markdown_section(content, "Procedure"))
        )
        auxiliary_artifacts = [
            match.group(0)
            for pattern in _MANDATORY_AUXILIARY_ARTIFACT_PATTERNS
            for match in pattern.finditer(runtime_rule)
        ]
        if auxiliary_artifacts:
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="experience mandates an unrequested auxiliary artifact or fixed helper file",
                evidence={
                    "target_name": target.target_name,
                    "matches": auxiliary_artifacts[:5],
                },
                retriable=True,
                repair_prompt=(
                    "Keep the verification behavior but do not require a fixed helper filename, "
                    "visible intermediate file, or extra deliverable unless the runtime request or "
                    "tool contract explicitly requires it. Use an in-context requirement map or any "
                    "available scratch mechanism, and deliver only requested artifacts."
                ),
            )
        matches = [
            match.group(0)
            for pattern in _NON_PORTABLE_CASE_LITERAL_PATTERNS
            for match in pattern.finditer(content)
        ]
        if not matches:
            return None
        return GateDecision(
            gate_name=self.name,
            action="reject",
            reason="experience embeds source-case literals instead of semantic runtime bindings",
            evidence={
                "target_name": target.target_name,
                "matches": matches,
            },
            retriable=True,
            repair_prompt=(
                "Replace example numbers, date/month ranges, sheet/tab names, and helper script "
                "filenames with semantic roles read from the user request, source workbook, or "
                "available runtime tools. Preserve exact literals only when a cited authoritative "
                "runtime source makes them invariant across future cases."
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
        reconsideration: dict[str, Any] | None = None
        if not result["pass"] and _is_other_failures_only_rejection(result):
            return GateDecision(
                gate_name=self.name,
                action="warn",
                reason="candidate-local rule overruled an unrelated-failures rejection",
                evidence={
                    "target_name": target.target_name,
                    "original_result": result,
                },
            )
        if not result["pass"] and _needs_candidate_local_reconsideration(result):
            reconsideration = await self._reconsider_candidate_local_rejection(
                target=target,
                original_result=result,
            )
            if reconsideration is not None and not reconsideration["uphold_rejection"]:
                return GateDecision(
                    gate_name=self.name,
                    action="warn",
                    reason="candidate-local review overruled an inconsistent rejection",
                    evidence={
                        "target_name": target.target_name,
                        "original_result": result,
                        "reconsideration": reconsideration,
                    },
                )
            if reconsideration is not None:
                result = {
                    **result,
                    "root_cause_quality": reconsideration["root_cause_quality"],
                    "reason": reconsideration["reason"],
                    "repair_prompt": reconsideration["repair_prompt"],
                }
        evidence = {
            "target_name": target.target_name,
            "pass": result["pass"],
            "root_cause_quality": result["root_cause_quality"],
            "reason": result["reason"],
            "expected_behavior_change": result["expected_behavior_change"],
            "risks": result["risks"],
        }
        if reconsideration is not None:
            evidence["reconsideration"] = reconsideration
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

    async def _reconsider_candidate_local_rejection(
        self,
        *,
        target: GateTarget,
        original_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        prompt = _experience_candidate_local_reconsideration_prompt(
            target=target,
            original_result=original_result,
            max_policy_chars=self.max_policy_chars,
        )
        try:
            response = await self._get_vlm().get_completion_async(
                prompt=prompt,
                thinking=False,
            )
            parsed = parse_json_from_response(response)
        except Exception as exc:
            logger.warning("experience candidate-local reconsideration failed: %s", exc)
            return None
        if not isinstance(parsed, dict):
            return None
        return _normalize_candidate_local_reconsideration(parsed)


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
If this exact experience had been injected before the earliest preventable
decision boundary for the ONE failure pattern it claims to repair,
would it reliably prevent or recover from that pattern without breaking nearby
correct cases? Other independent failures may remain; this candidate is not
required to make the entire source trajectory succeed by itself.

Candidate-local review rule:
- Judge only whether this candidate completely repairs its claimed root failure.
- NEVER reject a narrow, otherwise valid candidate merely because the source
  trajectory contains other independent failures. Those should become separate
  experiences.
- The relevant "first" or "earliest" boundary is local to this candidate's
  claimed failure pattern. This is the candidate-local preventable wrong decision;
  it is not necessarily the first failure anywhere in the source trajectory.
- Reject broad candidates that merge failures with different evidence,
  boundaries, or repairs. Do not demand that a narrow candidate absorb them.
- If the only criticism is that sibling criteria, gaps, or failures remain,
  return `pass: true`. Judge those siblings as separate candidates.

Pass only when all are true:
1. Direct evaluation evidence supports the failed outcome or unmet requirement.
   Any claimed internal cause is separately supported by an observation,
   decision, action, verification, or output in the source trajectory.
2. The experience is directly preventive: it changes a future tool call, missing
   tool call, confirmation, calculation, policy branch, write, communication, or
   final answer before or at the failing boundary.
3. The experience is injectable: it is a runtime reminder, not a case audit,
   evaluator diagnosis, broad SOP, hidden answer, or generic "check everything" rule.
4. `## Situation` is specific enough for the skill loader path: it tells a future
   agent when to read/apply the experience, when not to apply it, and which
   runtime source binding supports the rule.
5. The experience covers one reusable root failure pattern. Multiple symptoms
   are combined only when they share the same first divergence, decisive
   evidence, decision boundary, and minimal repair. Do not fail an otherwise
   complete experience merely because the trajectory also contains unrelated
   failures that should become separate experiences.
6. When evaluation proves a reusable requirement failure but the internal cause
   is unknown, a narrow verification reminder at the earliest observable output
   or action boundary is acceptable; do not invent a hidden cause.
7. `Does not apply when` names a real task-pattern mismatch, not a temporal
   loader stage such as still reading/writing, before final_response, or before
   writes complete. Temporal wording would make the future agent skip reading an
   experience that must be available from task start.
8. Every mandatory behavior is supported by the user request, an authoritative
   source, or observable runtime evidence. A genre convention, evaluator-only
   preference, or hardcoded factual value cannot become a hidden requirement.
9. A mandatory output element is guarded by the same explicit runtime requirement
   that justifies it. Do not infer a required section, artifact, field, or language
   from a merely related input concept, audience, locale, or genre convention.
10. Runtime bindings name semantic roles and read their exact values from the future
   request, source, or available tools. Never require source-case example numbers,
   dates, tab names, paths, filenames, or tool identifiers in the experience or in
   a repair instruction when a capability or semantic source role is sufficient.

Fail when the proposed experience only summarizes the task, fires too late,
uses unsupported or hidden facts not present in the source/comparison
trajectories, overfits case literals, misses the root decision rule, merges
unrelated failures into a broad checklist, uses temporal non-applicability to
avoid skill loading, lacks a
concrete future behavior change, mandates unrequested conventional content,
hardcodes factual outputs as a substitute for implementation, or would likely
harm correct behavior.

When suggesting a repair, preserve these same constraints: request a semantic
runtime binding and one behavior delta, never source-case literals or a broader
set of requirements.

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
wrong_scope, mixed_root_causes, missing_source_binding,
missing_behavior_change, not_injectable, over_broad,
temporal_non_applicability, unsafe, unclear.
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


def _experience_candidate_local_reconsideration_prompt(
    *,
    target: GateTarget,
    original_result: dict[str, Any],
    max_policy_chars: int,
) -> str:
    trajectory = target.trajectory
    trajectory_content = _preview_text(
        trajectory.content if trajectory is not None else "",
        limit=max_policy_chars,
    )
    candidate = _preview_text(target.after_content or "", limit=max_policy_chars)
    return f"""You are checking whether a previous experience-gate rejection is logically consistent.

The candidate is allowed to repair ONE evidenced reusable failure pattern from a
trajectory with several independent failures. It need not repair the earliest
failure of the whole session and need not make the whole session succeed.

Uphold the rejection only if the candidate itself is unsupported, merges
different evidence/boundaries/repairs, is too late for its own claimed pattern,
is generic or non-injectable, or incompletely repairs its own claimed pattern.
Do not uphold merely because other failures remain or another failure occurred
earlier in the session.

Return JSON only:
{{
  "uphold_rejection": true,
  "root_cause_quality": "mixed_root_causes",
  "reason": "candidate-local reason",
  "repair_prompt": "one concise repair, or empty when rejection is overruled"
}}

## Original rejection
{json.dumps(original_result, ensure_ascii=False, default=str)}

## Source trajectory
{trajectory_content}

## Proposed experience
{candidate}
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


def _needs_candidate_local_reconsideration(result: dict[str, Any]) -> bool:
    if result.get("root_cause_quality") not in {
        "wrong_scope",
        "mixed_root_causes",
        "too_late_boundary",
        "over_broad",
    }:
        return False
    reason = str(result.get("reason") or "")
    return any(pattern.search(reason) for pattern in _CANDIDATE_LOCAL_REJECTION_PATTERNS)


def _is_other_failures_only_rejection(result: dict[str, Any]) -> bool:
    """Detect a rejection that incorrectly requires one candidate to fix sibling failures."""

    if result.get("root_cause_quality") not in {
        "wrong_scope",
        "mixed_root_causes",
        "missing_behavior_change",
        "not_preventive",
        "too_late_boundary",
        "over_broad",
        "unclear",
    }:
        return False
    reason = str(result.get("reason") or "")
    if re.search(
        r"(?i)\b(?:unsupported|generic|non-injectable|overly broad|merges?\s+(?:scope|"
        r"(?:multiple|distinct)\s+(?:failures?|concerns?))|fails?\s+to\s+(?:tie|bind)\b|"
        r"(?:no|lacks?\s+(?:a\s+)?)concrete\s+behavior\s+change\s+(?:for|to)\s+(?:its|the\s+"
        r"candidate(?:'s)?|the\s+claimed)|incomplet(?:e|ely)\s+(?:repairs?|covers?)\s+"
        r"(?:its|the candidate(?:'s)?)\s+(?:own\s+)?claimed scope)\b|"
        r"(?:不受支持|过于泛化|不可注入|混合多个|未绑定|缺少具体行为变化|未完整修复自身|自身声称的范围)",
        reason,
    ):
        return False
    return bool(
        re.search(
            r"(?is)\b(?:only|solely)\s+(?:addresses|targets|covers|focuses on)\b.{0,500}"
            r"\b(?:other|additional|remaining|separate)\b.{0,160}"
            r"\b(?:failures?|issues?|errors?|failed\s+criteria|gaps?|requirements?)\b",
            reason,
        )
        or re.search(
            r"(?is)\b(?:only|solely)\s+(?:mandates|requires|repairs|prevents)\b.{0,500}"
            r"\b(?:does\s+not|doesn't|fails?\s+to)\s+(?:address|cover|repair|prevent|specify)\b"
            r".{0,200}\b(?:other|additional|remaining|separate|two|three)\b.{0,160}"
            r"\b(?:failures?|issues?|errors?|failed\s+criteria|gaps?|requirements?)\b",
            reason,
        )
        or re.search(
            r"(?s)(?:仅|只)(?:针对|覆盖|处理|修复|关注).{0,300}"
            r"(?:未|没有)(?:覆盖|处理|修复|整合).{0,160}"
            r"(?:其他|另外|其余|剩余|同一决策边界).{0,120}(?:失败|问题|错误|缺失)",
            reason,
        )
    )


def _normalize_candidate_local_reconsideration(parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "uphold_rejection": bool(parsed.get("uphold_rejection", True)),
        "root_cause_quality": str(parsed.get("root_cause_quality") or "unclear"),
        "reason": str(parsed.get("reason") or ""),
        "repair_prompt": str(parsed.get("repair_prompt") or ""),
    }


def _preview_text(text: str, *, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


_GENERIC_EXPERIENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "generic_requirement_reminder",
            r"\b(?:systematically\s+)?(?:check|verify|review|ensure|map)\s+(?:off\s+)?"
            r"(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]requested\s+|user\s+)?"
            r"requirements?\b",
        ),
        (
            "generic_requirement_checklist",
            r"\bcreate\s+a\s+checklist\s+from\s+(?:the\s+)?user(?:'s)?\s+"
            r"(?:explicit\s+)?requirements?\b",
        ),
        (
            "generic_full_requirement_checklist",
            r"\b(?:create|compile|build)\s+(?:a\s+|the\s+)?(?:full\s+|complete\s+|structured\s+)?"
            r"checklist\s+(?:of|from)\s+(?:all\s+|every\s+|explicit\s+|user\s+)*"
            r"requirements?\b",
        ),
        (
            "generic_required_item_checklist",
            r"\b(?:create|compile|build)\s+(?:a\s+|the\s+)?(?:full\s+|complete\s+|structured\s+)?"
            r"checklist\s+(?:of|from)\s+(?:all\s+)?(?:user[- ]specified\s+)?"
            r"(?:required\s+)?(?:content\s+)?(?:items?|topics?|requirements?)\b",
        ),
        (
            "generic_universal_required_content",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"required\s+content\s+items?\b",
        ),
        (
            "generic_document_required_content_scope",
            r"\bcreat(?:e|ing)\s+(?:a\s+)?documents?\b.{0,100}"
            r"\bexplicit\s+(?:list\s+of\s+)?required\s+content\s+items?\b",
        ),
        (
            "generic_mark_implemented",
            r"\bfor\s+each\s+requirement.{0,80}\bmark(?:ed)?\s+as\s+implemented\b",
        ),
        (
            "generic_requirement_placeholder",
            r"\bfor\s+each\s+requirement.{0,100}\b(?:placeholder|section|content element)\b",
        ),
        (
            "generic_requirement_inventory",
            r"\b(?:extract|list|enumerate)(?:\s+and\s+(?:extract|list|enumerate))?\s+"
            r"(?:all|every|each)\s+"
            r"(?:explicit\s+)?(?:user[- ]requested\s+|user\s+)?requirements?\b",
        ),
        (
            "generic_content_requirement_inventory",
            r"\b(?:inventory|extract|list|map)\s+(?:all|every|each)\s+"
            r"(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"(?:(?:slide|document|strategy|deliverable)\s+)?content\s+requirements?\b",
        ),
        (
            "generic_universal_content_requirement_scope",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"(?:(?:slide|document|strategy|deliverable)\s+)?content\s+requirements?\b",
        ),
        (
            "generic_universal_required_topics",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?required\s+topics?\b",
        ),
        (
            "generic_compile_review_checklist",
            r"\bcompile(?:\s+and\s+(?:review|verify|check))?\s+(?:a\s+|the\s+)?"
            r"(?:full\s+|complete\s+)?checklist\s+of\s+(?:all\s+|every\s+)?"
            r"(?:explicit\s+)?requirements?\b",
        ),
        (
            "generic_universal_requirement_scope",
            r"\b(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"requirements?\b(?!\s+(?:group|category|cluster|section|region)\b)",
        ),
        (
            "generic_requirement_mapping",
            r"\bmap\s+(?:all|every|each)\s+(?:explicit\s+)?(?:user[- ]specified\s+|user\s+)?"
            r"requirements?\b(?!\s+(?:group|category|cluster|section|region)\b)",
        ),
        (
            "generic_line_by_line_requirement_check",
            r"\b(?:check|verify|compare)\s+(?:all|every|each)?\s*(?:explicit\s+)?"
            r"(?:user[- ]specified\s+|user\s+)?requirements?\s+"
            r"(?:line[- ]by[- ]line|one[- ]by[- ]one)\b",
        ),
        (
            "generic_cross_verify_requirements",
            r"\bcross[- ]verify\s+(?:each|all|every)\s+requirements?\b",
        ),
        (
            "generic_compliance_slogan",
            r"\b(?:ensure|verify)\s+(?:full|complete)\s+compliance\b",
        ),
        ("generic_review_slogan", r"\breview\s+(?:it\s+)?(?:carefully|thoroughly)\b"),
        ("chinese_generic_check_all", r"(逐项|逐一)?(检查|核对|验证).{0,12}(全部|所有)(要求|需求)"),
        ("chinese_generic_compliance", r"确保.{0,8}(完全|全部)(合规|符合要求)"),
        (
            "generic_document_requirement_scope",
            r"\bcreat(?:e|ing)\s+(?:a\s+)?(?:document|artifact|deliverable|structured content)"
            r".{0,100}\b(?:explicit|enumerated|listed)\b.{0,40}\brequirements?\b",
        ),
        (
            "generic_artifact_requirement_scope",
            r"\b(?:programmatically\s+)?(?:generat(?:e|ing)|creat(?:e|ing)|finaliz(?:e|ing))\s+"
            r"(?:a\s+)?(?:structured\s+)?(?:deliverable\s+)?"
            r"(?:artifacts?|documents?|deliverables?)\b.{0,100}"
            r"\b(?:explicit|enumerated|listed)\b.{0,40}\brequirements?\b",
        ),
        (
            "generic_component_inventory",
            r"\b(?:extract|list|enumerate)(?:\s+and\s+(?:extract|list|enumerate))?\s+"
            r"(?:all|every|each)\s+(?:explicitly\s+required\s+|user[- ]specified\s+|"
            r"user[- ]requested\s+)?(?:tables?|sheets?|sections?|components?)\b",
        ),
        (
            "generic_component_checklist",
            r"\bchecklist\s+(?:of|from|containing)\s+(?:all|every|each)?\s*"
            r"(?:explicitly\s+required\s+|user[- ]specified\s+|user[- ]requested\s+)?"
            r"(?:tables?|sheets?|sections?|components?)\b",
        ),
    )
)


def _generic_experience_signals(
    *,
    situation: str,
    reminder: str,
    procedure: str,
    target_name: str = "",
) -> list[str]:
    text = "\n".join((situation, reminder, procedure))
    signals = [name for name, pattern in _GENERIC_EXPERIENCE_PATTERNS if pattern.search(text)]
    if _GENERIC_EXPERIENCE_NAME_RE.search(str(target_name or "")):
        signals.append("generic_requirement_name")
    return signals


_GENERIC_EXPERIENCE_NAME_RE = re.compile(
    r"(?i)\b(?:validate|verify|check|address|cover|inventory|list|map)[_ -]+"
    r"(?:(?:all|every|each|explicit|required|user(?:[_ -]+specified)?)[_ -]+)*"
    r"(?:(?:slide|document|strategy|deliverable)[_ -]+)?"
    r"(?:requirements?|topics?|content[_ -]+requirements?)(?=$|[_ -])"
)


_INDEPENDENT_EXPERIENCE_CONCERN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "internal_output_cleanup",
        re.compile(
            r"(?is)(?:internal\s+(?:tools?|workflows?|metadata|paths?|process)|"
            r"内部(?:工具|流程|元数据|路径|文件)).{0,160}(?:final|user-facing|最终|用户)",
        ),
    ),
    (
        "source_reference_completeness",
        re.compile(
            r"(?is)(?:sources?|references?|citations?|supporting materials|来源|参考文献|引用)"
            r".{0,120}(?:include|provide|verify|missing|包含|提供|核对|缺失)",
        ),
    ),
)


_COMPOUND_REPAIR_ACTION_RE = re.compile(
    r"(?i)\b(?:define|distinguish|differentiate|address|cover|compare|include|document|"
    r"classify|explain|summarize|cite|calculate|reconcile|label|disclose|map|validate|"
    r"verify|confirm|note)\b"
)


def _compound_repair_actions(reminder: str) -> list[str]:
    """Find parallel corrective verbs compressed into the Reminder section."""

    return list(
        dict.fromkeys(
            match.group(0).lower() for match in _COMPOUND_REPAIR_ACTION_RE.finditer(reminder)
        )
    )


def _independent_experience_concerns(content: str) -> list[str]:
    value = str(content or "")
    return [
        name for name, pattern in _INDEPENDENT_EXPERIENCE_CONCERN_PATTERNS if pattern.search(value)
    ]


_CANDIDATE_LOCAL_REJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bonly\s+(?:addresses|targets|covers|focuses on)\b",
        r"\bignores?\s+(?:the\s+)?(?:other|additional|co-occurring)\b",
        r"\bdoes not (?:address|cover)\s+(?:the\s+)?(?:other|all|remaining)\b",
        r"\bfirst preventable wrong decision\s+(?:was|is)\b",
        r"(?:仅|只)(?:覆盖|处理|修复|关注).{0,20}(?:其他|多个|单一|症状|问题)",
        r"忽略.{0,20}(?:其他|多个|独立)(?:失败|问题|错误)",
        r"最早.{0,20}(?:错误|失败|决策)",
    )
)


_GEOGRAPHY_LANGUAGE_INFERENCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"\b(?:use|write|output|produce)\s+(?:all\s+content\s+)?(?:in\s+)?"
        r"(?:English|Chinese|Spanish|French|German|Japanese|Korean)\b.{0,100}"
        r"\b(?:audience|readers?|users?)\b",
        r"\b(?:audience|readers?|users?)\b.{0,100}\b(?:US|U\.S\.|UK|China|Chinese|"
        r"Japan|Japanese|Korea|Korean|France|French|Germany|German)[ -]?(?:based)?\b"
        r".{0,180}\b(?:use|using|write|output|produce|choose|select|prioritize)\b.{0,100}"
        r"\b(?:English|Chinese|Spanish|French|German|Japanese|Korean)\b",
        r"\b(?:English|Chinese|Spanish|French|German|Japanese|Korean)\s+for\s+"
        r"(?:US|U\.S\.|UK|China|Japan|Korea|France|Germany)[ -]?based\b",
        r"\b(?:US|U\.S\.|UK|China|Japan|Korea|France|Germany)[ -]?based\s+"
        r"(?:audience|readers?|users?)\b.{0,100}\b(?:use|write|output|produce)\b.{0,40}"
        r"\b(?:English|Chinese|Spanish|French|German|Japanese|Korean)\b",
    )
)


_LANGUAGE_AUDIENCE_TARGET_RE = re.compile(
    r"(?i)\b(?:match|select|choose|determine)[_ -]+(?:output[_ -]+)?language"
    r"[_ -]+(?:to|from|for|by)[_ -]+(?:audience|region|locale)\b"
)


_AUDIENCE_IMPLIES_LANGUAGE_RE = re.compile(
    r"(?i)\b(?:target\s+)?audience\b.{0,90}\b(?:implies?|indicates?|determines?|"
    r"suggests?)\b.{0,40}\b(?:a\s+)?(?:specific\s+)?language\b|"
    r"\blanguage[- ]implying\s+(?:target\s+)?audience\b|"
    r"\baudience\s+descriptions?\b.{0,50}\bimply\b.{0,30}\blanguage\b|"
    r"\b(?:determine|select|choose|infer)\b.{0,50}\b(?:output\s+)?language\b"
    r".{0,80}\b(?:from|based\s+on)\b.{0,30}\b(?:audience|locale|region)\b|"
    r"(?:根据|基于|按照).{0,20}(?:受众|读者|地区|地域|国家|区域).{0,20}"
    r"(?:确定|选择|决定|推断).{0,20}(?:输出)?语言|"
    r"(?:受众|读者|地区|地域|国家|区域).{0,20}(?:暗示|意味着|决定|对应).{0,12}语言"
)


_LANGUAGE_INFERENCE_PROHIBITION_RE = re.compile(
    r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not|avoid|prohibit)\b|"
    r"(?:不要|不得|禁止|避免)"
)


def _language_inference_is_prohibited(text: str, match_start: int) -> bool:
    """Return whether a matched inference is the object of a nearby prohibition."""

    prefix = text[max(0, match_start - 100) : match_start]
    prohibition = None
    for candidate in _LANGUAGE_INFERENCE_PROHIBITION_RE.finditer(prefix):
        prohibition = candidate
    if prohibition is None:
        return False
    between = prefix[prohibition.end() :]
    return "\n" not in between and len(between) <= 80


def _experience_name_polarity_contradiction(
    target_name: str,
    runtime_rule: str,
) -> str | None:
    normalized_name = re.sub(r"[_-]+", " ", target_name).strip().lower()
    pairs = (
        (
            r"^skip\b",
            r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+skip\b|\bavoid\s+skipping\b",
        ),
        (
            r"^omit\b",
            r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+omit\b|\bavoid\s+omitting\b",
        ),
        (
            r"^ignore\b",
            r"(?i)\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+ignore\b|\bavoid\s+ignoring\b",
        ),
        (r"^跳过", r"(?:不要|不得|禁止|避免)跳过"),
        (r"^(?:忽略|省略)", r"(?:不要|不得|禁止|避免)(?:忽略|省略)"),
    )
    for name_pattern, rule_pattern in pairs:
        if re.search(name_pattern, normalized_name) and re.search(rule_pattern, runtime_rule):
            return f"name={target_name!r}; prohibited_action={name_pattern}"
    return None


_GEOGRAPHY_AUDIENCE_RE = re.compile(
    r"(?i)\b(?:US|U\.S\.|UK|China|Japan|Korea|France|Germany)[ -]?(?:based)?\b|"
    r"\b(?:regional?|geograph(?:y|ic|ical)|locale)\s+(?:audience|readers?|users?)\b"
)


_UNSUPPORTED_CONTENT_FILL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bif\b[^.\n]{0,60}\b(?:unsure|uncertain|unknown|missing|unavailable)\b"
        r"[^.\n]{0,120}\b(?:add|insert|use|fill)\b[^.\n]{0,60}\bplaceholders?\b",
        r"\bmake\s+(?:a\s+)?(?:reasonable\s+)?(?:explicit\s+)?assumptions?\b",
        r"(?:不确定|缺失|未知|无法获取).{0,60}(?:填入|使用|添加).{0,30}(?:占位符|假设值)",
    )
)


_UNSUPPORTED_SCOPE_EXPANSION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:require|requires|required|must|include|add)\b[^.\n]{0,160}"
        r"\b(?:even\s+(?:when|if)|although)\b[^.\n]{0,100}"
        r"\b(?:not\s+explicitly\s+(?:listed|required|requested|specified)|not\s+requested)\b",
        r"\bdo\s+not\s+skip\b[^.\n]{0,160}\b(?:just\s+because|even\s+if)\b"
        r"[^.\n]{0,100}\bnot\s+explicitly\s+(?:listed|required|requested|specified)\b",
        r"(?:即使|虽然).{0,80}(?:用户)?(?:未|没有)(?:明确)?(?:要求|列出|指定).{0,80}"
        r"(?:也必须|仍需|仍然要|必须添加|必须包含)",
    )
)


_EXPLICIT_PLACEHOLDER_PERMISSION_RE = re.compile(
    r"(?i)\buser\s+(?:explicitly\s+)?(?:allows?|permits?|requests?)\b.{0,80}"
    r"\b(?:placeholders?|assumptions?|estimated values?)\b|"
    r"用户.{0,20}(?:明确)?(?:允许|要求).{0,30}(?:占位符|假设值|估算值)"
)


_UNREQUESTED_CONTEXT_RESTATEMENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:restate|copy|repeat|place|add|include)\b[^.\n]{0,120}"
        r"\b(?:assumptions?|exclusions?|pre[- ]handled topics?)\b[^.\n]{0,100}"
        r"\b(?:introduction|scope section|document|deliverable|report|memo|final output)\b",
        r"\b(?:assumptions?|exclusions?|pre[- ]handled topics?)\b[^.\n]{0,100}"
        r"\b(?:restate|copy|repeat|place|add|include)\b[^.\n]{0,100}"
        r"\b(?:introduction|scope section|document|deliverable|report|memo|final output)\b",
    )
)


_EXPLICIT_CONTEXT_RESTATEMENT_REQUEST_RE = re.compile(
    r"(?is)\buser\b.{0,40}\b(?:asks?|requires?|instructs?|explicitly\s+requests?)\b"
    r".{0,100}\b(?:restate|copy|repeat|place|add|include|show)\b.{0,100}"
    r"\b(?:assumptions?|exclusions?|pre[- ]handled topics?)\b|"
    r"用户.{0,30}(?:明确)?(?:要求|请求|指示).{0,80}(?:复述|列出|包含|展示).{0,80}"
    r"(?:假设|排除项|已处理事项)"
)


_FIXED_CAPACITY_ARTIFACT_RE = re.compile(
    r"(?i)\b(?:single[- ]slide|one[- ]slide|one[- ]page|single[- ]page|fixed[- ]capacity|"
    r"fixed[- ]size|space[- ]constrained)\b|(?:单页|单张|一页|固定容量|空间受限)"
)


_DENSITY_AS_OVERFLOW_RE = re.compile(
    r"(?i)\b(?:dense formatting|nested bullet points?|smaller fonts?|shrink(?:ing)?\s+(?:the\s+)?"
    r"(?:font|text|content)|pack(?:ing)?\s+(?:the\s+)?content|condense everything)\b|"
    r"(?:密集排版|嵌套项目符号|缩小字体|压缩全部内容|塞入)"
)


_EXPLICIT_OVERFLOW_RESOLUTION_RE = re.compile(
    r"(?i)\b(?:readab(?:le|ility)|overflow|priorit(?:y|ize|ise|ization)|trade[- ]?off|"
    r"clarif(?:y|ication)|ask\s+(?:the\s+)?user|user permission|scope decision)\b|"
    r"(?:可读性|溢出|优先级|取舍|澄清|询问用户|用户确认|范围决策)"
)


_NON_PORTABLE_CASE_LITERAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\be\.g\.,?\s*[\"']?\d+(?:\.\d+)?(?:\s*[/,-]\s*\d+(?:\.\d+)?)*"
        r"(?:\s*(?:hours?|days?|weeks?|months?|years?|%))?\b",
        r"(?i)\bEvidence binding\s*:[^\n]*\b\d+\s*[-–]\s*\d+\s*"
        r"(?:days?|weeks?|months?|years?|%)\b",
        r"[\"'][^\"'\n]{1,80}[\"']\s+(?:sheet|tab|worksheet)\b",
        r"\b(?:sheet|tab|worksheet)\s+(?:named\s+)?[\"'][^\"'\n]{1,80}[\"']",
        r"\b[a-zA-Z_][a-zA-Z0-9_.-]*\.(?:py|sh|bash|command)\b",
        r"\be\.g\.,?\s*[\"'][^\"'\n]{0,80}\b[a-z]*[A-Z][a-zA-Z]*[A-Z][a-zA-Z]*"
        r"[^\"'\n]{0,80}[\"']",
        r"\b(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\b.{0,40}\bWeeks?\s*\d+(?:\s*[-–]\s*\d+)?\b",
    )
)


_MANDATORY_AUXILIARY_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:write|create|save|generate)\s+(?:a\s+)?(?:visible\s+|mandatory\s+|"
        r"itemized\s+|intermediate\s+|temporary\s+|helper\s+|scratch\s+)+"
        r"(?:checklist\s+)?(?:file|document|artifact)\b",
        r"\b(?:must|always|do\s+not\s+skip|mandatory)\b.{0,100}"
        r"\b(?:intermediate|temporary|helper|scratch)\b.{0,40}\b(?:file|document|artifact)\b",
        r"(?<![a-z0-9])(?:[a-z0-9_.-]*(?:checklist|scratch|temporary|intermediate|helper|"
        r"requirements)[a-z0-9_.-]*)\.(?:md|txt|json|csv)(?![a-z0-9])",
    )
)


_RUNTIME_CONTROL_PLANE_TERM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(pattern, re.IGNORECASE))
    for term, pattern in (
        ("rollout_evaluation", r"(?<![a-z0-9])rollout[_ -]?evaluation(?![a-z0-9])"),
        ("evaluation_result", r"(?<![a-z0-9])evaluation[_ -]?results?(?![a-z0-9])"),
        (
            "evaluation_as_evidence_binding",
            r"(?im)^\s*-\s*Evidence binding\s*:[^\n]*\bevaluation\s+"
            r"(?:showing|indicating|reporting|feedback|result|results|score|scores)\b",
        ),
        (
            "evaluator_feedback",
            r"(?<![a-z0-9])evaluator[_ -]?(?:feedback|diagnosis|score|results?)(?![a-z0-9])",
        ),
        (
            "platform_internal_metadata",
            r"(?<![a-z0-9])(?:[a-z0-9_.-]+-specific|platform-specific|workflow-specific)\s+"
            r"internal\s+metadata(?![a-z0-9])",
        ),
        (
            "internal_task_checklist_file",
            r"(?<![a-z0-9])(?:[a-z0-9_.-]+\s+)?task_checklist\.json(?![a-z0-9])",
        ),
        (
            "platform_internal_literal",
            r"(?is)(?:\b(?:internal|tool-specific|workspace|temporary|process)\b|"
            r"(?:内部|工作区|临时|流程)).{0,180}"
            r"(?:[\"']?\.[a-z][a-z0-9_.-]*[\"']?|manifest\.json|source_manifest)",
        ),
        (
            "platform_tool_identifier",
            r"(?im)^\s*-\s*Evidence binding\s*:[^\n]*\be\.g\.,?\s*"
            r"[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}\b",
        ),
        ("communicate_checks", r"\bcommunicate_checks?\b"),
        ("action_checks", r"\baction_checks?\b"),
        ("db_check", r"\bdb_checks?\b"),
        ("reward_signal", r"(?<![a-z0-9])reward[_ -]?(?:signal|score)(?![a-z0-9])"),
        ("rubric_result", r"(?<![a-z0-9])rubric[_ -]?(?:result|score|passed|failed)(?![a-z0-9])"),
        ("outcome_checks", r"(?<![a-z0-9])outcome[_ -]?checks?(?![a-z0-9])"),
        ("review_result", r"(?<![a-z0-9])review[_ -]?results?(?![a-z0-9])"),
        ("评估结果", r"评估(?:结果|得分|反馈)"),
        (
            "evaluation_as_evidence_binding_chinese",
            r"(?m)^\s*-\s*Evidence binding\s*:[^\n]*评估(?:显示|表明|指出|认为)",
        ),
        ("奖励信号", r"奖励(?:信号|得分)"),
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
    after_file = getattr(gradient, "after_file", None)
    fields = dict(getattr(after_file, "extra_fields", {}) or {})
    metadata = dict(getattr(gradient, "metadata", {}) or {})
    return str(
        getattr(after_file, "memory_type", "")
        or fields.get("memory_type")
        or metadata.get("memory_type")
        or "unknown"
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
