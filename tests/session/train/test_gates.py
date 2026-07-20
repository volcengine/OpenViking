# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import SimpleNamespace

import pytest

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
    ExperiencePlanQualityGate,
    ExperienceRootCausePreventionGate,
    GateDecision,
    GateReport,
    GateRunner,
    GateTarget,
    build_gate_retry_instruction,
    candidate_retry_draft,
    default_experience_gate_contract,
    default_policy_gate_runner,
)
from openviking.session.train.gradients import PatchSemanticGradient


class FakeVLM:
    def __init__(self, response: str | Exception | list[str | Exception]):
        self.response = response
        self.calls = []

    async def get_completion_async(self, **kwargs):
        self.calls.append(kwargs)
        response = self.response
        if isinstance(response, list):
            response = response[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response


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
            "- Evidence binding: user request scope and retrieved record prices.\n"
            "- Decision boundary: before composing the final user-visible answer.\n\n"
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


def test_default_policy_gate_runner_only_contains_final_semantic_review():
    names = [gate.name for gate in default_policy_gate_runner().gates]

    assert names == ["experience_plan_quality"]

    contract = default_experience_gate_contract()
    assert "source trajectory" in contract
    assert "prevent the failed behavior" in contract
    assert "merged final experience" in contract


def test_every_concrete_experience_gate_is_enabled():
    modules = [gates_module]
    package_path = getattr(gates_module, "__path__", None)
    if package_path is not None:
        modules.extend(
            importlib.import_module(module_info.name)
            for module_info in pkgutil.walk_packages(
                package_path,
                prefix=f"{gates_module.__name__}.",
            )
        )
    defined_gate_types = {
        value
        for module in modules
        for value in vars(module).values()
        if inspect.isclass(value)
        and value.__module__ == module.__name__
        and value.__name__.startswith("Experience")
        and value.__name__.endswith("Gate")
    }
    enabled_gate_types = {
        *(type(gate) for gate in default_policy_gate_runner().gates),
        ExperienceRootCausePreventionGate,
    }

    assert defined_gate_types == enabled_gate_types


@pytest.mark.asyncio
async def test_gate_runner_traces_each_applicable_gate_result_once(monkeypatch):
    class AllowGate:
        name = "allow_gate"
        mode = "enforce"

        def applies_to(self, target):
            return True

        async def evaluate(self, target):
            return None

    class RejectGate:
        name = "reject_gate"
        mode = "enforce"

        def applies_to(self, target):
            return True

        async def evaluate(self, target):
            return GateDecision(
                gate_name=self.name,
                action="reject",
                reason="candidate\nfailed semantic review",
            )

    target, _ = _gradient_target(
        '{"pass": true, "root_cause_quality": "sufficient", '
        '"reason": "unused", "expected_behavior_change": "unused", '
        '"repair_prompt": "", "risks": []}'
    )
    assert target.gradient is not None
    assert target.analysis is not None
    assert target.policy_set is not None
    trace_events = []
    monkeypatch.setattr(
        gates_module.tracer,
        "info",
        lambda message, **kwargs: trace_events.append(str(message)),
    )

    gated, report = await GateRunner([AllowGate(), RejectGate()]).filter_gradients(
        [target.gradient],
        analyses=[target.analysis],
        policy_set=target.policy_set,
    )

    assert gated == []
    assert report.rejected_count == 1
    assert len(trace_events) == 2
    assert all(event.startswith("policy_gate.result ") for event in trace_events)
    assert "gate=allow_gate" in trace_events[0]
    assert "action=allow" in trace_events[0]
    assert "reason=passed" in trace_events[0]
    assert "gate=reject_gate" in trace_events[1]
    assert "action=reject" in trace_events[1]
    assert "candidate failed semantic review" in trace_events[1]
    assert "\n" not in trace_events[1]


def test_gate_retry_instruction_includes_observed_diagnostics():
    report = GateReport(
        stage="post_gradient",
        rejected_count=1,
        decisions=[
            GateDecision(
                gate_name="experience_skill_readability",
                action="reject",
                reason="experience readability contract failed",
                evidence={
                    "target_name": "verify_required_output",
                    "missing_sections": ["Anti-pattern"],
                    "missing_situation_fields": ["Decision boundary"],
                },
                retriable=True,
                repair_prompt="Add only the missing section and field.",
            )
        ],
    )

    instruction = build_gate_retry_instruction(report)

    assert '"missing_sections": ["Anti-pattern"]' in instruction
    assert '"missing_situation_fields": ["Decision boundary"]' in instruction
    assert "Add only the missing section and field" in instruction


def test_gate_retry_instruction_is_candidate_local_and_includes_prior_failure():
    blocked = GateDecision(
        gate_name="non_retriable",
        action="reject",
        reason="unsupported candidate",
        evidence={"target_name": "blocked"},
    )
    first_failure = GateDecision(
        gate_name="semantic_gate",
        action="reject",
        reason="missing source binding",
        evidence={"target_name": "repair_me"},
        retriable=True,
        repair_prompt="bind the rule to an observable source",
    )
    current_failure = GateDecision(
        gate_name="semantic_gate",
        action="reject",
        reason="decision boundary is still too late",
        evidence={"target_name": "repair_me"},
        retriable=True,
        repair_prompt="move the check before the irreversible action",
    )
    report = GateReport(
        stage="post_gradient",
        rejected_count=2,
        decisions=[blocked, current_failure],
    )

    instruction = build_gate_retry_instruction(
        report,
        prior_reports=[
            GateReport(stage="post_gradient", rejected_count=1, decisions=[first_failure])
        ],
    )

    assert report.retriable_rejected_targets() == ["repair_me"]
    assert "Retry targets: repair_me" in instruction
    assert "missing source binding" in instruction
    assert "decision boundary is still too late" in instruction
    assert "blocked" not in instruction
    assert "must not be repeated or rewritten" in instruction


def test_candidate_retry_draft_keeps_only_rejected_candidates():
    draft = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_fields={"experience_name": "accepted", "constraint": "good"},
                uris=["viking://user/u/memories/experiences/accepted.md"],
            ),
            SimpleNamespace(
                memory_fields={"experience_name": "repair_me", "constraint": "bad"},
                uris=["viking://user/u/memories/experiences/repair_me.md"],
            ),
        ],
        delete_file_contents=[SimpleNamespace(uri="old")],
    )

    retry_draft = candidate_retry_draft(draft, target_names={"repair_me"})

    assert [item.memory_fields["experience_name"] for item in retry_draft.upsert_operations] == [
        "repair_me"
    ]
    assert retry_draft.delete_file_contents == []
    assert len(draft.upsert_operations) == 2


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
    assert "Candidate-local review rule" in prompt
    assert "required to make the entire source trajectory succeed" in prompt
    assert "Direct evaluation evidence" in prompt
    assert "unrelated failures" in prompt
    assert "evaluation-only language" not in prompt
    assert "evaluation_as_runtime_trigger" not in prompt
    assert "temporal non-applicability" in prompt
    assert "canonical total/payment field" not in prompt


@pytest.mark.asyncio
async def test_experience_gate_requires_new_delta_when_loaded_experience_still_failed():
    target, gate = _gradient_target(
        '{"pass": true, "root_cause_quality": "sufficient", '
        '"reason": "new decision rule fixes the observed action delta", '
        '"expected_behavior_change": "apply a different eligibility decision", '
        '"repair_prompt": "", "risks": []}'
    )
    assert target.gradient is not None
    target.gradient.before_file = MemoryFile(
        uri=target.gradient.target_uri,
        content="Existing reminder that was already injected.",
        memory_type="experiences",
        extra_fields={"experience_name": target.gradient.target_name},
    )
    assert target.analysis is not None
    target.analysis.metadata["loaded_experience_uris"] = [target.gradient.target_uri]

    decision = await gate.evaluate(target)

    assert decision is None
    prompt = gate.vlm.calls[0]["prompt"]
    assert "target_experience_was_loaded: true" in prompt
    assert "empirically insufficient for the claimed failure pattern" in prompt
    assert "why the old rule did not prevent the failure" in prompt
    assert "paraphrases, stronger wording, or checklist additions" in prompt
    assert "successful comparison trajectory may establish" in prompt
    assert "must not be used to invent a hidden cause" in prompt
    assert "an explicit tool name, field list, exhaustive loop, or verification checklist" in prompt
    assert "must change the decision made from that evidence" in prompt
    assert "evaluator-backed successful comparison behavior is authoritative" in prompt
    assert "encode the narrow runtime-observable exception" in prompt


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
async def test_experience_plan_quality_gate_reviews_merged_final_content():
    item = _plan_item()
    item.metadata["plan_quality_review_required"] = True
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Hardcode summary values as a substitute for the requested dashboard features.",
    )
    gate = ExperiencePlanQualityGate(
        vlm=FakeVLM(
            '{"pass": false, "root_cause_quality": "unsafe", '
            '"reason": "hardcoded factual outputs are not supported by runtime evidence", '
            '"expected_behavior_change": "", "repair_prompt": "remove hardcoded values", '
            '"risks": ["stale output"]}'
        )
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await gate.evaluate(target)

    assert gate.applies_to(target)
    assert decision is not None
    assert decision.action == "reject"
    assert decision.gate_name == "experience_plan_quality"
    assert "genre convention" in gate.vlm.calls[0]["prompt"]
    assert "hardcodes factual outputs" in gate.vlm.calls[0]["prompt"]


def test_experience_plan_quality_gate_skips_unchanged_single_candidate():
    item = _plan_item()
    item.metadata["plan_quality_review_required"] = False
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    assert ExperiencePlanQualityGate(vlm=FakeVLM("{}")).applies_to(target) is False


@pytest.mark.asyncio
async def test_experience_root_cause_gate_reconsiders_whole_trajectory_rejection():
    target, gate = _gradient_target(
        [
            '{"pass": false, "root_cause_quality": "wrong_scope", '
            '"reason": "The candidate only addresses the missing total but ignores other failures", '
            '"expected_behavior_change": "include the total", "repair_prompt": "broaden it", '
            '"risks": []}',
            '{"uphold_rejection": false, "root_cause_quality": "sufficient", '
            '"reason": "The candidate completely repairs its own evidenced pattern", '
            '"repair_prompt": ""}',
        ]
    )

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "warn"
    assert "overruled" in decision.reason
    assert len(gate.vlm.calls) == 1


@pytest.mark.asyncio
async def test_experience_root_cause_gate_overrules_chinese_sibling_failure_rejection():
    target, gate = _gradient_target(
        '{"pass": false, "root_cause_quality": "mixed_root_causes", '
        '"reason": "该经验仅针对表头格式问题，但未覆盖同一决策边界的其他核心失败", '
        '"expected_behavior_change": "match the sample headers", '
        '"repair_prompt": "broaden it", "risks": []}'
    )

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "warn"
    assert "unrelated-failures" in decision.reason
    assert len(gate.vlm.calls) == 1


@pytest.mark.asyncio
async def test_experience_plan_gate_overrules_failed_criteria_sibling_rejection():
    item = _plan_item()
    gate = ExperiencePlanQualityGate(
        vlm=FakeVLM(
            '{"pass": false, "root_cause_quality": "missing_behavior_change", '
            '"reason": "The proposed experience only mandates defining the core concept, but '
            'does not address the other two failed criteria from the source trajectory", '
            '"expected_behavior_change": "", "repair_prompt": "broaden it", "risks": []}'
        )
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "warn"
    assert "unrelated-failures" in decision.reason
    assert len(gate.vlm.calls) == 1


@pytest.mark.asyncio
async def test_experience_plan_gate_keeps_candidate_local_defect_rejection():
    item = _plan_item()
    gate = ExperiencePlanQualityGate(
        vlm=FakeVLM(
            '{"pass": false, "root_cause_quality": "over_broad", '
            '"reason": "The candidate only addresses one issue, but it fails to bind the rule '
            'to runtime evidence and is overly broad", '
            '"expected_behavior_change": "", "repair_prompt": "narrow it", "risks": []}'
        )
    )
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "fails to bind" in decision.reason


@pytest.mark.asyncio
async def test_experience_root_cause_prevention_gate_fails_open_on_llm_error():
    target, gate = _gradient_target(RuntimeError("model unavailable"))

    decision = await gate.evaluate(target)

    assert decision is not None
    assert decision.action == "warn"
    assert "failed open" in decision.reason
