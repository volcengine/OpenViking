# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.message import Message, TextPart, ToolPart
from openviking.usage_reporter import (
    MemoryUsageExtractor,
    UsageContext,
)


def _context() -> UsageContext:
    return UsageContext(
        account_id="new",
        user_id="test",
        session_id="session-1",
        archive_uri="viking://user/test/sessions/session-1/history/archive_001",
        task_id="task-1",
    )


@pytest.mark.asyncio
async def test_memory_usage_extractor_emits_recall_and_injection_events():
    experience_uri = "viking://user/test/memories/experiences/no-order-exchange.md"
    messages = [
        Message(
            id="msg-1",
            role="user",
            parts=[
                TextPart("我要处理无订单号换货"),
                ToolPart(
                    tool_id="call-search",
                    tool_name="search_experience",
                    tool_status="completed",
                    tool_input={"query": "无订单号换货"},
                    tool_output={"results": [{"uri": experience_uri}, {"uri": "viking://other"}]},
                ),
            ],
        ),
        Message(
            id="msg-2",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name="read_experience",
                    tool_status="completed",
                    tool_input={"uri": experience_uri},
                    tool_output="## Situation\n用户未提供订单号但要求换货。",
                )
            ],
        ),
        Message(
            id="msg-3",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-pending",
                    tool_name="read_experience",
                    tool_status="running",
                    tool_input={"uri": experience_uri},
                )
            ],
        ),
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.event_type for event in events] == ["memory.recalled", "memory.injected"]
    assert [event.resource_uri for event in events] == [experience_uri, experience_uri]
    assert [event.resource_type for event in events] == ["experience", "experience"]
    assert events[0].evidence == {
        "archive_uri": _context().archive_uri,
        "message_id": "msg-1",
        "tool_call_id": "call-search",
        "tool_name": "search_experience",
    }
    assert events[1].evidence["tool_name"] == "read_experience"


@pytest.mark.asyncio
async def test_memory_usage_extractor_uses_message_time_for_event_time():
    experience_uri = "viking://user/test/memories/experiences/no-order-exchange.md"
    messages = [
        Message(
            id="msg-1",
            role="user",
            created_at="2026-07-10T20:30:40.123456+08:00",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name="read_experience",
                    tool_status="completed",
                    tool_input={"uri": experience_uri},
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events[0].occurred_at == "2026-07-10T12:30:40.123Z"


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_non_experience_memory_uris():
    messages = [
        Message(
            id="msg-1",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name="search_experience",
                    tool_status="completed",
                    tool_output={
                        "results": [
                            {"uri": "viking://user/default/memories/trajectories/a.md"},
                            {"uri": "viking://user/default/memories/preferences/a.md"},
                        ]
                    },
                ),
                ToolPart(
                    tool_id="call-read",
                    tool_name="read_experience",
                    tool_status="completed",
                    tool_input={"uri": "viking://user/default/memories/trajectories/a.md"},
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events == []


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_other_users_experience_uris():
    own_uri = "viking://user/test/memories/experiences/own.md"
    other_uri = "viking://user/other/memories/experiences/other.md"
    messages = [
        Message(
            id="msg-1",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name="search_experience",
                    tool_status="completed",
                    tool_output={"results": [{"uri": own_uri}, {"uri": other_uri}]},
                ),
                ToolPart(
                    tool_id="call-read",
                    tool_name="read_experience",
                    tool_status="completed",
                    tool_input={"uri": other_uri},
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [own_uri]
