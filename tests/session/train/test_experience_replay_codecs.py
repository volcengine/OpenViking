# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

import pytest

from openviking.message import Message
from openviking.message.part import ContextPart, ImagePart, TextPart, ToolPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.merge_op.base import SearchReplaceBlock, StrPatch

# Importing this module is the explicit registration point for experience replay codecs.
from openviking.session.train.components import experience_replay_codecs  # noqa: F401, E402
from openviking.session.train.components.gradient_estimator import (
    ExperienceGradientEstimateRequest,
)
from openviking.session.train.domain import (
    CriterionResult,
    Policy,
    PolicySet,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.gradients import PatchSemanticGradient
from openviking.telemetry.replay import ReplayCodecError, decode_value, encode_value
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    """These isolated codec tests do not need the session integration client."""
    yield


def _round_trip(value):
    return decode_value(encode_value(value))


def test_message_codec_preserves_all_part_variants() -> None:
    message = Message(
        id="message-1",
        role="assistant",
        peer_id="peer-1",
        created_at="2026-07-17T08:00:00Z",
        parts=[
            TextPart(text="hello"),
            ContextPart(uri="viking://memory/1", context_type="memory", abstract="context"),
            ImagePart(url="https://example.test/image.png", detail="high"),
            ToolPart(
                tool_id="tool-1",
                tool_name="lookup",
                tool_uri="viking://tool/1",
                skill_uri="viking://skill/1",
                tool_input={"nested": [1, {"ok": True}]},
                tool_output="result",
                tool_status="completed",
                duration_ms=12.5,
                prompt_tokens=4,
                completion_tokens=7,
                tool_output_ref="ref",
                tool_output_truncated=True,
                tool_output_original_chars=100,
                tool_output_preview_chars=20,
                tool_output_sha256="abc",
                tool_output_storage_uri="viking://storage/1",
                tool_output_mime_type="application/json",
                tool_output_source_ref="source",
                tool_output_source_offset=2,
                tool_output_source_limit=10,
                tool_output_externalization_error="none",
                tool_output_group_id="group",
                tool_output_externalized_reason="size",
                tool_output_group_original_chars=200,
                tool_output_group_budget_chars=80,
            ),
        ],
    )

    assert _round_trip(message) == message


def test_request_context_codec_preserves_custom_role_and_identity() -> None:
    context = RequestContext(
        user=UserIdentifier(account_id="account", user_id="user"),
        role=Role("reviewer"),
        actor_peer_id="peer-1",
        legacy_agent_id="legacy-1",
        from_oauth=True,
    )

    decoded = _round_trip(context)

    assert decoded == context
    assert type(decoded.role) is Role


def test_trajectory_and_evaluation_codecs_preserve_complete_metadata() -> None:
    trajectory = Trajectory(
        name="failed-case",
        uri="viking://user/u/memories/trajectories/failed-case.md",
        content="trajectory body",
        outcome="failure",
        retrieval_anchor="Stage: final",
        metadata={"nested": [1, {"reward": 0.25}], "nullable": None},
    )
    evaluation = RubricEvaluation(
        passed=False,
        score=0.25,
        criterion_results=[
            CriterionResult(
                criterion_name="booked",
                passed=False,
                score=0.25,
                feedback=["duplicate booking"],
                evidence=["tool call 3"],
                metadata={"judge": {"confidence": 0.9}},
            )
        ],
        metadata={"reward": 0.25, "source": "tau2", "raw": {"complete": True}},
    )

    assert _round_trip(trajectory) == trajectory
    assert _round_trip(evaluation) == evaluation


def test_policy_set_codec_excludes_only_runtime_dependencies() -> None:
    policy_set = PolicySet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Policy(
                name="avoid_duplicate",
                uri="viking://user/u/memories/experiences/avoid_duplicate.md",
                version=3,
                status="production",
                content="content",
                metadata={"nested": {"enabled": True}},
                links=[{"to_uri": "viking://case/1", "weight": 0.8}],
                backlinks=[{"from_uri": "viking://case/2"}],
            )
        ],
        metadata={"snapshot": {"id": "s1"}},
        viking_fs=object(),
        request_context=object(),
    )

    decoded = _round_trip(policy_set)

    assert decoded.root_uri == policy_set.root_uri
    assert decoded.policies == policy_set.policies
    assert decoded.metadata == policy_set.metadata
    assert decoded.viking_fs is None
    assert decoded.request_context is None


def test_memory_file_link_and_gradient_codecs_round_trip() -> None:
    before_file = MemoryFile(
        uri="viking://user/u/memories/experiences/avoid_duplicate.md",
        content="before",
        links=[{"to_uri": "viking://case/1", "metadata": {"rank": 1}}],
        backlinks=[{"from_uri": "viking://case/2"}],
        memory_type="experiences",
        extra_fields={"version": 4, "nested": ["a", {"b": False}]},
    )
    after_file = before_file.model_copy(update={"content": "after"})
    link = StoredLink(
        from_uri=before_file.uri,
        to_uri="viking://case/1",
        link_type="related",
        weight=0.75,
        match_text="booking",
        description="support",
        created_at="2026-07-17T08:00:00Z",
    )
    gradient = PatchSemanticGradient(
        before_file=before_file,
        after_file=after_file,
        base_version=4,
        rationale="avoid duplicate",
        links=[link],
        confidence=0.9,
        metadata={"gate": {"allowed": True}, "attempts": [1, 2]},
    )

    assert _round_trip(before_file) == before_file
    assert _round_trip(link) == link
    assert _round_trip(gradient) == gradient


def test_gradient_codec_preserves_unregistered_pydantic_metadata() -> None:
    after_file = MemoryFile(
        uri="viking://user/u/memories/experiences/cancel_eligible.md",
        content="after",
        memory_type="experiences",
    )
    patch = StrPatch(
        blocks=[
            SearchReplaceBlock(
                search="Do not cancel without a refund.",
                replace="Treat cancellation eligibility separately from refund eligibility.",
            )
        ]
    )
    gradient = PatchSemanticGradient(
        before_file=None,
        after_file=after_file,
        base_version=None,
        rationale="correct cancellation behavior",
        links=[],
        confidence=0.9,
        metadata={"resolved_operation": {"content": patch}},
    )

    decoded = _round_trip(gradient)

    assert decoded == gradient
    assert isinstance(decoded.metadata["resolved_operation"]["content"], StrPatch)


def test_nested_domain_metadata_rejects_unknown_runtime_objects() -> None:
    trajectory = Trajectory(
        name="bad",
        uri="viking://trajectory/bad",
        content="bad",
        outcome="failure",
        retrieval_anchor="bad",
        metadata={"runtime": object()},
    )

    with pytest.raises(ReplayCodecError, match="No replay codec"):
        encode_value(trajectory)


def test_experience_gradient_request_codec_round_trips_complete_entry_input() -> None:
    request = ExperienceGradientEstimateRequest(
        trajectory=Trajectory(
            name="failure",
            uri="viking://user/u/memories/trajectories/failure.md",
            content="trajectory",
            outcome="failure",
            retrieval_anchor="Stage: final",
            metadata={"training_category": "booking"},
        ),
        messages=[
            Message(
                id="m1",
                role="user",
                parts=[TextPart(text="book a flight")],
                created_at="2026-07-17T08:00:00Z",
            )
        ],
        evaluation=RubricEvaluation(
            passed=False,
            score=0.0,
            feedback=["duplicate"],
            metadata={"reward": 0},
        ),
        experience_set=PolicySet(
            root_uri="viking://user/u/memories/experiences",
            policies=[],
            metadata={"snapshot": "s1"},
        ),
        request_context=RequestContext(
            UserIdentifier("account", "user"),
            Role.USER,
        ),
        case_uri="viking://user/u/memories/cases/case-1.md",
        case_name="case-1",
        task_signature="book-flight",
        diagnostics={"attempt": 0},
    )

    assert _round_trip(request) == request
