# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, patch

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking_cli.session.user_id import UserIdentifier


def test_create_tool_context_uses_extract_context_page_id_map():
    provider = AgentExperienceContextProvider(
        messages=[],
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
        messages=[],
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


@pytest.mark.asyncio
async def test_agent_experience_prefetch_starts_with_conversation_and_new_trajectory_read():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1"),
        role=Role.USER,
    )
    provider._viking_fs = AsyncMock()
    provider._transaction_handle = None
    provider.search_files = AsyncMock(return_value=[])

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        messages = await provider.prefetch()

    assert messages[0]["role"] == "user"
    assert "## Conversation History" in messages[0]["content"]
    assert "After exploring, analyze the conversation" in messages[0]["content"]
    assert add_tool_call_pair.call_count == 1
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["context_role"] == "new_trajectory"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["memory_type"] == "trajectories"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["uri"] == provider.trajectory_uri
    assert messages[-1]["role"] == "user"
    assert "candidate_experience" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_agent_experience_prefetch_includes_structured_read_results():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://user/user_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1"),
        role=Role.USER,
    )
    provider._viking_fs = AsyncMock()
    provider._transaction_handle = None

    provider.search_files = AsyncMock(
        return_value=[
            "viking://user/user_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md"
        ]
    )

    read_result = {
        "experience_name": "personal_experience_sharing_conversation_flow",
        "content": "1 | line one\n2 | line two",
        "page_id": 1,
        "memory_type": "experiences",
    }
    provider.read_file = AsyncMock(return_value=read_result)
    provider._read_file_contents = {
        "viking://user/user_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md": MemoryFile(
            uri="viking://user/user_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md",
            content="line one\nline two",
            memory_type="experiences",
            extra_fields={
                "experience_name": "personal_experience_sharing_conversation_flow",
                "page_id": 1,
                "situation": "- Applies when: the user shares a personal experience.",
                "reminder": "- Acknowledge the experience before changing topics.",
                "procedure": "- Before replying: identify the user's main point.",
                "anti_pattern": "- Do not ignore the shared experience.",
            },
            links=[],
        )
    }

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        messages = await provider.prefetch()

    assert any(msg.get("role") == "user" for msg in messages)
    assert add_tool_call_pair.call_count == 2
    assert (
        add_tool_call_pair.call_args_list[1].kwargs["result"]["context_role"]
        == "candidate_experience"
    )
    assert add_tool_call_pair.call_args_list[1].kwargs["result"]["page_id"] == 1
    candidate = add_tool_call_pair.call_args_list[1].kwargs["result"]
    assert candidate["situation"].startswith("- Applies when:")
    assert "content" not in candidate


@pytest.mark.asyncio
async def test_agent_experience_prefetch_missing_experience_dir_returns_empty_candidates():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="new execution",
        trajectory_uri="viking://user/user_1/memories/trajectories/new_execution.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1"),
        role=Role.USER,
    )
    provider._viking_fs = AsyncMock()
    provider._viking_fs.ls = AsyncMock(
        side_effect=Exception("Directory not found: viking://user/user_1/memories/experiences")
    )
    provider._transaction_handle = None
    provider.search_files = AsyncMock(return_value=[])

    with (
        patch(
            "openviking.session.memory.agent_experience_context_provider.tracer.error"
        ) as tracer_error,
        patch(
            "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
        ) as add_tool_call_pair,
    ):
        messages = await provider.prefetch()

    assert messages[-1]["role"] == "user"
    assert add_tool_call_pair.call_count == 1
    tracer_error.assert_not_called()


@pytest.mark.asyncio
async def test_agent_experience_comparison_prefers_case_linked_success_trajectories():
    case_uri = "viking://user/user_1/memories/cases/tau2_airline_train_5.md"
    current_uri = "viking://user/user_1/memories/trajectories/current_failure.md"
    success_uri = "viking://user/user_1/memories/trajectories/same_case_success.md"
    failure_uri = "viking://user/user_1/memories/trajectories/same_case_failure.md"
    semantic_uri = "viking://user/user_1/memories/trajectories/semantic_only.md"

    def raw(memory_file: MemoryFile) -> str:
        from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

        return MemoryFileUtils.write(memory_file)

    files = {
        case_uri: raw(
            MemoryFile(
                uri=case_uri,
                content="# tau2_airline_train_5",
                memory_type="cases",
                extra_fields={"case_name": "tau2_airline_train_5"},
                links=[
                    {
                        "from_uri": case_uri,
                        "to_uri": failure_uri,
                        "link_type": "related_to",
                        "weight": 1.0,
                    },
                    {
                        "from_uri": case_uri,
                        "to_uri": success_uri,
                        "link_type": "related_to",
                        "weight": 1.0,
                    },
                ],
            )
        ),
        success_uri: raw(
            MemoryFile(
                uri=success_uri,
                content="# success\n- Outcome: success\n- Communication: total 1628",
                memory_type="trajectories",
                extra_fields={"trajectory_name": "success", "outcome": "success"},
            )
        ),
        failure_uri: raw(
            MemoryFile(
                uri=failure_uri,
                content="# failure\n- Outcome: partial\n- Communication: total 708",
                memory_type="trajectories",
                extra_fields={"trajectory_name": "failure", "outcome": "partial"},
            )
        ),
        semantic_uri: raw(
            MemoryFile(
                uri=semantic_uri,
                content="# semantic\n- Outcome: partial",
                memory_type="trajectories",
                extra_fields={"trajectory_name": "semantic", "outcome": "partial"},
            )
        ),
    }

    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="other upcoming total cost failure",
        trajectory_uri=current_uri,
        case_uri=case_uri,
    )
    provider.search_files = AsyncMock(return_value=[semantic_uri])
    viking_fs = AsyncMock()
    viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])
    ctx = RequestContext(user=UserIdentifier(account_id="acc", user_id="user_1"), role=Role.USER)

    results = await provider._search_comparison_trajectories(
        trajectory_dir="viking://user/user_1/memories/trajectories",
        viking_fs=viking_fs,
        ctx=ctx,
    )

    assert [item["uri"] for item in results] == [success_uri]
    assert results[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_agent_experience_comparison_resolves_case_from_trajectory_backlink():
    case_uri = "viking://user/user_1/memories/cases/tau2_airline_train_5.md"
    current_uri = "viking://user/user_1/memories/trajectories/current_failure.md"
    success_uri = "viking://user/user_1/memories/trajectories/same_case_success.md"

    def raw(memory_file: MemoryFile) -> str:
        from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

        return MemoryFileUtils.write(memory_file)

    files = {
        current_uri: raw(
            MemoryFile(
                uri=current_uri,
                content="# current",
                memory_type="trajectories",
                extra_fields={"trajectory_name": "current", "outcome": "partial"},
                backlinks=[
                    {
                        "from_uri": case_uri,
                        "to_uri": current_uri,
                        "link_type": "related_to",
                        "weight": 1.0,
                    }
                ],
            )
        ),
        case_uri: raw(
            MemoryFile(
                uri=case_uri,
                content="# tau2_airline_train_5",
                memory_type="cases",
                extra_fields={"case_name": "tau2_airline_train_5"},
                links=[
                    {
                        "from_uri": case_uri,
                        "to_uri": success_uri,
                        "link_type": "related_to",
                        "weight": 1.0,
                    }
                ],
            )
        ),
        success_uri: raw(
            MemoryFile(
                uri=success_uri,
                content="# success\n- Outcome: success",
                memory_type="trajectories",
                extra_fields={"trajectory_name": "success", "outcome": "success"},
            )
        ),
    }

    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="other upcoming total cost failure",
        trajectory_uri=current_uri,
    )
    provider.search_files = AsyncMock(return_value=[])
    viking_fs = AsyncMock()
    viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])
    ctx = RequestContext(user=UserIdentifier(account_id="acc", user_id="user_1"), role=Role.USER)

    results = await provider._search_comparison_trajectories(
        trajectory_dir="viking://user/user_1/memories/trajectories",
        viking_fs=viking_fs,
        ctx=ctx,
    )

    assert [item["uri"] for item in results] == [success_uri]


@pytest.mark.asyncio
async def test_agent_experience_comparison_does_not_semantic_fallback_without_case_success():
    case_uri = "viking://user/user_1/memories/cases/tau2_airline_train_5.md"
    current_uri = "viking://user/user_1/memories/trajectories/current_failure.md"
    failure_uri = "viking://user/user_1/memories/trajectories/same_case_failure.md"

    def raw(memory_file: MemoryFile) -> str:
        from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

        return MemoryFileUtils.write(memory_file)

    files = {
        case_uri: raw(
            MemoryFile(
                uri=case_uri,
                content="# tau2_airline_train_5",
                memory_type="cases",
                extra_fields={"case_name": "tau2_airline_train_5"},
                links=[
                    {
                        "from_uri": case_uri,
                        "to_uri": failure_uri,
                        "link_type": "related_to",
                        "weight": 1.0,
                    }
                ],
            )
        ),
        failure_uri: raw(
            MemoryFile(
                uri=failure_uri,
                content="# failure\n- Outcome: partial",
                memory_type="trajectories",
                extra_fields={"trajectory_name": "failure", "outcome": "partial"},
            )
        ),
    }

    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="other upcoming total cost failure",
        trajectory_uri=current_uri,
        case_uri=case_uri,
    )
    provider.search_files = AsyncMock(
        return_value=["viking://user/user_1/memories/trajectories/semantic_success.md"]
    )
    viking_fs = AsyncMock()
    viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])
    ctx = RequestContext(user=UserIdentifier(account_id="acc", user_id="user_1"), role=Role.USER)

    results = await provider._search_comparison_trajectories(
        trajectory_dir="viking://user/user_1/memories/trajectories",
        viking_fs=viking_fs,
        ctx=ctx,
    )

    assert results == []
    provider.search_files.assert_not_awaited()
