# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExperienceRootCausePreventionGate implementation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openviking.session.train.domain import RolloutAnalysis, Trajectory
from openviking_cli.utils import get_logger
from openviking_cli.utils.llm import parse_json_from_response

from ._shared import (
    _preview_text,
)
from .models import GateDecision, GateMode, GateTarget

logger = get_logger(__name__)


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
    target_experience_was_loaded = _target_experience_was_loaded(target)

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

Prior experience execution:
- target_experience_was_loaded: {str(target_experience_was_loaded).lower()}
- When this value is true and the evaluation failed, treat the existing experience
  as empirically insufficient for the claimed failure pattern. Pass an update only
  if it explains why the old rule did not prevent the failure and adds a concrete
  decision-rule or action delta. Reject paraphrases, stronger wording, or checklist additions
  that preserve the same decision logic.
- When the old experience already requires gathering or checking the relevant evidence,
  an explicit tool name, field list, exhaustive loop, or verification checklist is not a
  new delta. The update must change the decision made from that evidence at the observed
  failure boundary.

Evidence authority:
- Direct evaluation evidence establishes whether the source trajectory failed.
- A successful comparison trajectory may establish the required observable action
  or output delta even when the failed trajectory does not explain why it was correct.
- Do not reject a candidate merely because that successful action appears only in
  comparison trajectories. Comparison evidence must not be used to invent a hidden cause,
  unavailable tool, override path, or unsupported policy rule.
- For the observable behavior delta, evaluator-backed successful comparison behavior is authoritative
  over the failed trajectory's interpretation. If they conflict, encode the narrow runtime-observable exception
  supported by their differing inputs and actions; do not preserve the failed interpretation,
  demand source-only proof of the successful action, or generalize it into a broader hidden policy.

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
4. The experience covers one reusable root failure pattern. Multiple symptoms
   are combined only when they share the same first divergence, decisive
   evidence, decision boundary, and minimal repair. Do not fail an otherwise
   complete experience merely because the trajectory also contains unrelated
   failures that should become separate experiences.
5. When evaluation proves a reusable requirement failure but the internal cause
   is unknown, a narrow verification reminder at the earliest observable output
   or action boundary is acceptable; do not invent a hidden cause.
6. `Does not apply when` names a real task-pattern mismatch, not a temporal
   loader stage such as still reading/writing, before final_response, or before
   writes complete. Temporal wording would make the future agent skip reading an
   experience that must be available from task start.
7. Every mandatory behavior is supported by the user request, an authoritative
   source, or observable runtime evidence. A genre convention, evaluator-only
   preference, or hardcoded factual value cannot become a hidden requirement.
8. A mandatory output element is guarded by the same explicit runtime requirement
   that justifies it. Do not infer a required section, artifact, or field
   from a merely related input concept, audience, locale, or genre convention.
9. Runtime bindings name semantic roles and read their exact values from the future
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


def _target_experience_was_loaded(target: GateTarget) -> bool:
    if target.before_content is None or target.analysis is None:
        return False
    if target.gradient is not None:
        target_uri = str(target.gradient.target_uri or "")
    elif target.plan_item is not None:
        target_uri = str(target.plan_item.target_uri or "")
    else:
        target_uri = ""
    loaded_uris = {
        str(uri)
        for uri in list(target.analysis.metadata.get("loaded_experience_uris") or [])
        if uri
    }
    return bool(target_uri and target_uri in loaded_uris)


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
    for result in list(getattr(evaluation, "criterion_results", []) or [])[:10]:
        lines.append(
            f"criterion={result.criterion_name} passed={result.passed} score={result.score}"
        )
        if result.feedback:
            lines.append(
                "criterion_feedback="
                + "; ".join(_preview_text(str(item), limit=500) for item in result.feedback[:5])
            )
        if result.evidence:
            lines.append(
                "criterion_evidence="
                + "; ".join(_preview_text(str(item), limit=500) for item in result.evidence[:5])
            )
    metadata = dict(getattr(evaluation, "metadata", {}) or {})
    if metadata:
        # Keep only compact, source-agnostic metadata. Criterion results carry details.
        for key in ("reward", "source"):
            if key in metadata:
                lines.append(f"{key}={metadata[key]}")
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
