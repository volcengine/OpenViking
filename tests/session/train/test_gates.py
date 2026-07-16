# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.train.domain import (
    CriterionResult,
    ExperienceSet,
    PolicyPlanItem,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.gates import (
    ExperienceCausalSignalGate,
    ExperienceRootCausePreventionGate,
    ExperienceRuntimeWordingGate,
    ExperienceSkillReadabilityGate,
    ExperienceToolAlignmentGate,
    ExperienceTriggerRuntimeGate,
    GateTarget,
    default_experience_gate_contract,
    default_policy_gate_runner,
)
from openviking.session.train.gradients import PatchSemanticGradient


class FakeVLM:
    def __init__(self, response: str | Exception):
        self.response = response
        self.calls = []

    async def get_completion_async(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _trajectory() -> Trajectory:
    return Trajectory(
        name="missing_total",
        uri="viking://user/u/memories/trajectories/missing_total.md",
        outcome="failure",
        retrieval_anchor="Stage: final_response",
        content=(
            "# Missing total\n"
            "- Outcome: failure\n"
            "- Runtime Facts:\n"
            "  - communicate_checks failed: missing required total\n"
            "- First Wrong Tool Call:\n"
            "  - Tool: communicate_with_user\n"
            "- First Material Divergence:\n"
            "  - Kind: missing_communication\n"
        ),
    )


def _trajectory_with_repair_signal(
    *,
    outcome: str = "failure",
    action: str = "skip",
    first_wrong_tool: str = "communicate_with_user",
    trigger_boundary: str = "none",
) -> Trajectory:
    return Trajectory(
        name="missing_total",
        uri="viking://user/u/memories/trajectories/missing_total.md",
        outcome=outcome,
        retrieval_anchor="Stage: final_response",
        content=(
            "# Missing total\n"
            f"- Outcome: {outcome}\n"
            "- Runtime Facts:\n"
            "  - communicate_checks failed: missing required total\n"
            "- First Wrong Tool Call:\n"
            f"  - Tool: {first_wrong_tool}\n"
            "  - Error type: missing_communication\n"
            "- Experience Repair Signal:\n"
            f"  - Action: {action}\n"
            f"  - Trigger boundary: {trigger_boundary}\n"
        ),
    )


def _analysis() -> RolloutAnalysis:
    return RolloutAnalysis(
        evaluation=RubricEvaluation(
            passed=False,
            score=0.0,
            criterion_results=[
                CriterionResult(
                    criterion_name="tau2_reward",
                    passed=False,
                    score=0.0,
                    feedback=["required total was not communicated"],
                    evidence=[],
                )
            ],
            metadata={"reward": 0.0},
        ),
        trajectories=[_trajectory()],
    )


def _plan_item() -> PolicyPlanItem:
    return PolicyPlanItem(
        kind="upsert",
        memory_type="experiences",
        target_name="missing_total_communication",
        target_uri="viking://user/u/memories/experiences/missing_total_communication.md",
        before_content=None,
        after_content=(
            "## Situation\n"
            "- Applies when: final user-visible communication must answer a requested total.\n"
            "- Does not apply when: no total was requested.\n"
            "- Source binding: user request scope and retrieved record prices.\n\n"
            "## Reminder\n"
            "- Include the requested source-bound total in the final message.\n\n"
            "## Procedure\n"
            "- Before calling `communicate_with_user`: check whether the requested total is present.\n"
            "- If it is missing: add the calculated total from retrieved records.\n"
            "- Else: proceed with the candidate message.\n\n"
            "## Anti-pattern\n"
            "- Do not summarize completion while omitting the requested total.\n"
            "- Preserve unrelated correct database actions.\n"
        ),
        links=[
            StoredLink(
                from_uri="viking://user/u/memories/experiences/missing_total_communication.md",
                to_uri="viking://user/u/memories/trajectories/missing_total.md",
                link_type="derived_from",
                weight=1.0,
            )
        ],
        metadata={
            "merge_memory_fields": {
                "trigger_code": (
                    "def should_trigger(ctx):\n"
                    "    return ctx.get('candidate_tool') == 'communicate_with_user'\n"
                )
            }
        },
    )


def _gradient_target(
    vlm_response: str | Exception,
) -> tuple[GateTarget, ExperienceRootCausePreventionGate]:
    analysis = _analysis()
    item = _plan_item()
    after_file = MemoryFile(
        uri=item.target_uri,
        content=item.after_content,
        memory_type="experiences",
        extra_fields={
            "memory_type": "experiences",
            "experience_name": item.target_name,
            "constraint": item.after_content,
            "trigger_code": item.metadata["merge_memory_fields"]["trigger_code"],
        },
    )
    gradient = PatchSemanticGradient(
        before_file=None,
        after_file=after_file,
        base_version=None,
        rationale="test",
        links=item.links,
        confidence=0.8,
    )
    gate = ExperienceRootCausePreventionGate(vlm=FakeVLM(vlm_response))
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="gradient",
        gradient=gradient,
        analysis=analysis,
        trajectory=analysis.trajectories[0],
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )
    return target, gate


def test_default_policy_gate_runner_uses_deterministic_experience_gates_only():
    names = [gate.name for gate in default_policy_gate_runner().gates]

    assert names == ["experience_causal_signal", "experience_skill_readability"]
    assert "experience_counterfactual_reflection" not in names
    assert "experience_root_cause_prevention" not in names
    assert "experience_runtime_wording" not in names
    assert "experience_tool_alignment" not in names
    assert "experience_content_format" not in names
    assert "experience_trigger_shape" not in names
    assert "experience_update_narrowing" not in names

    contract = default_experience_gate_contract()
    assert "Content format" not in contract
    assert "Counterfactual reflection" not in contract
    assert "Runtime wording hygiene" not in contract
    assert "Trigger runtime compatibility" not in contract
    assert "Skill-loader readability" in contract
    assert "`## Situation`" in contract
    assert "eligible for experience learning by default" in contract
    assert "Recommended operation=skip" in contract
    assert "Existing target experience=none only means" in contract
    assert "not a temporal" in contract
    assert "request-time scope" in contract
    assert "canonical runtime value field" in contract
    assert "Action is create/update" not in contract
    assert "Candidate-shape trigger" not in contract
    assert "Update safety" not in contract


@pytest.mark.asyncio
async def test_runtime_wording_gate_rejects_evaluator_terms_in_experience_content():
    trajectory = _trajectory_with_repair_signal(
        action="create",
        first_wrong_tool="communicate_with_user",
        trigger_boundary="communicate_with_user",
    )
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: final communication must include a total.\n"
        "- Does not apply when: no total was requested.\n"
        "- Source binding: communicate_checks required total from evaluator.\n\n"
        "## Reminder\n"
        "- Include the total required by the rubric.\n\n"
        "## Procedure\n"
        "- Before calling `communicate_with_user`: check the final message.\n"
        "- If the total is missing: add it.\n"
        "- Else: proceed.\n\n"
        "## Anti-pattern\n"
        "- Do not omit evaluator-required content.\n"
        "- Preserve unrelated actions.\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceRuntimeWordingGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.gate_name == "experience_runtime_wording"
    assert set(decision.evidence["terms"]) >= {"communicate_checks", "evaluator", "rubric"}


@pytest.mark.asyncio
async def test_skill_readability_gate_requires_situation_source_binding():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: final communication is needed.\n\n"
        "## Reminder\n"
        "- Include the requested fact.\n\n"
        "## Procedure\n"
        "- Before replying: check the message.\n\n"
        "## Anti-pattern\n"
        "- Do not omit the fact.\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceSkillReadabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.gate_name == "experience_skill_readability"
    assert decision.evidence["has_source_binding"] is False


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_temporal_does_not_apply():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: a requested total may be communicated after later writes.\n"
        "- Does not apply when: still reading records before final_response.\n"
        "- Source binding: user request scope and retrieved records.\n\n"
        "## Reminder\n"
        "- Preserve the request-time total.\n\n"
        "## Procedure\n"
        "- Before replying: compare request-time and current scopes.\n\n"
        "## Anti-pattern\n"
        "- Do not answer only the current remaining total.\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceSkillReadabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "before_final_response" in decision.evidence["temporal_non_applicability"]
    assert "still_reading_or_writing" in decision.evidence["temporal_non_applicability"]


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_relative_write_scope_none():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: user asks for other upcoming items and their total while also "
        "requesting cancel or upgrade writes.\n"
        "- Does not apply when: the user explicitly says excluding a named object.\n"
        "- Source binding: user request scope, retrieved record set, and later write scope.\n"
        "- Scope ambiguity: 无\n\n"
        "## Reminder\n"
        "- Preserve the request-time aggregate and label any post-action remaining aggregate.\n\n"
        "## Procedure\n"
        "- Before replying: compare request-time and post-action scopes.\n\n"
        "## Anti-pattern\n"
        "- Do not answer only the remaining subset.\n"
        "- Preserve the requested writes.\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceSkillReadabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["relative_scope_ambiguity"] is True


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_line_item_money_without_canonical_source():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: user asks for a total cost.\n"
        "- Does not apply when: user asks for a non-monetary count.\n"
        "- Source binding: retrieved records, source field, and calculation.\n"
        "- Scope ambiguity: none\n\n"
        "## Reminder\n"
        "- Calculate the total cost from source-bound facts.\n\n"
        "## Procedure\n"
        "- Before replying: compute each record as flight price × passenger count, then sum.\n"
        "- If any record is missing: read it.\n\n"
        "## Anti-pattern\n"
        "- Do not omit part of the total.\n"
        "- Preserve unrelated actions.\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceSkillReadabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["line_item_money_source"] is True


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_price_field_money_source_without_canonical():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: 用户询问记录集合的总费用。\n"
        "- Does not apply when: 用户没有询问金额汇总。\n"
        "- Source binding: 请求范围内的记录和价格字段。\n"
        "- Scope ambiguity: 无\n\n"
        "## Reminder\n"
        "- 回答总费用时必须绑定来源字段。\n\n"
        "## Procedure\n"
        "- 对每条记录使用每个子项目的 price 字段相加计算总费用。\n"
        "- 在最终回复中说明这个总费用。\n\n"
        "## Anti-pattern\n"
        "- 不要遗漏任何记录。\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceSkillReadabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["line_item_money_source"] is True


@pytest.mark.asyncio
async def test_skill_readability_gate_allows_line_item_cross_check_with_canonical_source():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: user asks for a total cost from retrieved records.\n"
        "- Does not apply when: user asks for a non-monetary count.\n"
        "- Source binding: canonical payment_history.amount or explicit paid amount field; "
        "line-item prices are only a fallback/cross-check when no canonical amount exists.\n"
        "- Scope ambiguity: none\n\n"
        "## Reminder\n"
        "- Prefer the record-level paid amount over reconstructed item prices.\n\n"
        "## Procedure\n"
        "- Read the record-level payment amount first.\n"
        "- Use lower-level price fields only as a fallback or cross-check.\n\n"
        "## Anti-pattern\n"
        "- Do not make lower-level item prices the primary total source when a paid amount exists.\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceSkillReadabilityGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_causal_signal_gate_rejects_structured_selected_none():
    trajectory = Trajectory(
        name="missing_total",
        uri="viking://user/u/memories/trajectories/missing_total.md",
        outcome="failure",
        retrieval_anchor="Stage: final_response",
        content=(
            "# Missing total\n"
            "- Outcome: failure\n"
            "- First Wrong Tool Call:\n"
            "  - Tool: communicate_with_user\n"
            "  - Error type: missing_communication\n"
            "- Counterfactual Ideal Experience:\n"
            "  - Selected candidate: none\n"
            "- Experience Repair Signal:\n"
            "  - Recommended operation: skip\n"
            "  - Trigger boundary: none\n"
        ),
    )
    item = _plan_item()
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceCausalSignalGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["signals"][0]["selected_candidate"] == "none"


@pytest.mark.asyncio
async def test_causal_signal_gate_allows_structured_selected_c1():
    trajectory = Trajectory(
        name="missing_total",
        uri="viking://user/u/memories/trajectories/missing_total.md",
        outcome="failure",
        retrieval_anchor="Stage: final_response",
        content=(
            "# Missing total\n"
            "- Outcome: failure\n"
            "- First Wrong Tool Call:\n"
            "  - Tool: communicate_with_user\n"
            "  - Error type: missing_communication\n"
            "- Counterfactual Ideal Experience:\n"
            "  - Selected candidate: C1\n"
            "- Experience Repair Signal:\n"
            "  - Recommended operation: create\n"
            "  - Trigger boundary: communicate_with_user\n"
        ),
    )
    item = _plan_item()
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceCausalSignalGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_trigger_runtime_gate_rejects_vikingbot_incompatible_trigger():
    trajectory = _trajectory_with_repair_signal(
        action="create",
        first_wrong_tool="communicate_with_user",
        trigger_boundary="communicate_with_user",
    )
    item = _plan_item()
    item.metadata = {
        "merge_memory_fields": {
            "trigger_code": (
                "def should_trigger(ctx):\n"
                "    import os\n"
                "    return ctx.get('candidate_tool') == 'communicate_with_user'\n"
            )
        }
    }
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceTriggerRuntimeGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.retriable is True
    assert decision.gate_name == "experience_trigger_runtime"
    assert "VikingBot constraint runtime" in decision.reason


@pytest.mark.asyncio
async def test_trigger_runtime_gate_allows_vikingbot_supported_negative_slice():
    item = _plan_item()
    item.metadata = {
        "merge_memory_fields": {
            "trigger_code": (
                "def should_trigger(ctx):\n"
                "    messages = ctx.get('messages', [])\n"
                "    return bool(messages[-10:]) and ctx.get('candidate_tool') == 'communicate_with_user'\n"
            )
        }
    }
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceTriggerRuntimeGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_trigger_runtime_gate_parses_rendered_experience_trigger_body():
    item = _plan_item()
    item.metadata = {}
    item.after_content = (
        "## Failure Pattern\n"
        "- Missing required information.\n\n"
        "# Experience Trigger\n"
        "- experience_name: final_required_info\n"
        "- trigger_code:\n"
        "```python\n"
        "def should_trigger(ctx):\n"
        "    import os\n"
        "    return True\n"
        "```\n"
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(
            action="create",
            first_wrong_tool="communicate_with_user",
            trigger_boundary="communicate_with_user",
        ),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceTriggerRuntimeGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"


@pytest.mark.asyncio
async def test_causal_signal_gate_allows_failed_skip_signal_for_new_experience():
    trajectory = _trajectory_with_repair_signal(action="skip", trigger_boundary="none")
    item = _plan_item()
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceCausalSignalGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_tool_alignment_uses_first_wrong_tool_even_when_trigger_boundary_none():
    trajectory = _trajectory_with_repair_signal(
        action="skip",
        first_wrong_tool="communicate_with_user",
        trigger_boundary="none",
    )
    item = _plan_item()
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceToolAlignmentGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_causal_signal_gate_allows_split_signal_with_no_existing_target_for_new_experience():
    trajectory = Trajectory(
        name="missing_total",
        uri="viking://user/u/memories/trajectories/missing_total.md",
        outcome="failure",
        retrieval_anchor="Stage: final_response",
        content=(
            "# Missing total\n"
            "- Outcome: failure\n"
            "- First Wrong Tool Call:\n"
            "  - Tool: communicate_with_user\n"
            "  - Error type: missing_communication\n"
            "- Experience Repair Signal:\n"
            "  - Recommended operation: create\n"
            "  - Existing experience action: none\n"
            "  - Existing target experience: none\n"
            "  - New experience action: create\n"
            "  - New experience candidate: missing_required_total\n"
            "  - Trigger boundary: communicate_with_user\n"
        ),
    )
    item = _plan_item()
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    causal_decision = await ExperienceCausalSignalGate().evaluate(target)
    alignment_decision = await ExperienceToolAlignmentGate().evaluate(target)

    assert causal_decision is None
    assert alignment_decision is None


@pytest.mark.asyncio
async def test_causal_signal_gate_still_rejects_success_trajectory():
    trajectory = _trajectory_with_repair_signal(outcome="success", action="skip")
    item = _plan_item()
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=trajectory,
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceCausalSignalGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "non-success" in decision.reason


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_allows_preventive_experience():
    target, gate = _gradient_target(
        '{"pass": true, "root_cause_quality": "sufficient", '
        '"reason": "final communication trigger changes answer to include required total", '
        '"expected_behavior_change": "include required total", '
        '"repair_prompt": "", "risks": []}'
    )

    decision = await gate.evaluate(target)

    assert decision is None
    assert len(gate.vlm.calls) == 1
    prompt = gate.vlm.calls[0]["prompt"]
    assert "preventable wrong decision" in prompt
    assert "coupled causal chain" in prompt
    assert "agent-proposed expansion" in prompt
    assert "later modified, canceled, upgraded" in prompt
    assert "temporal non-applicability" in prompt
    assert "canonical total/payment field" in prompt


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_rejects_non_preventive_experience():
    target, gate = _gradient_target(
        '{"pass": false, "root_cause_quality": "not_preventive", '
        '"reason": "only summarizes a broad workflow", '
        '"expected_behavior_change": "", '
        '"repair_prompt": "Rewrite it around the missing final total communication.", '
        '"risks": []}'
    )

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.gate_name == "experience_root_cause_prevention"
    assert decision.retriable is True
    assert "workflow" in decision.reason
    assert "missing final total" in decision.repair_prompt


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_fails_closed_on_llm_error():
    target, gate = _gradient_target(RuntimeError("model unavailable"))

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.retriable is False
    assert "failed closed" in decision.reason


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_rejects_invalid_llm_output():
    target, gate = _gradient_target("not JSON")

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.retriable is False
    assert "invalid output" in decision.reason
