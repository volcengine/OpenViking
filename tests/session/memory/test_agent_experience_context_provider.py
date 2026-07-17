# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
    CandidateExperienceEvidence,
    ExperienceEvidenceBundle,
    TrajectoryEvidence,
)
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    """These isolated provider tests do not need the session integration client."""
    yield


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1"),
        role=Role.USER,
    )


def _loader(bundle: ExperienceEvidenceBundle):
    return SimpleNamespace(load=AsyncMock(return_value=bundle))


def test_create_tool_context_uses_extract_context_page_id_map():
    provider = AgentExperienceContextProvider(
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md",
    )

    extract_context = provider.get_extract_context()
    extract_context.page_id_map.get_page_id(
        "viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md"
    )

    tool_ctx = provider.create_tool_context()

    assert tool_ctx.page_id_map is extract_context.page_id_map


def test_user_memory_provider_splits_but_trajectory_provider_keeps_messages_whole():
    text = "第一句很长很长很长很长很长很长很长很长很长很长很长。" * 8
    messages = [Message(id="1", role="user", parts=[TextPart(text=text)])]

    user_provider = SessionExtractContextProvider(messages=messages)
    trajectory_provider = AgentTrajectoryContextProvider(messages=messages)

    assert len(user_provider.get_extract_context().messages) > 1
    assert len(trajectory_provider.get_extract_context().messages) == 1
    assert trajectory_provider.get_extract_context().messages[0] is messages[0]


def test_agent_experience_instruction_preserves_coupled_scope_repairs():
    provider = AgentExperienceContextProvider(
        trajectory_summary="scope and communication failure",
        trajectory_uri="viking://user/user_1/memories/trajectories/scope_failure.md",
    )

    instruction = provider.instruction()

    assert "coupled rule" in instruction
    assert (
        "answer the information obligation without expanding the write/action scope" in instruction
    )
    assert "agent-proposed broader plan" in instruction
    assert "State the behavior delta" in instruction
    assert "Do not output `trigger_code`" in instruction
    assert "later modified, canceled, upgraded" in instruction
    assert "`Does not apply when` must describe a task-pattern mismatch" in instruction
    assert "canonical runtime value field" in instruction
    assert 'other", "remaining", "those"' in instruction
    assert "## Situation" in instruction
    assert "- `situation`: only the `## Situation` bullet body" in instruction
    assert "storage template adds the four Markdown headings" in instruction
    assert "Authoritative outcome evidence" in instruction
    assert "smallest conflicting policy interpretation" in instruction
    assert "preserve non-conflicting constraints and object boundaries" in instruction
    assert "Tau2 evaluator authority" not in instruction
    assert "The experience itself must not" in instruction
    assert "mention the evaluator" in instruction
    assert "evaluation metadata, hidden checks" in instruction


def test_experience_schema_action_benefit_rule_requires_authoritative_evidence():
    schema_path = Path(__file__).parents[3] / "openviking/prompts/templates/memory/experiences.yaml"
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    anti_pattern = next(field for field in schema["fields"] if field["name"] == "anti_pattern")
    description = " ".join(anti_pattern["description"].split())

    assert "authoritative outcome evidence" in description
    assert "is not sufficient by itself" in description
    assert "preserve that gate instead of weakening it" in description
    assert "refund ineligibility as cancellation ineligibility" not in description


@pytest.mark.asyncio
async def test_agent_experience_prefetch_starts_with_new_trajectory_without_conversation():
    evidence_loader = _loader(ExperienceEvidenceBundle())
    provider = AgentExperienceContextProvider(
        trajectory_summary="用户要求取消预订，但执行失败。",
        trajectory_uri="viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md",
        evidence_loader=evidence_loader,
    )
    provider._ctx = _ctx()

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        messages = await provider.prefetch()

    assert provider.get_output_language() == "中文"
    assert all("Conversation History" not in message.get("content", "") for message in messages)
    assert add_tool_call_pair.call_count == 1
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["context_role"] == "new_trajectory"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["memory_type"] == "trajectories"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["uri"] == provider.trajectory_uri
    assert messages[-1]["role"] == "user"
    assert "candidate_experience" in messages[-1]["content"]
    evidence_loader.load.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_experience_prefetch_renders_candidate_without_content_and_populates_reads():
    experience_uri = (
        "viking://user/user_sample_9/memories/experiences/"
        "personal_experience_sharing_conversation_flow.md"
    )
    candidate_file = MemoryFile(
        uri=experience_uri,
        content="line one\nline two",
        memory_type="experiences",
        extra_fields={
            "experience_name": "personal_experience_sharing_conversation_flow",
            "situation": "- Applies when: the user shares a personal experience.",
            "reminder": "- Acknowledge the experience before changing topics.",
            "procedure": "- Before replying: identify the user's main point.",
            "anti_pattern": "- Do not ignore the shared experience.",
        },
    )
    provider = AgentExperienceContextProvider(
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://user/user_sample_9/memories/trajectories/current.md",
        evidence_loader=_loader(
            ExperienceEvidenceBundle(
                candidates=[CandidateExperienceEvidence(memory_file=candidate_file)]
            )
        ),
    )
    provider._ctx = _ctx()

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        await provider.prefetch()

    candidate = add_tool_call_pair.call_args_list[1].kwargs["result"]
    assert candidate["context_role"] == "candidate_experience"
    assert candidate["page_id"] == 1
    assert candidate["situation"].startswith("- Applies when:")
    assert "content" not in candidate
    assert provider.read_file_contents[experience_uri] == candidate_file


@pytest.mark.asyncio
async def test_agent_experience_prefetch_injects_only_two_newest_comparisons():
    comparison_uris = [
        f"viking://user/user_1/memories/trajectories/success_{index}.md"
        for index in range(5, 0, -1)
    ]
    bundle = ExperienceEvidenceBundle(
        comparison_trajectories=[
            TrajectoryEvidence(
                MemoryFile(
                    uri=uri,
                    content=f"# success {index}",
                    memory_type="trajectories",
                    extra_fields={"outcome": "success"},
                )
            )
            for index, uri in enumerate(comparison_uris)
        ]
    )
    provider = AgentExperienceContextProvider(
        trajectory_summary="failed execution",
        trajectory_uri="viking://user/user_1/memories/trajectories/current_failure.md",
        evidence_loader=_loader(bundle),
    )
    provider._ctx = _ctx()

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        await provider.prefetch()

    comparison_results = [
        call.kwargs["result"]
        for call in add_tool_call_pair.call_args_list
        if call.kwargs["result"]["context_role"] == "comparison_trajectory"
    ]
    assert [item["uri"] for item in comparison_results] == comparison_uris[:2]
    assert [item["uri"] for item in provider.prefetched_comparison_trajectories] == (
        comparison_uris[:2]
    )
