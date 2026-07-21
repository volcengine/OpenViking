# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.train import (
    CriterionResult,
    Experience,
    ExperienceGradientContext,
    ExperienceGradientEstimator,
    ExperienceSet,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.components import gradient_estimator as gradient_estimator_module
from openviking.session.train.gates import GateDecision, GateReport
from openviking_cli.session.user_id import UserIdentifier


class FakeExperienceGradientEstimator(ExperienceGradientEstimator):
    def __init__(self, operations_by_trajectory_uri):
        super().__init__()
        self.operations_by_trajectory_uri = operations_by_trajectory_uri
        self.calls = []

    async def _run_extract_loop(self, trajectory, context):
        self.calls.append((trajectory, context))
        context.metadata["final_gate_report"] = GateReport(
            stage="post_gradient",
            evaluated_count=1,
            allowed_count=1,
        ).to_dict()
        return self.operations_by_trajectory_uri.get(trajectory.uri)


def _analysis(*, passed: bool = True, outcome: str = "success") -> RolloutAnalysis:
    return RolloutAnalysis(
        evaluation=RubricEvaluation(
            passed=passed,
            score=1.0 if passed else 0.0,
            criterion_results=[
                CriterionResult(
                    criterion_name="done",
                    passed=passed,
                    score=1.0 if passed else 0.0,
                    feedback=[],
                    evidence=["evidence"],
                )
            ],
            feedback=[],
        ),
        trajectories=[
            Trajectory(
                name="booking_duplicate",
                uri="viking://user/u/memories/trajectories/booking_duplicate.md",
                content="trajectory content",
                outcome=outcome,
                retrieval_anchor="Stage: final",
                metadata={"training_category": "booking"},
            )
        ],
    )


def _experience_set() -> ExperienceSet:
    return ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Experience(
                name="booking_duplicate_handling",
                uri="viking://user/u/memories/experiences/booking_duplicate_handling.md",
                version=3,
                status="production",
                content="old body from policy set",
            )
        ],
    )


def _context() -> ExperienceGradientContext:
    return ExperienceGradientContext(
        request_context=RequestContext(UserIdentifier("account", "u"), Role.USER),
        messages=[],
    )


def _experience_fields(
    *,
    name: str = "booking_duplicate_handling",
    situation: str = (
        "- Applies when: duplicate booking evidence is present.\n"
        "- Does not apply when: no existing booking matches.\n"
        "- Evidence binding: retrieved booking records.\n"
        "- Decision boundary: before submitting the candidate booking."
    ),
    reminder: str = "- Check for a duplicate before booking.",
    procedure: str = "- Compare the candidate with retrieved bookings.",
    anti_pattern: str = "- Do not create a duplicate booking.",
) -> dict[str, str]:
    return {
        "experience_name": name,
        "situation": situation,
        "reminder": reminder,
        "procedure": procedure,
        "anti_pattern": anti_pattern,
    }


def test_retain_gated_experience_operations_keeps_only_allowed_candidates():
    accepted_fields = _experience_fields(name="specific_repair", reminder="specific body")
    rejected_fields = _experience_fields(name="generic_checklist", reminder="generic body")
    operations = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(memory_type="experiences", memory_fields=accepted_fields),
            SimpleNamespace(memory_type="experiences", memory_fields=rejected_fields),
        ]
    )
    gradient = SimpleNamespace(metadata={"memory_fields": dict(accepted_fields)})

    gradient_estimator_module._retain_gated_experience_operations(operations, [gradient])

    assert len(operations.upsert_operations) == 1
    assert operations.upsert_operations[0].memory_fields == accepted_fields


def test_gated_experience_operations_survive_later_repair_draft():
    accepted_fields = _experience_fields(name="specific_repair", reminder="specific body")
    accepted_operation = SimpleNamespace(
        memory_type="experiences",
        memory_fields=accepted_fields,
        uris=["viking://user/u/memories/experiences/specific_repair.md"],
    )
    first = SimpleNamespace(upsert_operations=[accepted_operation])
    gradient = SimpleNamespace(metadata={"memory_fields": dict(accepted_fields)})
    retained = {}

    gradient_estimator_module._remember_gated_experience_operations(
        first,
        [gradient],
        retained_upserts=retained,
    )

    final = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields=_experience_fields(
                    name="combined_invalid",
                    reminder="invalid body",
                ),
                uris=["viking://user/u/memories/experiences/combined_invalid.md"],
            )
        ]
    )
    gradient_estimator_module._restore_gated_experience_operations(
        final,
        retained_upserts=retained,
    )

    assert [
        operation.memory_fields["experience_name"] for operation in final.upsert_operations
    ] == ["specific_repair"]


@pytest.mark.asyncio
async def test_experience_gradient_checks_semantic_gate_after_partial_deterministic_rejection(
    monkeypatch,
):
    analysis = _analysis(passed=False, outcome="failure")
    good = SimpleNamespace(target_name="good")
    bad = SimpleNamespace(target_name="bad")
    calls = []

    class DeterministicRunner:
        async def filter_gradients(self, gradients, *, analyses, policy_set):
            assert gradients == [good, bad]
            return [good], GateReport(
                stage="post_gradient",
                evaluated_count=2,
                allowed_count=1,
                rejected_count=1,
                decisions=[
                    GateDecision(
                        gate_name="deterministic",
                        action="reject",
                        reason="bad shape",
                        evidence={"target_name": "bad"},
                        retriable=True,
                        repair_prompt="repair the shape",
                    )
                ],
            )

    class SemanticRunner:
        async def filter_gradients(self, gradients, *, analyses, policy_set):
            calls.append(list(gradients))
            return list(gradients), GateReport(
                stage="post_gradient",
                evaluated_count=1,
                allowed_count=1,
            )

    monkeypatch.setattr(
        gradient_estimator_module,
        "default_policy_gate_runner",
        lambda: DeterministicRunner(),
    )
    monkeypatch.setattr(
        gradient_estimator_module,
        "_experience_extract_gate_runner",
        lambda vlm: SemanticRunner(),
    )

    gated, report = await gradient_estimator_module._evaluate_experience_gradients(
        gradients=[good, bad],
        analysis=analysis,
        experience_set=_experience_set(),
        semantic_vlm=object(),
    )

    assert calls == [[good]]
    assert gated == [good]
    assert report.evaluated_count == 2
    assert report.allowed_count == 1
    assert report.rejected_count == 1


@pytest.mark.asyncio
async def test_experience_gradient_estimator_audits_missing_trajectory():
    analysis = RolloutAnalysis(
        evaluation=RubricEvaluation(
            passed=False,
            score=0.0,
            criterion_results=[],
            feedback=[],
        ),
        trajectories=[],
        metadata={
            "case_name": "case_17",
            "evidence_source_summary": {
                "direct_available": False,
                "source_count": 0,
                "advisory_signal_count": 1,
            },
            "trajectory_post_validation_retries": [
                {
                    "retry_index": 3,
                    "final_outcome": "discarded_after_max_retries",
                    "issues": [{"reason": "missing evidence"}],
                }
            ],
        },
    )

    gradients = await ExperienceGradientEstimator().estimate(
        analysis,
        _experience_set(),
        _context(),
    )

    assert gradients == []
    assert "experience_dispositions" not in analysis.metadata


@pytest.mark.asyncio
async def test_experience_gradient_estimator_skips_success_trajectories():
    analysis = _analysis(passed=True, outcome="success")
    operations = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields={"content": "should not be used"},
                uris=["viking://user/u/memories/experiences/unused.md"],
                old_memory_file_content=None,
            )
        ]
    )
    estimator = FakeExperienceGradientEstimator({analysis.trajectories[0].uri: operations})

    gradients = await estimator.estimate(analysis, _experience_set(), _context())

    assert gradients == []
    assert estimator.calls == []
    assert "experience_dispositions" not in analysis.metadata


@pytest.mark.asyncio
async def test_experience_gradient_estimator_converts_experience_operations():
    analysis = _analysis(passed=False, outcome="failure")
    old_file = MemoryFile(
        uri="viking://user/u/memories/experiences/booking_duplicate_handling.md",
        content="old body with [[links]]",
        memory_type="experiences",
        extra_fields={"version": "7"},
    )
    operations = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields={
                    **_experience_fields(),
                    "supersedes": ["older_experience"],
                },
                uris=["viking://user/u/memories/experiences/booking_duplicate_handling.md"],
                old_memory_file_content=old_file,
            ),
            SimpleNamespace(
                memory_type="trajectories",
                memory_fields={"content": "ignored"},
                uris=["viking://user/u/memories/trajectories/ignored.md"],
                old_memory_file_content=None,
            ),
        ]
    )
    estimator = FakeExperienceGradientEstimator({analysis.trajectories[0].uri: operations})

    gradients = await estimator.estimate(analysis, _experience_set(), _context())

    assert len(gradients) == 1
    gradient = gradients[0]
    assert gradient.target_name == "booking_duplicate_handling"
    assert gradient.target_uri == (
        "viking://user/u/memories/experiences/booking_duplicate_handling.md"
    )
    assert gradient.base_version == 7
    assert gradient.before_file is old_file
    assert "## Situation" in gradient.after_file.content
    assert gradient.after_file.extra_fields["supersedes"] == ["older_experience"]
    assert gradient.metadata["supersedes"] == ["older_experience"]
    assert len(gradient.links) == 1
    assert gradient.links[0].from_uri == gradient.target_uri
    assert gradient.links[0].to_uri == analysis.trajectories[0].uri
    assert gradient.links[0].link_type == "derived_from"
    assert gradient.links[0].match_text is None
    assert gradient.links[0].description == ""
    assert gradient.confidence == pytest.approx(0.3)
    assert gradient.metadata["trajectory_outcome"] == "failure"
    assert gradient.metadata["rubric_passed"] is False
    assert gradient.metadata["training_category"] == "booking"
    assert len(estimator.calls) == 1
    assert "experience_dispositions" not in analysis.metadata


@pytest.mark.asyncio
async def test_experience_gradient_estimator_uses_policy_version_for_newer_old_file_absence():
    analysis = _analysis(passed=False, outcome="failure")
    operations = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields=_experience_fields(),
                uris=["viking://user/u/memories/experiences/booking_duplicate_handling.md"],
                old_memory_file_content=None,
            )
        ]
    )
    estimator = FakeExperienceGradientEstimator({analysis.trajectories[0].uri: operations})

    gradients = await estimator.estimate(analysis, _experience_set(), _context())

    assert len(gradients) == 1
    gradient = gradients[0]
    assert gradient.target_name == "booking_duplicate_handling"
    assert gradient.base_version == 3
    assert gradient.before_file is None
    assert "## Situation" in gradient.after_file.content
    assert gradient.confidence == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_experience_gradient_estimator_runs_trajectory_extracts_in_parallel():
    analysis = _analysis(passed=False, outcome="failure")
    analysis.trajectories.append(
        Trajectory(
            name="booking_duplicate_second",
            uri="viking://user/u/memories/trajectories/booking_duplicate_second.md",
            content="second trajectory content",
            outcome="failure",
            retrieval_anchor="Stage: final",
        )
    )

    class ParallelProbeEstimator(ExperienceGradientEstimator):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.all_started = asyncio.Event()

        async def _run_extract_loop(self, trajectory, context):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == len(analysis.trajectories):
                self.all_started.set()
            try:
                await asyncio.wait_for(self.all_started.wait(), timeout=0.2)
                return None
            finally:
                self.active -= 1

    estimator = ParallelProbeEstimator()

    assert await estimator.estimate(analysis, _experience_set(), _context()) == []
    assert estimator.max_active == len(analysis.trajectories)


@pytest.mark.asyncio
async def test_experience_gradient_estimator_skips_empty_content_and_handles_extract_errors():
    analysis = _analysis(passed=False, outcome="failure")
    estimator = FakeExperienceGradientEstimator({})

    async def raise_error(_trajectory, _context):
        raise RuntimeError("extract failure")

    estimator._run_extract_loop = raise_error

    assert await estimator.estimate(analysis, _experience_set(), _context()) == []
    assert "experience_dispositions" not in analysis.metadata

    strict_context = ExperienceGradientContext(
        request_context=RequestContext(UserIdentifier("account", "u"), Role.USER),
        messages=[],
        strict_extract_errors=True,
    )
    with pytest.raises(RuntimeError, match="extract failure"):
        await estimator.estimate(analysis, _experience_set(), strict_context)


@pytest.mark.asyncio
async def test_experience_gradient_estimator_runs_extract_loop(monkeypatch):
    analysis = _analysis(passed=False, outcome="failure")
    captured = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            captured["provider_kwargs"] = kwargs

    class FakeIsolationHandler:
        def __init__(self, request_context, extract_context, allowed_memory_types):
            captured["request_context"] = request_context
            captured["extract_context"] = extract_context
            captured["allowed_memory_types"] = allowed_memory_types

        def prepare_messages(self):
            captured["prepare_messages_called"] = True

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured["extract_loop_kwargs"] = kwargs

        async def run(self):
            return SimpleNamespace(upsert_operations=[]), {"summary": "ok"}

    monkeypatch.setattr(gradient_estimator_module, "AgentExperienceContextProvider", FakeProvider)
    monkeypatch.setattr(gradient_estimator_module, "MemoryIsolationHandler", FakeIsolationHandler)
    monkeypatch.setattr(gradient_estimator_module, "ExtractLoop", FakeExtractLoop)

    estimator = ExperienceGradientEstimator(viking_fs=SimpleNamespace(), vlm=SimpleNamespace())
    context = _context()

    gradients = await estimator.estimate(analysis, _experience_set(), context)

    assert gradients == []
    assert captured["provider_kwargs"] == {
        "trajectory_summary": analysis.trajectories[0].content,
        "trajectory_uri": analysis.trajectories[0].uri,
        "loaded_experience_uris": [],
    }
    assert captured["request_context"] is context.request_context
    assert captured["allowed_memory_types"] == {"experiences"}
    assert captured["prepare_messages_called"] is True
    assert captured["extract_loop_kwargs"]["context_provider"]._isolation_handler is not None


def test_loaded_experience_uris_include_completed_read_experience_calls():
    analysis = _analysis(passed=False, outcome="failure")
    loaded_uri = "viking://user/u/memories/experiences/loaded.md"
    analysis.metadata["rollout_messages"] = [
        {
            "role": "user",
            "parts": [
                {
                    "type": "tool",
                    "tool_name": "read_experience",
                    "tool_status": "completed",
                    "tool_input": {"experience_uri": loaded_uri},
                    "tool_output": f"# Loaded Experience\n\nExperience URI: `{loaded_uri}`",
                }
            ],
        }
    ]

    assert gradient_estimator_module._loaded_experience_uris(analysis) == [loaded_uri]


@pytest.mark.asyncio
async def test_post_validation_gate_sees_prefetched_comparison_trajectories(monkeypatch):
    analysis = _analysis(passed=False, outcome="failure")
    comparison = [
        {
            "uri": "viking://user/u/memories/trajectories/same_case_success.md",
            "outcome": "success",
            "content": "# same case success\n- Outcome: success\n- Communication: included total.",
        }
    ]
    captured = {"metadata_seen": []}

    class FakeProvider:
        def __init__(self, **kwargs):
            captured["provider_kwargs"] = kwargs
            self.prefetched_comparison_trajectories = list(comparison)

    class FakeIsolationHandler:
        def __init__(self, request_context, extract_context, allowed_memory_types):
            self.request_context = request_context
            self.extract_context = extract_context
            self.allowed_memory_types = allowed_memory_types

        def prepare_messages(self):
            captured["prepare_messages_called"] = True

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            captured["extract_loop_kwargs"] = kwargs

        async def run(self):
            operations = SimpleNamespace(
                upsert_operations=[
                    SimpleNamespace(
                        memory_type="experiences",
                        memory_fields=_experience_fields(
                            name="scope_total",
                            situation=(
                                "- Applies when: a scoped total is requested.\n"
                                "- Does not apply when: no scoped total is requested.\n"
                                "- Source binding: request records."
                            ),
                            reminder="- Preserve the requested total scope.",
                            procedure="- Calculate and communicate the requested total.",
                            anti_pattern="- Do not omit the requested total.",
                        ),
                        uris=["viking://user/u/memories/experiences/scope_total.md"],
                        old_memory_file_content=None,
                    )
                ],
                delete_file_contents=[],
                errors=[],
            )
            decision = await captured["extract_loop_kwargs"]["post_validation_hook"](
                operations,
                0,
                messages=[{"role": "user", "content": "draft"}],
                latest_draft=operations,
            )
            captured["post_validation_decision"] = decision
            return operations, {"summary": "ok"}

    async def fake_evaluate_experience_gradients(
        *,
        gradients,
        analysis,
        experience_set,
        semantic_vlm=None,
    ):
        captured["metadata_seen"].append(
            list(analysis.trajectories[0].metadata.get("comparison_trajectories") or [])
        )
        return gradients, GateReport(
            stage="post_gradient",
            evaluated_count=len(gradients),
            allowed_count=len(gradients),
        )

    monkeypatch.setattr(gradient_estimator_module, "AgentExperienceContextProvider", FakeProvider)
    monkeypatch.setattr(gradient_estimator_module, "MemoryIsolationHandler", FakeIsolationHandler)
    monkeypatch.setattr(gradient_estimator_module, "ExtractLoop", FakeExtractLoop)
    monkeypatch.setattr(
        gradient_estimator_module,
        "_evaluate_experience_gradients",
        fake_evaluate_experience_gradients,
    )

    estimator = ExperienceGradientEstimator(viking_fs=SimpleNamespace(), vlm=SimpleNamespace())

    gradients = await estimator.estimate(analysis, _experience_set(), _context())

    assert len(gradients) == 1
    assert gradients[0].metadata["experience_gate_validation"] == "post_validation_hook"
    assert captured["post_validation_decision"] is None
    assert captured["metadata_seen"] == [comparison]
    assert analysis.trajectories[0].metadata["comparison_trajectory_uris"] == [comparison[0]["uri"]]
    assert analysis.metadata["gate_attempts"] == [
        {
            "stage": "post_gradient",
            "index": 0,
            "result": "passed",
            "targets": [
                {
                    "name": "scope_total",
                    "uri": "viking://user/u/memories/experiences/scope_total.md",
                    "outcome": "allowed",
                    "decisions": [],
                }
            ],
        }
    ]
    assert "experience_dispositions" not in analysis.metadata
