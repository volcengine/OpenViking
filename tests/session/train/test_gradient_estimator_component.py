# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

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
from openviking.session.train.gates import GateReport


class FakeExperienceGradientEstimator(ExperienceGradientEstimator):
    def __init__(self, operations_by_trajectory_uri):
        super().__init__()
        self.operations_by_trajectory_uri = operations_by_trajectory_uri
        self.calls = []

    async def _run_extract_loop(self, trajectory, context):
        self.calls.append((trajectory, context))
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
    return ExperienceGradientContext(request_context=SimpleNamespace(), messages=[])


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
                    "experience_name": "booking_duplicate_handling",
                    "constraint": "## Situation\n- Applies when: candidate booking may duplicate an existing retrieved booking.\n- Does not apply when: no matching existing booking is retrieved.\n- Source binding: user request and retrieved booking records.\n\n## Reminder\n- Avoid creating a duplicate booking for the same confirmed trip.\n\n## Procedure\n- Before booking: compare the candidate trip to retrieved bookings.\n- If it duplicates an existing booking: do not book and explain.\n- Else: proceed.\n\n## Anti-pattern\n- Do not book a duplicate reservation.\n- Preserve genuinely new bookings.",
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


@pytest.mark.asyncio
async def test_experience_gradient_estimator_uses_policy_version_for_newer_old_file_absence():
    analysis = _analysis(passed=False, outcome="failure")
    operations = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields={
                    "constraint": "## Situation\n- Applies when: candidate booking may duplicate an existing retrieved booking.\n- Does not apply when: no matching existing booking is retrieved.\n- Source binding: user request and retrieved booking records.\n\n## Reminder\n- Avoid creating a duplicate booking for the same confirmed trip.\n\n## Procedure\n- Before booking: compare the candidate trip to retrieved bookings.\n- If it duplicates an existing booking: do not book and explain.\n- Else: proceed.\n\n## Anti-pattern\n- Do not book a duplicate reservation.\n- Preserve genuinely new bookings.",
                },
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

    strict_context = ExperienceGradientContext(
        request_context=SimpleNamespace(),
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
        "messages": context.messages,
        "trajectory_summary": analysis.trajectories[0].content,
        "trajectory_uri": analysis.trajectories[0].uri,
    }
    assert captured["request_context"] is context.request_context
    assert captured["allowed_memory_types"] == {"experiences"}
    assert captured["prepare_messages_called"] is True
    assert captured["extract_loop_kwargs"]["context_provider"]._isolation_handler is not None


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
                        memory_fields={
                            "experience_name": "scope_total",
                            "constraint": (
                                "## Situation\n"
                                "- Applies when: a scoped total is requested.\n"
                                "- Does not apply when: no scoped total is requested.\n"
                                "- Source binding: request records.\n\n"
                                "## Reminder\n- Preserve the requested total scope.\n\n"
                                "## Procedure\n- Calculate and communicate the requested total.\n\n"
                                "## Anti-pattern\n- Do not omit the requested total."
                            ),
                        },
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
    assert captured["post_validation_decision"] is None
    assert captured["metadata_seen"]
    assert all(item == comparison for item in captured["metadata_seen"])
    assert analysis.trajectories[0].metadata["comparison_trajectory_uris"] == [
        comparison[0]["uri"]
    ]
