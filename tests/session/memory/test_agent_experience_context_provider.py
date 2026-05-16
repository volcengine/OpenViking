# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.session.memory.agent_experience_context_provider import AgentExperienceContextProvider
from openviking.session.memory.page_id_map import PageIdMap
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_agent_experience_prefetch_starts_with_session_conversation_message():
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
    provider.set_page_id_map(PageIdMap())
    provider.search_files = AsyncMock(return_value=[])

    messages = await provider.prefetch()

    assert messages[0]["role"] == "user"
    assert "## Conversation History" in messages[0]["content"]
    assert "After exploring, analyze the conversation" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "## New Trajectory" in messages[1]["content"]


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
    provider.set_page_id_map(PageIdMap())

    provider.search_files = AsyncMock(
        return_value=["viking://agent/agent_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md"]
    )

    read_result = {
        "experience_name": "personal_experience_sharing_conversation_flow",
        "content": "1 | line one\n2 | line two",
        "page_id": 1,
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
    assert add_tool_call_pair.called
