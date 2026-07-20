# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest
from test_fakes import render_experience_fields

import openviking.session.train.gates as gates_module
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
    ExperienceRootCausePreventionGate,
    GateReport,
    GateTarget,
    build_gate_retry_instruction,
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


def test_only_root_cause_experience_gate_remains():
    gate_names = {
        name
        for name in vars(gates_module)
        if name.startswith("Experience") and name.endswith("Gate")
    }

    assert gate_names == {"ExperienceRootCausePreventionGate"}
    assert not hasattr(gates_module, "default_policy_gate_runner")
    assert not hasattr(gates_module, "default_experience_gate_contract")


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
async def test_experience_root_cause_prevention_gate_semantically_reviews_policy_branch_completion():
    target, gate = _gradient_target(
        '{"pass": true, "root_cause_quality": "sufficient", '
        '"reason": "the proposal covers the observed policy branches", '
        '"expected_behavior_change": "check every reservation against every branch", '
        '"repair_prompt": "", "risks": []}'
    )
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

    assert decision is None
    assert len(gate.vlm.calls) == 1


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_does_not_auto_reject_policy_override_text():
    target, gate = _gradient_target(
        '{"pass": true, "root_cause_quality": "sufficient", '
        '"reason": "the source trajectory supports the proposed exception", '
        '"expected_behavior_change": "apply the evaluator-required exception", '
        '"repair_prompt": "", "risks": []}'
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

    assert decision is None
    assert len(gate.vlm.calls) == 1


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_rejects_refund_scope_expansion():
    target, gate = _gradient_target(
        '{"pass": false, "root_cause_quality": "unsafe_scope", '
        '"reason": "the proposal expands cancellation beyond supported eligibility", '
        '"expected_behavior_change": "cancel every unflown reservation", '
        '"repair_prompt": "retain the observed eligibility conditions", "risks": []}'
    )
    assert target.trajectory is not None
    target.trajectory.content = (
        "## Runtime Evidence\n"
        "- Observed policy text: cancellation eligibility depends on cabin, booking time, "
        "airline cancellation, or covered insurance.\n"
        "## Outcome Evidence\n"
        "- Missing or mismatched actions: cancel_reservation for one reservation.\n"
    )
    fields = {
        "situation": (
            "- Applies when: 用户请求取消所有即将到来的航班，且明确表示即使无法退款也希望取消\n"
            "- Does not apply when: 用户只请求取消特定条件的预订\n"
            "- Source binding: 用户请求、预订详情和取消政策文本\n"
            "- Scope ambiguity: none"
        ),
        "reminder": "- 确保所有未飞行的预订都被取消，即使不在标准可退款条件内",
        "procedure": "- 如果用户即使无法退款也希望取消：对所有未飞行预订执行cancel_reservation",
        "anti_pattern": "- 不要只取消部分未飞行预订",
    }
    assert target.gradient is not None
    target.gradient.after_file.content = render_experience_fields(fields)
    target.gradient.after_file.extra_fields.update(fields)

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.retriable is True
    assert "beyond supported eligibility" in decision.reason
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
