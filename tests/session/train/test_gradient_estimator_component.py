# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from test_fakes import render_experience_fields

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.train import (
    CriterionResult,
    Experience,
    ExperienceGradientContext,
    ExperienceGradientEstimateRequest,
    ExperienceGradientEstimator,
    ExperienceSet,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.components import gradient_estimator as gradient_estimator_module
from openviking.session.train.gates import GateDecision, GateReport
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    """These isolated component tests do not need the session integration client."""
    yield


class FakeExperienceGradientEstimator(ExperienceGradientEstimator):
    def __init__(self, operations_by_trajectory_uri):
        super().__init__()
        self.operations_by_trajectory_uri = operations_by_trajectory_uri
        self.calls = []

    async def _run_extract_loop(self, trajectory, context):
        self.calls.append((trajectory, context))
        return self.operations_by_trajectory_uri.get(trajectory.uri)


def _duplicate_booking_experience_fields() -> dict[str, str]:
    return {
        "situation": (
            "- Applies when: candidate booking may duplicate an existing retrieved booking.\n"
            "- Does not apply when: no matching existing booking is retrieved.\n"
            "- Source binding: user request and retrieved booking records."
        ),
        "reminder": "- Avoid creating a duplicate booking for the same confirmed trip.",
        "procedure": (
            "- Before booking: compare the candidate trip to retrieved bookings.\n"
            "- If it duplicates an existing booking: do not book and explain.\n"
            "- Else: proceed."
        ),
        "anti_pattern": (
            "- Do not book a duplicate reservation.\n- Preserve genuinely new bookings."
        ),
    }


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
        request_context=RequestContext(UserIdentifier("account", "user"), Role.USER),
    )


def _rejected_gate_report(
    *,
    retriable: bool,
    repair_prompt: str = "",
) -> GateReport:
    return GateReport(
        stage="post_gradient",
        evaluated_count=1,
        rejected_count=1,
        decisions=[
            GateDecision(
                gate_name="test_gate",
                action="reject",
                reason="test rejection",
                evidence={"target_name": "test_experience"},
                retriable=retriable,
                repair_prompt=repair_prompt,
            )
        ],
    )


class RecordingEntryEstimator(ExperienceGradientEstimator):
    def __init__(self, *, error: Exception | None = None):
        super().__init__()
        self.error = error
        self.requests: list[ExperienceGradientEstimateRequest] = []

    async def estimate_trajectory_gradients(self, request):
        self.requests.append(request)
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return [request.trajectory.uri]


@pytest.mark.asyncio
async def test_experience_gradient_estimator_schedules_isolated_per_trajectory_requests():
    analysis = _analysis(passed=False, outcome="failure")
    analysis.trajectories.extend(
        [
            Trajectory(
                name="partial",
                uri="viking://user/u/memories/trajectories/partial.md",
                content="partial",
                outcome="partial",
                retrieval_anchor="Stage: tool",
                metadata={"case_uri": "viking://user/u/memories/cases/case-1.md"},
            ),
            Trajectory(
                name="success",
                uri="viking://user/u/memories/trajectories/success.md",
                content="success",
                outcome="success",
                retrieval_anchor="Stage: final",
            ),
        ]
    )
    context = _context()
    estimator = RecordingEntryEstimator()

    gradients = await estimator.estimate(analysis, _experience_set(), context)

    assert gradients == [analysis.trajectories[0].uri, analysis.trajectories[1].uri]
    assert [request.trajectory for request in estimator.requests] == analysis.trajectories[:2]
    assert all(not hasattr(request, "messages") for request in estimator.requests)
    assert estimator.requests[0] is not estimator.requests[1]
    assert estimator.requests[1].case_uri == "viking://user/u/memories/cases/case-1.md"
    assert "current_analysis" not in context.metadata


@pytest.mark.asyncio
async def test_experience_gradient_estimator_records_deduplicated_loaded_experience_uris():
    analysis = _analysis(passed=False, outcome="failure")
    analysis.metadata["rollout_messages"] = [
        SimpleNamespace(
            content=(
                "<experience_reminder>"
                "<experience_name>case rule</experience_name>"
                "<experience_uri>viking://user/u/memories/experiences/case-rule.md</experience_uri>"
                "</experience_reminder>"
                "<experience_reminder>"
                "<experience_name>case rule duplicate</experience_name>"
                "<experience_uri>viking://user/u/memories/experiences/case-rule.md</experience_uri>"
                "</experience_reminder>"
                "<experience_reminder>"
                "<experience_name>loaded only</experience_name>"
                "<experience_uri>viking://user/u/memories/experiences/loaded-only.md</experience_uri>"
                "</experience_reminder>"
            )
        )
    ]
    estimator = RecordingEntryEstimator()

    await estimator.estimate(analysis, _experience_set(), _context())

    assert estimator.requests[0].loaded_experience_uris == [
        "viking://user/u/memories/experiences/case-rule.md",
        "viking://user/u/memories/experiences/loaded-only.md",
    ]


@pytest.mark.asyncio
async def test_experience_gradient_estimator_keeps_outer_error_policy():
    analysis = _analysis(passed=False, outcome="failure")

    tolerant = RecordingEntryEstimator(error=RuntimeError("extract failed"))
    assert await tolerant.estimate(analysis, _experience_set(), _context()) == []

    strict_context = _context()
    strict_context.strict_extract_errors = True
    strict = RecordingEntryEstimator(error=RuntimeError("extract failed"))
    with pytest.raises(RuntimeError, match="extract failed"):
        await strict.estimate(analysis, _experience_set(), strict_context)


def test_experience_post_validation_decision_handles_pass_retry_and_discard():
    pass_report = GateReport(stage="post_gradient", evaluated_count=1, allowed_count=1)
    retriable_report = _rejected_gate_report(
        retriable=True,
        repair_prompt="repair the experience",
    )
    non_retriable_report = _rejected_gate_report(retriable=False)

    assert (
        gradient_estimator_module._experience_post_validation_decision(
            pass_report,
            retry_count=0,
        )
        is None
    )

    retry = gradient_estimator_module._experience_post_validation_decision(
        retriable_report,
        retry_count=0,
    )
    assert retry is not None
    assert retry.retry is True
    assert retry.discard is False
    assert "repair the experience" in retry.instruction

    non_retriable = gradient_estimator_module._experience_post_validation_decision(
        non_retriable_report,
        retry_count=0,
    )
    assert non_retriable is not None
    assert non_retriable.discard is True

    exhausted = gradient_estimator_module._experience_post_validation_decision(
        retriable_report,
        retry_count=gradient_estimator_module._EXPERIENCE_POST_VALIDATION_MAX_RETRIES,
    )
    assert exhausted is not None
    assert exhausted.discard is True


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
                    **_duplicate_booking_experience_fields(),
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
    assert gradient.after_file.content == render_experience_fields(
        _duplicate_booking_experience_fields()
    )
    assert gradient.after_file.extra_fields["situation"].startswith("- Applies when:")
    assert "constraint" not in gradient.after_file.extra_fields
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


def test_operations_to_gradients_render_custom_template_content_field():
    from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
    from openviking.session.memory.merge_op.base import FieldType

    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(name="experience_name", field_type=FieldType.STRING),
            MemoryField(name="situation", field_type=FieldType.STRING),
            MemoryField(name="evidence", field_type=FieldType.STRING),
        ],
        content_template="## Situation\n{{ situation }}\n\n## Evidence\n{{ evidence }}",
    )
    operation = SimpleNamespace(
        memory_type="experiences",
        memory_fields={
            "experience_name": "scope_binding",
            "situation": "request-time scope",
            "evidence": "retrieved records",
        },
        uris=["viking://user/u/memories/experiences/scope_binding.md"],
        old_memory_file_content=None,
    )
    analysis = _analysis(passed=False, outcome="failure")

    gradients = gradient_estimator_module._operations_to_gradients(
        operations=SimpleNamespace(upsert_operations=[operation]),
        trajectory=analysis.trajectories[0],
        analysis=analysis,
        experience_set=_experience_set(),
        schema=schema,
    )

    assert gradients[0].after_file.content == (
        "## Situation\nrequest-time scope\n\n## Evidence\nretrieved records"
    )
    assert gradients[0].after_file.extra_fields["evidence"] == "retrieved records"


@pytest.mark.asyncio
async def test_experience_gradient_estimator_uses_policy_version_for_newer_old_file_absence():
    analysis = _analysis(passed=False, outcome="failure")
    operations = SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields={
                    **_duplicate_booking_experience_fields(),
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
        request_context=_context().request_context,
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
    captured = {"metadata_seen": [], "semantic_vlms": []}

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
                            "situation": (
                                "- Applies when: a scoped total is requested.\n"
                                "- Does not apply when: no scoped total is requested.\n"
                                "- Source binding: request records."
                            ),
                            "reminder": "- Preserve the requested total scope.",
                            "procedure": "- Calculate and communicate the requested total.",
                            "anti_pattern": "- Do not omit the requested total.",
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
        captured["semantic_vlms"].append(semantic_vlm)
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
    assert captured["semantic_vlms"] == [estimator.vlm]
    assert len(analysis.metadata["gate_reports"]) == 1
    assert analysis.trajectories[0].metadata["comparison_trajectory_uris"] == [comparison[0]["uri"]]


@pytest.mark.asyncio
async def test_post_validation_trace_records_root_cause_rejection(monkeypatch):
    spans = []

    class RecordingSpan:
        def __init__(self, name):
            self.name = name
            self.attributes = {}

        def __enter__(self):
            spans.append(self)
            return self

        def __exit__(self, *args):
            pass

        def set_attribute(self, key, value):
            self.attributes[key] = value

    monkeypatch.setattr(
        gradient_estimator_module.tracer,
        "start_as_current_span",
        classmethod(lambda _cls, name, **_kwargs: RecordingSpan(name)),
    )

    report = GateReport(
        stage="post_gradient",
        evaluated_count=1,
        rejected_count=1,
        decisions=[
            GateDecision(
                gate_name="experience_root_cause_prevention",
                action="reject",
                reason="runtime source binding is missing",
                evidence={
                    "root_cause_quality": "missing_source_binding",
                    "gate_model_reason": "source binding is too broad",
                    "authoritative_behavior_anchor": (
                        '- Required missing action: cancel_reservation({"reservation_id":"MSJ4OA"})'
                    ),
                    "anchored_repair": True,
                },
                retriable=True,
                repair_prompt="bind the reminder to visible tool results",
            )
        ],
    )

    async def reject_gradients(**kwargs):
        return [], report

    monkeypatch.setattr(
        gradient_estimator_module,
        "_evaluate_experience_gradients",
        reject_gradients,
    )

    (
        result,
        traced_report,
    ) = await gradient_estimator_module._evaluate_experience_gradients_with_trace(
        gradients=[],
        analysis=_analysis(passed=False, outcome="failure"),
        experience_set=_experience_set(),
        semantic_vlm=SimpleNamespace(),
        retry_count=2,
    )

    assert result == []
    assert traced_report is report
    assert len(spans) == 1
    assert spans[0].name == "train.gradient_estimator.experience.post_validation"
    assert spans[0].attributes == {
        "gate.retry_count": 2,
        "gate.stage": "post_gradient",
        "gate.evaluated_count": 1,
        "gate.allowed_count": 0,
        "gate.rejected_count": 1,
        "gate.warning_count": 0,
        "gate.outcome": "rejected",
        "gate.decision_count": 1,
        "gate.decision.0.name": "experience_root_cause_prevention",
        "gate.decision.0.action": "reject",
        "gate.decision.0.reason": "runtime source binding is missing",
        "gate.decision.0.retriable": True,
        "gate.decision.0.repair_prompt": "bind the reminder to visible tool results",
        "gate.decision.0.root_cause_quality": "missing_source_binding",
        "gate.decision.0.gate_model_reason": "source binding is too broad",
        "gate.decision.0.authoritative_behavior_anchor": (
            '- Required missing action: cancel_reservation({"reservation_id":"MSJ4OA"})'
        ),
        "gate.decision.0.anchored_repair": True,
    }


@pytest.mark.asyncio
async def test_post_validation_hook_discards_when_gate_evaluation_raises(monkeypatch):
    analysis = _analysis(passed=False, outcome="failure")
    context = _context()
    captured = {}

    class FakeProvider:
        def __init__(self, **kwargs):
            self.prefetched_comparison_trajectories = []

    class FakeIsolationHandler:
        def __init__(self, request_context, extract_context, allowed_memory_types):
            pass

        def prepare_messages(self):
            pass

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            self.post_validation_hook = kwargs["post_validation_hook"]

        async def run(self):
            operations = SimpleNamespace(upsert_operations=[])
            captured["decision"] = await self.post_validation_hook(
                operations,
                0,
                messages=[],
                latest_draft=operations,
            )
            return operations, {"summary": "discarded"}

    async def raise_gate_error(**kwargs):
        raise RuntimeError("gate unavailable")

    monkeypatch.setattr(gradient_estimator_module, "AgentExperienceContextProvider", FakeProvider)
    monkeypatch.setattr(gradient_estimator_module, "MemoryIsolationHandler", FakeIsolationHandler)
    monkeypatch.setattr(gradient_estimator_module, "ExtractLoop", FakeExtractLoop)
    monkeypatch.setattr(
        gradient_estimator_module,
        "_evaluate_experience_gradients",
        raise_gate_error,
    )

    estimator = ExperienceGradientEstimator(viking_fs=SimpleNamespace(), vlm=SimpleNamespace())

    assert await estimator.estimate(analysis, _experience_set(), context) == []
    assert captured["decision"].discard is True
    assert context.metadata["gate_reports"][-1]["rejected_count"] == 1
    assert context.metadata["post_validation_retries"][-1]["final_outcome"] == (
        "discarded_after_gate_error"
    )
