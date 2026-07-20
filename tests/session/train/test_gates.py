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
    ExperienceCausalSignalGate,
    ExperienceEvidenceSafetyGate,
    ExperienceLanguageBindingGate,
    ExperienceNamePolarityGate,
    ExperiencePlanQualityGate,
    ExperiencePortabilityGate,
    ExperienceRootCausePreventionGate,
    ExperienceSkillReadabilityGate,
    ExperienceSpecificityGate,
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


def test_default_policy_gate_runner_uses_layered_experience_quality_gates():
    names = [gate.name for gate in default_policy_gate_runner().gates]

    assert names == [
        "experience_causal_signal",
        "experience_skill_readability",
        "experience_name_polarity",
        "experience_specificity",
        "experience_language_binding",
        "experience_evidence_safety",
        "experience_portability",
        "experience_plan_quality",
    ]
    assert "experience_counterfactual_reflection" not in names
    assert "experience_root_cause_prevention" not in names
    assert "experience_tool_alignment" not in names
    assert "experience_content_format" not in names
    assert "experience_trigger_shape" not in names
    assert "experience_update_narrowing" not in names

    contract = default_experience_gate_contract()
    assert "Content format" not in contract
    assert "Counterfactual reflection" not in contract
    assert "Runtime-only wording" not in contract
    assert "Trigger runtime compatibility" not in contract
    assert "Skill-loader readability" in contract
    assert "Specific behavior delta" in contract
    assert "Explicit language binding" in contract
    assert "`## Situation`" in contract
    assert "eligible for experience learning by default" in contract
    assert "Recommended operation=skip" in contract
    assert "Existing target experience=none only means" in contract
    assert "not a temporal" in contract
    assert "`Evidence binding`" in contract
    assert "`Decision boundary`" in contract
    assert "canonical runtime value field" not in contract
    assert "Action is create/update" not in contract
    assert "Candidate-shape trigger" not in contract
    assert "Update safety" not in contract


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
    assert set(decision.evidence["missing_situation_fields"]) == {
        "Does not apply when",
        "Evidence binding",
        "Decision boundary",
    }
    assert "missing Situation fields" in decision.reason


@pytest.mark.asyncio
async def test_skill_readability_gate_rejects_temporal_does_not_apply():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: a requested total may be communicated after later writes.\n"
        "- Does not apply when: still reading records before final_response.\n"
        "- Evidence binding: user request scope and retrieved records.\n"
        "- Decision boundary: before composing the requested answer.\n\n"
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
async def test_skill_readability_gate_accepts_domain_neutral_evidence_binding():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: the task requires a structured deliverable with named sections.\n"
        "- Does not apply when: the user requests unstructured brainstorming only.\n"
        "- Evidence binding: the user's requested section list and the current draft.\n"
        "- Decision boundary: before saving the final deliverable.\n\n"
        "## Reminder\n"
        "- Verify every named section before delivery.\n\n"
        "## Procedure\n"
        "- Before saving: compare the requested section list with the draft.\n"
        "- If a section is absent: add only that section.\n"
        "- Else: preserve the completed draft.\n\n"
        "## Anti-pattern\n"
        "- Do not declare completion while a named section is missing.\n"
        "- Preserve sections already supported by source material.\n"
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
async def test_specificity_gate_rejects_generic_requirement_checklist():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: creating an artifact with explicit requirements.\n"
        "- Does not apply when: the task has no explicit requirements.\n"
        "- Evidence binding: the user request and current artifact.\n"
        "- Decision boundary: before finalizing the artifact.\n\n"
        "## Reminder\n"
        "- Systematically check off each explicit requirement as it is implemented.\n\n"
        "## Procedure\n"
        "- Create a checklist from the user's explicit requirements.\n"
        "- For each requirement, mark it as implemented.\n\n"
        "## Anti-pattern\n"
        "- Do not assume all requirements are met.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert len(decision.evidence["generic_signals"]) >= 2


@pytest.mark.asyncio
async def test_specificity_gate_rejects_generic_inventory_and_validation_rule():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: creating a formal document with explicit requirements.\n"
        "- Does not apply when: writing informal notes.\n"
        "- Evidence binding: user request and draft.\n"
        "- Decision boundary: before drafting.\n\n"
        "## Reminder\n"
        "- Validate all explicit user requirements before creating the document.\n\n"
        "## Procedure\n"
        "- List every explicit user requirement before drafting.\n"
        "- Map each requirement to the draft.\n\n"
        "## Anti-pattern\n"
        "- Do not skip requirements.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_requirement_reminder",
        "generic_requirement_inventory",
    }


@pytest.mark.asyncio
async def test_specificity_gate_rejects_generic_document_checklist_scope():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: creating a document with explicit enumerated requirements.\n"
        "- Does not apply when: writing open-ended notes.\n"
        "- Evidence binding: user request.\n"
        "- Decision boundary: before planning.\n\n"
        "## Reminder\n"
        "- Compile a full checklist of explicit user requirements before planning.\n\n"
        "## Procedure\n"
        "- Extract all explicit requirements into the checklist.\n\n"
        "## Anti-pattern\n"
        "- Do not start without the checklist.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_full_requirement_checklist",
        "generic_document_requirement_scope",
    }


@pytest.mark.asyncio
async def test_specificity_gate_uses_generic_target_name_as_signal():
    item = _plan_item()
    item.target_name = "verify_all_requirements_before_finalizing_document"
    item.after_content = (
        "## Situation\n"
        "- Applies when: creating a formal document with listed content topics.\n"
        "- Does not apply when: writing open-ended notes.\n"
        "- Evidence binding: user request.\n"
        "- Decision boundary: before finalizing.\n\n"
        "## Reminder\n"
        "- Check required content and length constraints.\n\n"
        "## Procedure\n"
        "- Create a checklist of all user-specified required content items.\n\n"
        "## Anti-pattern\n"
        "- Do not omit requested content.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_required_item_checklist",
        "generic_requirement_name",
    }


@pytest.mark.asyncio
async def test_specificity_gate_rejects_generic_requirement_mapping_before_document_generation():
    item = _plan_item()
    item.target_name = "map_requirements_before_doc_generation"
    item.after_content = (
        "## Situation\n"
        "- Applies when: generating a structured document with explicit enumerated user requirements.\n"
        "- Does not apply when: writing open-ended notes.\n"
        "- Evidence binding: user request.\n"
        "- Decision boundary: before writing a generation script.\n\n"
        "## Reminder\n"
        "- Map all user-specified requirements before generating the document.\n\n"
        "## Procedure\n"
        "- Extract a complete checklist of all explicit user requirements.\n\n"
        "## Anti-pattern\n"
        "- Do not omit requirements.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_universal_requirement_scope",
        "generic_requirement_mapping",
        "generic_artifact_requirement_scope",
    }


@pytest.mark.asyncio
async def test_specificity_gate_rejects_generic_artifact_delivery_checklist():
    item = _plan_item()
    item.target_name = "verify_explicit_requirements_against_artifact_before_delivery"
    item.after_content = (
        "## Situation\n"
        "- Applies when: generating structured artifacts from explicit user requirements.\n"
        "- Does not apply when: answering an open-ended question.\n"
        "- Evidence binding: user request and generated artifact.\n"
        "- Decision boundary: before delivery.\n\n"
        "## Reminder\n"
        "- Check every explicit user requirement line-by-line against the artifact.\n\n"
        "## Procedure\n"
        "- Create a checklist of every explicit user requirement and verify each item.\n\n"
        "## Anti-pattern\n"
        "- Do not skip any requirement.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_universal_requirement_scope",
        "generic_line_by_line_requirement_check",
        "generic_artifact_requirement_scope",
    }


@pytest.mark.asyncio
async def test_specificity_gate_rejects_generic_required_content_cross_check():
    item = _plan_item()
    item.target_name = "cross_check_required_content_items_before_delivery"
    item.after_content = (
        "## Situation\n"
        "- Applies when: creating a document where the user provides an explicit list of required content items.\n"
        "- Does not apply when: writing free-form notes.\n"
        "- Evidence binding: user request and document artifact.\n"
        "- Decision boundary: before delivery.\n\n"
        "## Reminder\n"
        "- Cross-check each explicit required content item against the document.\n\n"
        "## Procedure\n"
        "- Extract the full list and verify every item.\n\n"
        "## Anti-pattern\n"
        "- Do not omit requested content.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_universal_required_content",
        "generic_document_required_content_scope",
    }


@pytest.mark.asyncio
async def test_specificity_gate_rejects_generic_content_requirement_inventory():
    item = _plan_item()
    item.target_name = "inventory_requirements_before_drafting_strategy_deliverables"
    item.after_content = (
        "## Situation\n"
        "- Applies when: a strategy document has enumerated content requirements.\n"
        "- Does not apply when: the task is open-ended.\n"
        "- Evidence binding: user request.\n"
        "- Decision boundary: before drafting.\n\n"
        "## Reminder\n"
        "- Inventory all user-specified content requirements before drafting.\n\n"
        "## Procedure\n"
        "- Extract every explicit content requirement and map it to a section.\n\n"
        "## Anti-pattern\n"
        "- Do not draft before mapping requirements.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_content_requirement_inventory",
        "generic_universal_content_requirement_scope",
        "generic_requirement_name",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_name", "reminder", "expected_signal"),
    [
        (
            "list_explicit_slide_content_requirements_before_drafting",
            "List all explicit slide content requirements before drafting.",
            "generic_universal_content_requirement_scope",
        ),
        (
            "verify_all_required_topics_gathered_before_content_creation",
            "Verify all required topics before creating content.",
            "generic_universal_required_topics",
        ),
        (
            "verify_explicit_requirements_before_final_output",
            "Compile and review a full checklist of all explicit requirements.",
            "generic_compile_review_checklist",
        ),
    ],
)
async def test_specificity_gate_rejects_generic_action_name_synonyms(
    target_name,
    reminder,
    expected_signal,
):
    item = _plan_item()
    item.target_name = target_name
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        reminder,
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_requirement_name",
        expected_signal,
    }


@pytest.mark.asyncio
async def test_specificity_gate_rejects_internal_cleanup_merged_with_source_check():
    item = _plan_item()
    item.target_name = "clean_final_output_and_check_sources"
    item.after_content = (
        "## Situation\n"
        "- Applies when: finalizing a user-facing deliverable.\n"
        "- Does not apply when: doing internal analysis only.\n"
        "- Evidence binding: user request and source materials.\n"
        "- Decision boundary: before final delivery.\n\n"
        "## Reminder\n"
        "- Remove internal workflow metadata and include required source references.\n\n"
        "## Procedure\n"
        "- Remove internal paths from the final user-facing output.\n"
        "- Verify source references are included when requested.\n\n"
        "## Anti-pattern\n"
        "- Do not expose internal process details or omit citations.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert set(decision.evidence["independent_concerns"]) == {
        "internal_output_cleanup",
        "source_reference_completeness",
    }


@pytest.mark.asyncio
async def test_evidence_safety_gate_rejects_unpermitted_placeholder_assumptions():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "If unsure about a required field, add placeholders or make reasonable assumptions.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceEvidenceSafetyGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert decision.evidence["matches"]


@pytest.mark.asyncio
async def test_evidence_safety_gate_allows_explicit_placeholder_permission():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "user request scope and retrieved record prices",
        "user explicitly allows placeholders for unavailable values",
    ).replace(
        "Include the requested source-bound total in the final message.",
        "If a required value is unavailable, use a placeholder.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceEvidenceSafetyGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_evidence_safety_gate_rejects_mandatory_unrequested_conventions():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        (
            "Literature reviews require foundational definitions even when they are not "
            "explicitly requested."
        ),
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceEvidenceSafetyGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "unrequested convention" in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "literal",
    [
        "e.g., 30 standard hours per day",
        "Evidence binding: user request specifying a 3-5 year range",
        'the "STD SALES" tab',
        "use recalc.py to validate formulas",
        "a May Weeks 1-4 sales plan",
    ],
)
async def test_portability_gate_rejects_source_case_literals(literal):
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        f"Use {literal} before finalizing.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperiencePortabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"


@pytest.mark.asyncio
async def test_portability_gate_allows_runtime_bound_range_and_sheet():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Read the required range from the user request and the target tab from the workbook.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperiencePortabilityGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_language_binding_gate_rejects_geography_to_language_inference():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "For a US-based audience, use English for all content.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceLanguageBindingGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "geography" in decision.reason


@pytest.mark.asyncio
async def test_language_binding_gate_rejects_audience_language_target_and_using_wording():
    item = _plan_item()
    item.target_name = "match_output_language_to_audience"
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "For a US-based audience, prioritize using the region's common language, English.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceLanguageBindingGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"


@pytest.mark.asyncio
async def test_language_binding_gate_rejects_audience_that_implies_language():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Scan for an explicit language or a target audience that implies a specific language.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceLanguageBindingGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"


@pytest.mark.asyncio
async def test_language_binding_gate_allows_audience_locale_without_language_inference():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Use explicit language instructions; use audience locale only for spelling conventions.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceLanguageBindingGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_rule",
    [
        "Do not infer the output language from the audience or locale.",
        "不要在没有明确语言指令的情况下仅根据受众信息选择语言。",
    ],
)
async def test_language_binding_gate_allows_prohibition_of_audience_language_inference(
    runtime_rule: str,
):
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        runtime_rule,
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceLanguageBindingGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_language_binding_gate_rejects_chinese_audience_language_inference():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "如果用户描述了目标受众，根据受众确定合适的输出语言。",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceLanguageBindingGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"


@pytest.mark.asyncio
async def test_name_polarity_gate_rejects_skip_name_when_body_says_do_not_skip():
    item = _plan_item()
    item.target_name = "skip_required_components_for_notes"
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Do not skip required components or replace them with setup notes.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceNamePolarityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "prohibits" in decision.reason


@pytest.mark.asyncio
async def test_name_polarity_gate_allows_consistent_avoid_name():
    item = _plan_item()
    item.target_name = "avoid_internal_metadata_in_final_answers"
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Never mention internal metadata in final answers.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceNamePolarityGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_specificity_gate_rejects_compound_independent_repairs():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: drafting a review around a user-named topic.\n"
        "- Does not apply when: the topic is outside the requested review.\n"
        "- Evidence binding: the user request and source material.\n"
        "- Decision boundary: before drafting.\n\n"
        "## Reminder\n"
        "- Define the core term, distinguish two categories, address variation, and cover measurement methods.\n\n"
        "## Procedure\n"
        "- Add each missing subject to the draft.\n\n"
        "## Anti-pattern\n"
        "- Do not omit independently requested subjects.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert set(decision.evidence["repair_actions"]) == {
        "define",
        "distinguish",
        "address",
        "cover",
    }


@pytest.mark.asyncio
async def test_specificity_gate_rejects_generic_component_inventory_checklist():
    item = _plan_item()
    item.target_name = "extract_user_specified_components_before_planning"
    item.after_content = (
        "## Situation\n"
        "- Applies when: a user lists named sections for a structured artifact.\n"
        "- Does not apply when: no structure is specified.\n"
        "- Evidence binding: the user request.\n"
        "- Decision boundary: before planning.\n\n"
        "## Reminder\n"
        "- Extract and list every user-specified table and section before planning.\n\n"
        "## Procedure\n"
        "- Create a checklist of all user-specified components and use it as the plan.\n\n"
        "## Anti-pattern\n"
        "- Do not omit listed components.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert set(decision.evidence["generic_signals"]) >= {
        "generic_component_inventory",
        "generic_component_checklist",
    }


@pytest.mark.asyncio
async def test_evidence_safety_gate_rejects_unrequested_context_restatement():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Restate all user-provided assumptions and exclusions in the document introduction.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceEvidenceSafetyGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "unrequested output" in decision.reason


@pytest.mark.asyncio
async def test_evidence_safety_gate_allows_explicit_context_restatement_request():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Restate all user-provided assumptions and exclusions in the document introduction.",
    ).replace(
        "Evidence binding: user request scope and retrieved record prices.",
        "Evidence binding: the user explicitly requests to include assumptions in the document.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceEvidenceSafetyGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_evidence_safety_gate_rejects_cramming_fixed_capacity_artifact():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: creating a single-slide deliverable with many listed items.\n"
        "- Does not apply when: multiple slides are allowed.\n"
        "- Evidence binding: requested slide count and content list.\n"
        "- Decision boundary: before drafting.\n\n"
        "## Reminder\n"
        "- Map every item to a region and use dense formatting with nested bullet points if needed.\n\n"
        "## Procedure\n"
        "- Pack all required content into the available regions.\n\n"
        "## Anti-pattern\n"
        "- Do not omit items.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceEvidenceSafetyGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "cramming" in decision.reason


@pytest.mark.asyncio
async def test_evidence_safety_gate_allows_readable_overflow_resolution():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: creating a single-slide deliverable with many listed items.\n"
        "- Does not apply when: multiple slides are allowed.\n"
        "- Evidence binding: requested slide count and content list.\n"
        "- Decision boundary: before drafting.\n\n"
        "## Reminder\n"
        "- Map items to regions while preserving readability.\n\n"
        "## Procedure\n"
        "- Resolve overflow using explicit priorities or ask the user for a scope decision.\n\n"
        "## Anti-pattern\n"
        "- Do not shrink text to hide overflow.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceEvidenceSafetyGate().evaluate(target)

    assert decision is None


@pytest.mark.asyncio
async def test_portability_gate_rejects_mandatory_intermediate_helper_file():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        "Write a visible intermediate checklist file named report_requirements_checklist.md.",
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperiencePortabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "auxiliary artifact" in decision.reason


@pytest.mark.asyncio
async def test_portability_gate_rejects_branded_source_label_example():
    item = _plan_item()
    item.after_content = item.after_content.replace(
        "Include the requested source-bound total in the final message.",
        'Map values to exact runtime labels, e.g., "ExampleVendorRetail proposed price".',
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperiencePortabilityGate().evaluate(target)

    assert decision is not None
    assert decision.action == "reject"
    assert "source-case literals" in decision.reason


@pytest.mark.asyncio
async def test_specificity_gate_allows_requirement_mapping_with_concrete_discriminator():
    item = _plan_item()
    item.after_content = (
        "## Situation\n"
        "- Applies when: a single-slide deck has more than 20 grouped requirements.\n"
        "- Does not apply when: a multi-slide deck may distribute requirements across slides.\n"
        "- Evidence binding: the requested slide count and grouped requirement list.\n"
        "- Decision boundary: before drafting slide copy.\n\n"
        "## Reminder\n"
        "- Map each requirement group to a named slide region before drafting.\n\n"
        "## Procedure\n"
        "- Assign each group to a visible region and resolve overflow before writing copy.\n\n"
        "## Anti-pattern\n"
        "- Do not draft prose before allocating the constrained slide area.\n"
    )
    target = GateTarget(
        stage="post_gradient",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
        trajectory=_trajectory_with_repair_signal(action="create"),
    )

    decision = await ExperienceSpecificityGate().evaluate(target)

    assert decision is None


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
async def test_causal_signal_gate_warns_when_merge_rename_loses_plan_provenance():
    item = _plan_item()
    item.links = []
    item.metadata["merge_gradient_count"] = 1
    target = GateTarget(
        stage="post_plan",
        memory_type="experiences",
        target_kind="plan_item",
        plan_item=item,
    )

    decision = await ExperienceCausalSignalGate().evaluate(target)

    assert decision is not None
    assert decision.action == "warn"
    assert "merge rename" in decision.reason


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
