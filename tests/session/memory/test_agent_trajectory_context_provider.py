# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart, ToolPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def execution_messages():
    messages = [
        Message(id="request", role="user", parts=[TextPart("Update reservation ABC123.")]),
        Message(
            id="read",
            role="assistant",
            parts=[
                TextPart("Checking the reservation."),
                ToolPart(
                    tool_id="read-1",
                    tool_name="get_reservation_details",
                    tool_input={"reservation_id": "ABC123"},
                    tool_output="x" * 600 + "\nfare_difference=209; free_baggage=3",
                    tool_status="completed",
                ),
            ],
        ),
        Message(id="confirm", role="user", parts=[TextPart("I confirm the $209 change.")]),
        Message(
            id="write",
            role="assistant",
            parts=[
                ToolPart(
                    tool_id="write-1",
                    tool_name="update_reservation",
                    tool_input={"reservation_id": "ABC123", "amount": 209},
                    tool_output="Payment declined; reservation unchanged.",
                    tool_status="error",
                ),
                TextPart("The reservation was not changed."),
            ],
        ),
    ]
    messages[1].reasoning_content = "PRIVATE_REASONING_NOT_FOR_EXTRACTION"
    return messages


@pytest.mark.asyncio
@pytest.mark.parametrize("include_session_skills", [False, True])
async def test_trajectory_prefetch_preserves_complete_ordered_tool_evidence(
    execution_messages, include_session_skills
):
    fs = AsyncMock()
    fs.ls.return_value = [
        {
            "name": "reservation-review",
            "uri": "viking://user/default/skills/reservation-review",
            "isDir": True,
            "abstract": "Review reservations before changing them.",
        }
    ]
    provider = AgentTrajectoryContextProvider(
        messages=execution_messages,
        include_session_skills=include_session_skills,
        ctx=RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT),
        viking_fs=fs,
    )

    prefetched = await provider.prefetch()

    history = prefetched[0]["content"]
    read_output = execution_messages[1].parts[1].tool_output
    assert read_output in history
    assert "input={'reservation_id': 'ABC123'}" in history
    assert "input={'reservation_id': 'ABC123', 'amount': 209}" in history
    assert "tool_name=get_reservation_details; status=completed" in history
    assert "tool_name=update_reservation; status=error" in history
    ordered_evidence = [
        "Update reservation ABC123.",
        "Checking the reservation.",
        "tool_name=get_reservation_details",
        read_output,
        "I confirm the $209 change.",
        "tool_name=update_reservation",
        "Payment declined; reservation unchanged.",
        "The reservation was not changed.",
    ]
    positions = [history.index(item) for item in ordered_evidence]
    assert positions == sorted(positions)
    all_content = "\n".join(message.get("content", "") for message in prefetched)
    assert all_content.count("## Conversation History") == 1
    assert all_content.count(read_output) == 1
    assert "PRIVATE_REASONING_NOT_FOR_EXTRACTION" not in all_content
    if include_session_skills:
        assert fs.ls.await_count == 2
        assert "reservation-review/SKILL.md" in all_content
    else:
        fs.ls.assert_not_awaited()
        assert len(prefetched) == 1
