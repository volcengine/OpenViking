# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest
from test_fakes import render_experience_fields

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
    ExperienceFieldSemanticsGate,
    ExperienceRootCausePreventionGate,
    ExperienceRuntimeWordingGate,
    ExperienceToolAlignmentGate,
    ExperienceTriggerRuntimeGate,
    GateReport,
    GateTarget,
    build_gate_retry_instruction,
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
    item = PolicyPlanItem(
        kind="upsert",
        memory_type="experiences",
        target_name="missing_total_communication",
        target_uri="viking://user/u/memories/experiences/missing_total_communication.md",
        before_content=None,
        after_content="",
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
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: final user-visible communication must answer a requested total.\n"
            "- Does not apply when: no total was requested.\n"
            "- Source binding: user request scope and retrieved record prices."
        ),
        reminder="- Include the requested source-bound total in the final message.",
        procedure=(
            "- Before calling `communicate_with_user`: check whether the requested total is present.\n"
            "- If it is missing: add the calculated total from retrieved records.\n"
            "- Else: proceed with the candidate message."
        ),
        anti_pattern=(
            "- Do not summarize completion while omitting the requested total.\n"
            "- Preserve unrelated correct database actions."
        ),
    )
    return item


def _set_experience_fields(
    item: PolicyPlanItem,
    *,
    situation: str,
    reminder: str,
    procedure: str,
    anti_pattern: str,
) -> None:
    fields = {
        "situation": situation,
        "reminder": reminder,
        "procedure": procedure,
        "anti_pattern": anti_pattern,
    }
    item.after_content = render_experience_fields(fields)
    item.metadata.setdefault("merge_memory_fields", {}).update(fields)


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
            **item.metadata["merge_memory_fields"],
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


def _add_structured_behavior_evidence(target: GateTarget) -> None:
    assert target.analysis is not None
    target.analysis.evaluation.metadata["evaluation_result"] = {
        "action_checks": [
            {
                "action": {
                    "name": "cancel_reservation",
                    "arguments": {"reservation_id": "MSJ4OA"},
                },
                "action_match": False,
                "tool_type": "write",
            },
            {
                "action": {
                    "name": "get_reservation_details",
                    "arguments": {"reservation_id": "MSJ4OA"},
                },
                "action_match": True,
                "tool_type": "read",
            },
        ],
        "communicate_checks": [
            {
                "info": "confirm the cancellation result",
                "met": False,
            }
        ],
        "db_check": {"db_match": False},
    }


def test_default_policy_gate_runner_uses_deterministic_experience_gates_only():
    names = [gate.name for gate in default_policy_gate_runner().gates]

    assert names == ["experience_causal_signal", "experience_field_semantics"]
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
    assert "Structured skill-loader fields" in contract
    assert "`situation`" in contract
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
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: final communication must include a total.\n"
            "- Does not apply when: no total was requested.\n"
            "- Source binding: communicate_checks required total from evaluator."
        ),
        reminder="- Include the total required by the rubric.",
        procedure=(
            "- Before calling `communicate_with_user`: check the final message.\n"
            "- If the total is missing: add it.\n"
            "- Else: proceed."
        ),
        anti_pattern=("- Do not omit evaluator-required content.\n- Preserve unrelated actions."),
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
    _set_experience_fields(
        item,
        situation="- Applies when: final communication is needed.",
        reminder="- Include the requested fact.",
        procedure="- Before replying: check the message.",
        anti_pattern="- Do not omit the fact.",
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

    decision = await ExperienceFieldSemanticsGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.gate_name == "experience_field_semantics"
    assert decision.evidence["has_source_binding"] is False


@pytest.mark.asyncio
async def test_field_semantics_gate_requires_custom_template_content_fields():
    from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
    from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
    from openviking.session.memory.merge_op.base import FieldType

    item = _plan_item()
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory(),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )
    content_fields = ["situation", "reminder", "procedure", "anti_pattern", "evidence"]
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[MemoryField(name=name, field_type=FieldType.STRING) for name in content_fields],
        content_template="\n".join(f"## {name}\n{{{{ {name} }}}}" for name in content_fields),
    )
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(schema)

    decision = await ExperienceFieldSemanticsGate(registry=registry).evaluate(target)

    assert decision is not None
    assert decision.evidence["missing_fields"] == ["evidence"]
    assert "`evidence`" in decision.repair_prompt


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_temporal_does_not_apply():
    item = _plan_item()
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: a requested total may be communicated after later writes.\n"
            "- Does not apply when: still reading records before final_response.\n"
            "- Source binding: user request scope and retrieved records."
        ),
        reminder="- Preserve the request-time total.",
        procedure="- Before replying: compare request-time and current scopes.",
        anti_pattern="- Do not answer only the current remaining total.",
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

    decision = await ExperienceFieldSemanticsGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "before_final_response" in decision.evidence["temporal_non_applicability"]
    assert "still_reading_or_writing" in decision.evidence["temporal_non_applicability"]


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_relative_write_scope_none():
    item = _plan_item()
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: user asks for other upcoming items and their total while also "
            "requesting cancel or upgrade writes.\n"
            "- Does not apply when: the user explicitly says excluding a named object.\n"
            "- Source binding: user request scope, retrieved record set, and later write scope.\n"
            "- Scope ambiguity: 无"
        ),
        reminder=(
            "- Preserve the request-time aggregate and label any post-action remaining aggregate."
        ),
        procedure="- Before replying: compare request-time and post-action scopes.",
        anti_pattern=(
            "- Do not answer only the remaining subset.\n- Preserve the requested writes."
        ),
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

    decision = await ExperienceFieldSemanticsGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["relative_scope_ambiguity"] is True


@pytest.mark.asyncio
async def test_skill_readability_gate_ignores_relative_wording_only_in_anti_pattern():
    item = _plan_item()
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: user asks to cancel all upcoming reservations.\n"
            "- Does not apply when: user names specific reservation IDs.\n"
            "- Source binding: cancellation policy and retrieved reservations.\n"
            "- Scope ambiguity: none"
        ),
        reminder="- Check every policy eligibility branch for every upcoming reservation.",
        procedure="- If a reservation matches any branch: cancel that reservation.",
        anti_pattern="- Do not ignore other eligible reservations.",
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=None,
        trajectory=_trajectory_with_repair_signal(action="create"),
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )

    decision = await ExperienceFieldSemanticsGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_line_item_money_without_canonical_source():
    item = _plan_item()
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: user asks for a total cost.\n"
            "- Does not apply when: user asks for a non-monetary count.\n"
            "- Source binding: retrieved records, source field, and calculation.\n"
            "- Scope ambiguity: none"
        ),
        reminder="- Calculate the total cost from source-bound facts.",
        procedure=(
            "- Before replying: compute each record as flight price × passenger count, then sum.\n"
            "- If any record is missing: read it."
        ),
        anti_pattern=("- Do not omit part of the total.\n- Preserve unrelated actions."),
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

    decision = await ExperienceFieldSemanticsGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["line_item_money_source"] is True


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_price_field_money_source_without_canonical():
    item = _plan_item()
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: 用户询问记录集合的总费用。\n"
            "- Does not apply when: 用户没有询问金额汇总。\n"
            "- Source binding: 请求范围内的记录和价格字段。\n"
            "- Scope ambiguity: 无"
        ),
        reminder="- 回答总费用时必须绑定来源字段。",
        procedure=(
            "- 对每条记录使用每个子项目的 price 字段相加计算总费用。\n"
            "- 在最终回复中说明这个总费用。"
        ),
        anti_pattern="- 不要遗漏任何记录。",
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

    decision = await ExperienceFieldSemanticsGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["line_item_money_source"] is True


@pytest.mark.asyncio
async def test_skill_readability_gate_allows_line_item_cross_check_with_canonical_source():
    item = _plan_item()
    _set_experience_fields(
        item,
        situation=(
            "- Applies when: user asks for a total cost from retrieved records.\n"
            "- Does not apply when: user asks for a non-monetary count.\n"
            "- Source binding: canonical payment_history.amount or explicit paid amount field; "
            "line-item prices are only a fallback/cross-check when no canonical amount exists.\n"
            "- Scope ambiguity: none"
        ),
        reminder="- Prefer the record-level paid amount over reconstructed item prices.",
        procedure=(
            "- Read the record-level payment amount first.\n"
            "- Use lower-level price fields only as a fallback or cross-check."
        ),
        anti_pattern=(
            "- Do not make lower-level item prices the primary total source when a paid amount exists."
        ),
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

    decision = await ExperienceFieldSemanticsGate().evaluate(target)

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
    assert "Authoritative outcome evidence" in prompt
    assert "smallest conflicting policy interpretation" in prompt
    assert "preserve non-conflicting constraints and object" in prompt
    assert "Tau2 evaluator authority" not in prompt
    assert "must not mention the evaluator" in prompt


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_accepts_observed_policy_branch_completion():
    target, gate = _gradient_target(RuntimeError("semantic gate must not run"))
    assert target.trajectory is not None
    target.trajectory.content = (
        "## Runtime Evidence\n"
        "- Observed policy text: cancellation eligibility includes bookings within 24 hours, "
        "airline cancellation, business class, or covered insurance.\n"
        "## Execution\n"
        "- Agent-stated decisions: treated only business-class reservations as eligible.\n"
        "## Outcome Evidence\n"
        "- Missing or mismatched actions: cancel_reservation for one eligible reservation.\n"
    )
    fields = {
        "situation": (
            "- Applies when: 用户要求取消所有即将到来的预订\n"
            "- Does not apply when: 用户仅指定特定预订\n"
            "- Source binding: 已读取的取消政策及每条预订详情\n"
            "- Scope ambiguity: none"
        ),
        "reminder": "- 检查取消政策中的所有资格条件，而不是只检查商务舱",
        "procedure": (
            "- 逐一检查每个未飞预订是否符合政策任一资格条件\n"
            "- 如果符合任一条件：将该预订纳入取消列表并执行取消"
        ),
        "anti_pattern": "- 不要只检查部分政策分支",
    }
    assert target.gradient is not None
    target.gradient.after_file.content = render_experience_fields(fields)
    target.gradient.after_file.extra_fields.update(fields)

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "allow"
    assert decision.evidence["deterministic_policy_branch_completion"] is True
    assert gate.vlm.calls == []


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_still_reviews_policy_override():
    target, gate = _gradient_target(
        '{"pass": false, "root_cause_quality": "unsafe", '
        '"reason": "user intent cannot override cancellation eligibility", '
        '"expected_behavior_change": "", "repair_prompt": "remove override", "risks": []}'
    )
    assert target.trajectory is not None
    target.trajectory.content = (
        "## Runtime Evidence\n"
        "- Observed policy text: cancellation requires an eligible policy branch.\n"
        "## Outcome Evidence\n"
        "- Missing or mismatched actions: cancel_reservation.\n"
    )
    fields = {
        "situation": (
            "- Applies when: 用户要求取消所有预订\n"
            "- Does not apply when: 用户未要求取消\n"
            "- Source binding: 用户请求文本和取消政策\n"
            "- Scope ambiguity: none"
        ),
        "reminder": "- 检查所有政策资格条件；即使预订不符合条件，也按用户意愿取消",
        "procedure": "- 逐一检查每个预订是否符合任一资格条件；无论是否符合取消政策，都取消",
        "anti_pattern": "- 不要让政策资格阻止用户要求的取消",
    }
    assert target.gradient is not None
    target.gradient.after_file.content = render_experience_fields(fields)
    target.gradient.after_file.extra_fields.update(fields)

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert len(gate.vlm.calls) == 1


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_anchors_structured_evaluator_behavior():
    target, gate = _gradient_target(
        '{"pass": true, "root_cause_quality": "sufficient", '
        '"reason": "the proposal implements the fixed behavior", '
        '"expected_behavior_change": "cancel the required reservation", '
        '"repair_prompt": "", "risks": []}'
    )
    _add_structured_behavior_evidence(target)
    stored_content = target.after_content

    decision = await gate.evaluate(target)

    assert decision is None
    prompt = gate.vlm.calls[0]["prompt"]
    assert "## Fixed authoritative behavior delta" in prompt
    assert '- Required missing action: cancel_reservation({"reservation_id":"MSJ4OA"})' in prompt
    assert (
        '- Preserve matched action: get_reservation_details({"reservation_id":"MSJ4OA"})' in prompt
    )
    assert "- Required missing communication: confirm the cancellation result" in prompt
    assert "Do not re-decide whether this behavior is correct" in prompt
    assert "Base-policy wording cannot reverse" in prompt
    assert target.after_content == stored_content


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_anchors_retry_instruction():
    target, gate = _gradient_target(
        '{"pass": false, "root_cause_quality": "unsupported", '
        '"reason": "base policy does not support cancellation", '
        '"expected_behavior_change": "", '
        '"repair_prompt": "Restore base policy and remove the cancellation requirement.", '
        '"risks": []}'
    )
    _add_structured_behavior_evidence(target)

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.retriable is True
    assert "Fixed authoritative behavior delta (must preserve)" in decision.repair_prompt
    assert 'cancel_reservation({"reservation_id":"MSJ4OA"})' in decision.repair_prompt
    assert "must not remove, reverse, weaken, or condition away" in decision.repair_prompt
    assert "Restore base policy" not in decision.repair_prompt
    assert decision.reason == "experience does not safely encode fixed authoritative behavior"
    assert decision.evidence["gate_model_reason"] == "base policy does not support cancellation"
    assert decision.evidence["anchored_repair"] is True
    assert (
        'cancel_reservation({"reservation_id":"MSJ4OA"})'
        in decision.evidence["authoritative_behavior_anchor"]
    )
    retry_instruction = build_gate_retry_instruction(
        GateReport(
            stage="post_gradient",
            rejected_count=1,
            decisions=[decision],
        )
    )
    assert "base policy does not support cancellation" not in retry_instruction
    assert "Restore base policy" not in retry_instruction
    assert 'cancel_reservation({"reservation_id":"MSJ4OA"})' in retry_instruction


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
