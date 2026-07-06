# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.session.memory.dataclass import StoredLink
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
    ExperienceCounterfactualReflectionGate,
    ExperienceToolAlignmentGate,
    GateTarget,
    default_experience_gate_contract,
    default_policy_gate_runner,
)


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
            "## Failure Pattern\n"
            "- Missing required total in final user-visible message.\n\n"
            "## Repair Procedure\n"
            "- Before calling `communicate_with_user`, include the required total.\n\n"
            "## Guardrails\n"
            "- Only applies to final summary communication.\n"
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


def _target(
    vlm_response: str | Exception,
) -> tuple[GateTarget, ExperienceCounterfactualReflectionGate]:
    analysis = _analysis()
    item = _plan_item()
    gate = ExperienceCounterfactualReflectionGate(vlm=FakeVLM(vlm_response))
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        analysis=analysis,
        trajectory=analysis.trajectories[0],
        policy_set=ExperienceSet(root_uri="viking://user/u/memories/experiences", policies=[]),
    )
    return target, gate


def test_default_policy_gate_runner_uses_reflection_not_shape_or_narrowing_gate():
    names = [gate.name for gate in default_policy_gate_runner().gates]

    assert "experience_counterfactual_reflection" in names
    assert "experience_trigger_shape" not in names
    assert "experience_update_narrowing" not in names

    contract = default_experience_gate_contract()
    assert "Counterfactual reflection" in contract
    assert "eligible for experience learning by default" in contract
    assert "Action=skip or Trigger boundary=none must not suppress" in contract
    assert "Action is create/update" not in contract
    assert "Candidate-shape trigger" not in contract
    assert "Update safety" not in contract


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
async def test_counterfactual_reflection_gate_allows_likely_improvement():
    target, gate = _target(
        '{"would_improve_original_rollout": true, "confidence": 0.82, '
        '"failure_mode_addressed": "missing_communication", '
        '"expected_behavior_change": "include required total", '
        '"reject_reason": null, "risks": []}'
    )

    decision = await gate.evaluate(target)

    assert decision is None
    assert len(gate.vlm.calls) == 1
    assert "would the original rollout likely execute better" in gate.vlm.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_counterfactual_reflection_gate_rejects_unlikely_improvement():
    target, gate = _target(
        '{"would_improve_original_rollout": false, "confidence": 0.9, '
        '"failure_mode_addressed": "broad workflow", '
        '"expected_behavior_change": "", '
        '"reject_reason": "only summarizes a workflow", "risks": []}'
    )

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.gate_name == "experience_counterfactual_reflection"
    assert "workflow" in decision.reason
    assert decision.evidence["would_improve_original_rollout"] is False


@pytest.mark.asyncio
async def test_counterfactual_reflection_gate_fails_open_on_llm_error():
    target, gate = _target(RuntimeError("model unavailable"))

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "warn"
    assert "failed open" in decision.reason
