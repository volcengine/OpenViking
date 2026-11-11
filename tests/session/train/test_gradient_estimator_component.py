# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.session.memory.dataclass import MemoryFile, ResolvedOperation, ResolvedOperations
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
async def test_experience_gradient_estimator_converts_experience_operations():
    analysis = _analysis(passed=True, outcome="success")
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
                    "content": "new body",
                    "reasoning": "The observed state change supports this method only before submission.",
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
    assert gradient.after_file.content == "new body"
    assert (
        gradient.after_file.extra_fields["reasoning"]
        == operations.upsert_operations[0].memory_fields["reasoning"]
    )
    assert gradient.after_file.extra_fields["supersedes"] == ["older_experience"]
    assert gradient.metadata["supersedes"] == ["older_experience"]
    assert len(gradient.links) == 1
    assert gradient.links[0].from_uri == gradient.target_uri
    assert gradient.links[0].to_uri == analysis.trajectories[0].uri
    assert gradient.links[0].link_type == "derived_from"
    assert gradient.links[0].match_text is None
    assert gradient.links[0].description == ""
    assert gradient.confidence == pytest.approx(0.9)
    assert gradient.metadata["trajectory_outcome"] == "success"
    assert gradient.metadata["rubric_passed"] is True
    assert gradient.metadata["training_category"] == "booking"
    assert len(estimator.calls) == 1


@pytest.mark.asyncio
async def test_experience_gradient_estimator_uses_policy_version_for_newer_old_file_absence():
    analysis = _analysis(passed=False, outcome="failure")
    operations = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields={"content": "replacement body"},
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
    assert gradient.after_file.content == "replacement body"
    assert gradient.confidence == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_experience_gradient_estimator_runs_trajectory_extracts_in_parallel():
    analysis = _analysis()
    analysis.trajectories.append(
        Trajectory(
            name="booking_duplicate_second",
            uri="viking://user/u/memories/trajectories/booking_duplicate_second.md",
            content="second trajectory content",
            outcome="success",
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
    analysis = _analysis()
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
@pytest.mark.parametrize("use_resolver", [False, True])
async def test_experience_gradient_estimator_runs_extract_loop(monkeypatch, use_resolver):
    analysis = _analysis()
    analysis.trajectories[0].metadata.update(
        case_uris=["viking://user/u/memories/cases/booking.md"],
        source_archive_uri="viking://user/u/sessions/new/history/archive_001",
        task_query="Update the selected record first.",
    )
    history_uri = "viking://user/u/memories/trajectories/old.md"
    captured = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            captured["provider_kwargs"] = kwargs
            self.history_uris = [history_uri]

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
            return ResolvedOperations(
                upsert_operations=[
                    ResolvedOperation(
                        memory_type="experiences",
                        memory_fields={"experience_name": "booking", "content": "new body"},
                        uris=["viking://user/u/memories/experiences/booking.md"],
                    )
                ],
                delete_file_contents=[],
                errors=[],
            ), {"summary": "ok"}

    monkeypatch.setattr(gradient_estimator_module, "AgentExperienceContextProvider", FakeProvider)
    monkeypatch.setattr(gradient_estimator_module, "MemoryIsolationHandler", FakeIsolationHandler)
    monkeypatch.setattr(gradient_estimator_module, "ExtractLoop", FakeExtractLoop)

    vlm = SimpleNamespace(model="account-model")
    resolver = SimpleNamespace(get_vlm=AsyncMock(return_value=vlm))
    estimator = ExperienceGradientEstimator(
        viking_fs=SimpleNamespace(),
        vlm=None if use_resolver else vlm,
        vlm_resolver=resolver if use_resolver else None,
    )
    context = _context()
    context.request_context.account_id = "account-a"
    context.messages = [Message(id="raw", role="user", parts=[TextPart("RAW_CONTEXT_SENTINEL")])]
    analysis.metadata["rollout_messages"] = [
        Message(id="rollout", role="user", parts=[TextPart("RAW_ROLLOUT_SENTINEL")])
    ]

    gradients = await estimator.estimate(analysis, _experience_set(), context)

    assert len(gradients) == 1
    assert [link.to_uri for link in gradients[0].links] == [
        analysis.trajectories[0].uri,
        history_uri,
    ]
    expected_provider_kwargs = {
        "messages": [],
        "trajectory_summary": analysis.trajectories[0].content,
        "trajectory_uri": analysis.trajectories[0].uri,
        "trajectory_case_uris": analysis.trajectories[0].metadata["case_uris"],
        "trajectory_source_archive_uri": analysis.trajectories[0].metadata["source_archive_uri"],
        "trajectory_outcome": analysis.trajectories[0].outcome,
        "trajectory_task_query": "Update the selected record first.",
        "trajectory_retrieval_anchor": analysis.trajectories[0].retrieval_anchor,
        "trajectory_evaluation": {
            "passed": True,
            "score": 1.0,
            "feedback": [],
            "criterion_results": [
                {
                    "criterion_name": "done",
                    "passed": True,
                    "score": 1.0,
                    "feedback": [],
                    "evidence": ["evidence"],
                    "metadata": {},
                }
            ],
            "metadata": {},
        },
    }
    if use_resolver:
        expected_provider_kwargs["vlm_config"] = vlm
        resolver.get_vlm.assert_awaited_once_with(context.request_context.account_id)
    else:
        resolver.get_vlm.assert_not_awaited()
    assert captured["provider_kwargs"] == expected_provider_kwargs
    assert captured["extract_context"].messages == []
    assert context.messages[0].content == "RAW_CONTEXT_SENTINEL"
    assert analysis.metadata["rollout_messages"][0].content == "RAW_ROLLOUT_SENTINEL"
    assert captured["request_context"] is context.request_context
    assert captured["allowed_memory_types"] == {"experiences"}
    assert captured["prepare_messages_called"] is True
    assert captured["extract_loop_kwargs"]["vlm"] is vlm
    assert captured["extract_loop_kwargs"]["context_provider"]._isolation_handler is not None


@pytest.mark.parametrize("fallback", ["trajectory_extracted", "rollout_evaluation_provided"])
def test_extraction_or_missing_evaluation_fallback_is_not_a_task_score(fallback):
    analysis = _analysis()
    criterion = analysis.evaluation.criterion_results[0]
    criterion.criterion_name = fallback
    if fallback == "trajectory_extracted":
        analysis.evaluation.metadata = {"trajectory_count": 1}
    else:
        criterion.metadata = {"source": "session_commit_default"}

    assert gradient_estimator_module._task_evaluation(analysis) is None


@pytest.mark.parametrize("scope", ["multiple_tasks", "unmatched_case"])
@pytest.mark.asyncio
async def test_rollout_evaluation_is_not_assigned_to_unscoped_tasks(scope):
    analysis = _analysis()
    if scope == "multiple_tasks":
        analysis.trajectories.append(
            Trajectory(
                name="independent_task",
                uri="viking://user/u/memories/trajectories/independent_task.md",
                content="An independent task without a task-specific evaluation.",
                outcome="unknown",
                retrieval_anchor="independent task",
            )
        )
    else:
        analysis.trajectories[0].metadata["case_uris"] = []
    estimator = FakeExperienceGradientEstimator({})

    await estimator.estimate(analysis, _experience_set(), _context())

    assert len(estimator.calls) == len(analysis.trajectories)
    assert all(context.evaluation is None for _, context in estimator.calls)
