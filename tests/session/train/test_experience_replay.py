# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
    CandidateExperienceEvidence,
    ExperienceEvidenceBundle,
    ExperienceEvidenceQuery,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.train.components import gradient_estimator as gradient_estimator_module
from openviking.session.train.components.gradient_estimator import (
    ExperienceGradientEstimateRequest,
)
from openviking.session.train.domain import PolicySet, RubricEvaluation, Trajectory
from openviking.session.train.gates import GateDecision, GateReport
from openviking.telemetry.replay import EntryRecord, MockRecord, ReplayRunner, encode_value
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    """This isolated replay test does not need the session integration client."""
    yield


def _request() -> ExperienceGradientEstimateRequest:
    return ExperienceGradientEstimateRequest(
        trajectory=Trajectory(
            name="failed-booking",
            uri="viking://user/user/memories/trajectories/failed-booking.md",
            content="duplicate booking trajectory",
            outcome="failure",
            retrieval_anchor="Stage: final",
            metadata={"training_category": "booking"},
        ),
        evaluation=RubricEvaluation(
            passed=False,
            score=0.0,
            feedback=["created a duplicate"],
            metadata={"reward": 0.0},
        ),
        experience_set=PolicySet(
            root_uri="viking://user/user/memories/experiences",
            policies=[],
        ),
        request_context=RequestContext(UserIdentifier("account", "user"), Role.USER),
        case_uri="viking://user/user/memories/cases/case-1.md",
        case_name="case-1",
        task_signature="book-flight",
        loaded_experience_uris=["viking://user/user/memories/experiences/existing.md"],
    )


def _evidence_query(request: ExperienceGradientEstimateRequest) -> ExperienceEvidenceQuery:
    provider = AgentExperienceContextProvider(
        trajectory_summary=request.trajectory.content,
        trajectory_uri=request.trajectory.uri,
        case_uri=request.case_uri,
        case_name=request.case_name,
        task_signature=request.task_signature,
        loaded_experience_uris=request.loaded_experience_uris,
    )
    return ExperienceEvidenceQuery(
        trajectory_summary=request.trajectory.content,
        trajectory_uri=request.trajectory.uri,
        trajectory_dir=provider._render_trajectory_dir(request.request_context),
        case_uri=request.case_uri,
        case_name=request.case_name,
        task_signature=request.task_signature,
        loaded_experience_uris=request.loaded_experience_uris,
    )


def _operations(attempt: int):
    return SimpleNamespace(
        upsert_operations=[
            SimpleNamespace(
                memory_type="experiences",
                memory_fields={
                    "experience_name": "avoid_duplicate_booking",
                    "situation": (
                        "- Applies when: a candidate booking may duplicate an existing booking.\n"
                        "- Does not apply when: no existing booking matches.\n"
                        "- Source binding: retrieved booking records."
                    ),
                    "reminder": f"- Fresh extraction attempt {attempt}.",
                    "procedure": "- Compare the candidate with retrieved bookings before writing.",
                    "anti_pattern": "- Do not create a matching duplicate booking.",
                },
                uris=["viking://user/user/memories/experiences/avoid_duplicate_booking.md"],
                old_memory_file_content=None,
            )
        ],
        delete_file_contents=[],
        errors=[],
    )


@pytest.mark.asyncio
async def test_replay_uses_historical_evidence_but_reruns_extraction_and_gate(
    monkeypatch,
) -> None:
    request = _request()
    evidence_bundle = ExperienceEvidenceBundle(
        candidates=[
            CandidateExperienceEvidence(
                memory_file=MemoryFile(
                    uri="viking://user/user/memories/experiences/existing.md",
                    content="historical candidate body",
                    memory_type="experiences",
                    extra_fields={"experience_name": "existing"},
                )
            )
        ]
    )
    mock_record = MockRecord(
        name="memory.experience.load_evidence",
        match_key=encode_value({"query": _evidence_query(request)}),
        outcome="returned",
        result=encode_value(evidence_bundle),
        invocation_id="mock-1",
    )
    entry_record = EntryRecord(
        name="memory.experience.estimate_gradients",
        module=gradient_estimator_module.__name__,
        arguments=encode_value({"request": request}),
        outcome="returned",
        result=encode_value([]),
        invocation_id="entry-1",
    )

    class FakeVlm:
        def __init__(self):
            self.calls = []

        async def get_completion(self, prompt):
            self.calls.append(prompt)
            return {"prompt": prompt}

    fake_vlm = FakeVlm()
    gate_calls = 0
    evidence_seen = []

    class FakeExtractLoop:
        def __init__(self, **kwargs):
            self.vlm = kwargs["vlm"]
            self.provider = kwargs["context_provider"]
            self.post_validation_hook = kwargs["post_validation_hook"]

        async def run(self):
            prefetched = await self.provider.prefetch()
            evidence_seen.extend(self.provider.prefetched_uris)
            for retry_count in range(2):
                await self.vlm.get_completion(f"extract:{retry_count + 1}")
                operations = _operations(retry_count + 1)
                decision = await self.post_validation_hook(
                    operations,
                    retry_count,
                    messages=prefetched,
                    latest_draft=operations,
                )
                if decision is None:
                    return operations, {"summary": "accepted"}
                assert decision.retry is True
            raise AssertionError("fresh gate never accepted the replayed extraction")

    async def fake_gate(**kwargs):
        nonlocal gate_calls
        gate_calls += 1
        await kwargs["semantic_vlm"].get_completion(f"gate:{gate_calls}")
        gradients = kwargs["gradients"]
        if gate_calls == 1:
            return [], GateReport(
                stage="post_gradient",
                evaluated_count=1,
                rejected_count=1,
                decisions=[
                    GateDecision(
                        gate_name="fresh_gate",
                        action="reject",
                        reason="first current draft needs repair",
                        retriable=True,
                        repair_prompt="rerun current extraction with a narrower reminder",
                    )
                ],
            )
        return gradients, GateReport(
            stage="post_gradient",
            evaluated_count=1,
            allowed_count=1,
        )

    monkeypatch.setattr(gradient_estimator_module, "ExtractLoop", FakeExtractLoop)
    monkeypatch.setattr(
        gradient_estimator_module,
        "_evaluate_experience_gradients",
        fake_gate,
    )
    monkeypatch.setattr(
        gradient_estimator_module,
        "get_openviking_config",
        lambda: SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: fake_vlm),
        ),
    )

    result = await ReplayRunner().run(entry_record, [mock_record])

    assert result.outcome == "returned"
    assert result.exception is None
    assert result.unconsumed_records == []
    assert fake_vlm.calls == ["extract:1", "gate:1", "extract:2", "gate:2"]
    assert gate_calls == 2
    assert evidence_seen == ["viking://user/user/memories/experiences/existing.md"]
    assert len(result.result) == 1
    assert "Fresh extraction attempt 2" in result.result[0].after_file.content
