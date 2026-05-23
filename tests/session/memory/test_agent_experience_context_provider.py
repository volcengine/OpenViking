# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking.session.memory.batch_agent_experience_context_provider import (
    SOURCE_TRAJECTORY_IDS_FIELD,
    BatchAgentExperienceContextProvider,
)
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking_cli.session.user_id import UserIdentifier


def test_create_tool_context_uses_extract_context_page_id_map():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )

    extract_context = provider.get_extract_context()
    extract_context.page_id_map.get_page_id(
        "viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md"
    )

    tool_ctx = provider.create_tool_context()

    assert tool_ctx.page_id_map is extract_context.page_id_map


@pytest.mark.asyncio
async def test_agent_experience_prefetch_starts_with_conversation_and_new_trajectory_read():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(),
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
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(),
    )
    provider._viking_fs = AsyncMock()
    provider._transaction_handle = None

    provider.search_files = AsyncMock(
        return_value=[
            "viking://agent/agent_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md"
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
        "viking://agent/agent_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md": SimpleNamespace(
            extra_fields={"experience_name": "personal_experience_sharing_conversation_flow"},
            content="line one\nline two",
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


@pytest.mark.asyncio
async def test_batch_agent_experience_prefetch_includes_multiple_new_trajectories():
    provider = BatchAgentExperienceContextProvider(
        messages=[],
        trajectory_items=[
            {
                "uri": "viking://agent/agent_sample_9/memories/trajectories/first.md",
                "content": "first trajectory",
            },
            {
                "uri": "viking://agent/agent_sample_9/memories/trajectories/second.md",
                "content": "second trajectory",
            },
        ],
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(),
    )
    provider._viking_fs = AsyncMock()
    provider._transaction_handle = None
    provider.search_files = AsyncMock(return_value=[])

    with patch(
        "openviking.session.memory.batch_agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        messages = await provider.prefetch()

    assert add_tool_call_pair.call_count == 2
    assert add_tool_call_pair.call_args_list[0].kwargs["call_id"] == "new-trajectory-0"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["uri"].endswith("/first.md")
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["source_trajectory_id"] == "T1"
    assert add_tool_call_pair.call_args_list[1].kwargs["call_id"] == "new-trajectory-1"
    assert add_tool_call_pair.call_args_list[1].kwargs["result"]["uri"].endswith("/second.md")
    assert add_tool_call_pair.call_args_list[1].kwargs["result"]["source_trajectory_id"] == "T2"
    assert "multiple `new_trajectory`" in messages[-1]["content"]


def test_batch_agent_experience_schema_adds_temporary_source_ids_field():
    provider = BatchAgentExperienceContextProvider(
        messages=[],
        trajectory_items=[
            {
                "uri": "viking://agent/agent_sample_9/memories/trajectories/first.md",
                "content": "first trajectory",
            }
        ],
    )

    schemas = provider.get_memory_schemas(
        RequestContext(
            user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
            role=Role.USER,
            namespace_policy=AccountNamespacePolicy(),
        )
    )

    assert any(field.name == SOURCE_TRAJECTORY_IDS_FIELD for field in schemas[0].fields)


def test_batch_agent_experience_instruction_derives_from_single_prompt():
    provider = BatchAgentExperienceContextProvider(
        messages=[],
        trajectory_items=[
            {
                "uri": "viking://agent/agent_sample_9/memories/trajectories/first.md",
                "content": "first trajectory",
            },
            {
                "uri": "viking://agent/agent_sample_9/memories/trajectories/second.md",
                "content": "second trajectory",
            },
        ],
    )

    instruction = provider.instruction()

    assert "Multiple new trajectories from the latest committed session" in instruction
    assert "`source_trajectory_ids`" in instruction
    assert "Precise source attribution" in instruction
    assert "Do not split only for attribution" in instruction
    assert "One experience may cite multiple `source_trajectory_ids`" in instruction
    assert "Preserve action-boundary differences" in instruction
    assert "legal action boundary" in instruction
    assert "write target/provenance" in instruction
    assert "continuation policy" in instruction
    assert "For each distinct user intent across the new trajectories" in instruction
    assert "one entry per intent" in instruction
    assert "Split over merge" in instruction
    assert "Only incorporate relevant trajectories" not in instruction


def test_batch_agent_experience_resolves_and_removes_source_attribution():
    provider = BatchAgentExperienceContextProvider(
        messages=[],
        trajectory_items=[
            {
                "uri": "viking://agent/agent_sample_9/memories/trajectories/first.md",
                "content": "first trajectory",
            },
            {
                "uri": "viking://agent/agent_sample_9/memories/trajectories/second.md",
                "content": "second trajectory",
            },
        ],
    )
    op = ResolvedOperation(
        memory_type="experiences",
        uris=["viking://agent/agent_sample_9/memories/experiences/debug.md"],
        memory_fields={
            "experience_name": "debug",
            SOURCE_TRAJECTORY_IDS_FIELD: "T1,T2",
        },
    )
    operations = ResolvedOperations(upsert_operations=[op], delete_file_contents=[], errors=[])

    attribution = provider.resolve_source_attribution(operations)

    assert attribution == {
        "viking://agent/agent_sample_9/memories/experiences/debug.md": [
            "viking://agent/agent_sample_9/memories/trajectories/first.md",
            "viking://agent/agent_sample_9/memories/trajectories/second.md",
        ]
    }
    assert SOURCE_TRAJECTORY_IDS_FIELD not in op.memory_fields
