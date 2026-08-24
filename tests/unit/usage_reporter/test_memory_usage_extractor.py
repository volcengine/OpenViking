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
                    tool_name="find",
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
                    tool_name="read",
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
                    tool_name="read",
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
        "tool_name": "find",
    }
    assert events[1].evidence["tool_name"] == "read"


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_removed_dedicated_experience_tools():
    experience_uri = "viking://user/test/memories/experiences/legacy.md"
    messages = [
        Message(
            id="msg-legacy",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name="search_experience",
                    tool_status="completed",
                    tool_output={"results": [{"uri": experience_uri}]},
                ),
                ToolPart(
                    tool_id="call-read",
                    tool_name="read_experience",
                    tool_status="completed",
                    tool_input={"uri": experience_uri},
                ),
            ],
        )
    ]

    assert await MemoryUsageExtractor().extract(messages=messages, context=_context()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    [
        "find",
        "search",
        "openviking_find",
        "openviking_search",
        "ov_search",
        "mcp__openviking__find",
        "mcp__openviking__search",
    ],
)
async def test_memory_usage_extractor_recognizes_generic_search_tools(tool_name):
    experience_uris = [
        f"viking://user/test/memories/experiences/experience-{index}.md" for index in range(10)
    ]
    messages = [
        Message(
            id="msg-search",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name=tool_name,
                    tool_status="completed",
                    tool_output={
                        "details": {
                            "memories": [
                                *[{"uri": uri} for uri in experience_uris],
                                {"uri": "viking://user/test/memories/preferences/preference.md"},
                                {"uri": "viking://user/test/memories/cases/case.md"},
                            ],
                            "resources": [{"uri": "viking://resources/guide.md"}],
                        }
                    },
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert len(events) == 10
    assert [event.event_type for event in events] == ["memory.recalled"] * 10
    assert [event.resource_uri for event in events] == experience_uris


@pytest.mark.asyncio
async def test_memory_usage_extractor_does_not_count_search_scope_as_result():
    experience_uri = "viking://user/test/memories/experiences/search-scope.md"
    messages = [
        Message(
            id="msg-empty-search",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-empty-search",
                    tool_name="ov_search",
                    tool_status="completed",
                    tool_input={"query": "missing", "uri": experience_uri},
                    tool_output={
                        "details": {
                            "action": "searched",
                            "uri": experience_uri,
                            "memories": [],
                            "resources": [],
                            "skills": [],
                            "total": 0,
                        }
                    },
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name", ["list", "openviking_list", "ov_list", "mcp__openviking__list"]
)
async def test_memory_usage_extractor_list_counts_only_experience_files(tool_name):
    experience_uri = "viking://user/test/memories/experiences/listed.md"
    messages = [
        Message(
            id="msg-list",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-list",
                    tool_name=tool_name,
                    tool_status="completed",
                    tool_input={"uri": "viking://user/test/memories/experiences/"},
                    tool_output={
                        "details": {
                            "entries": [
                                {"uri": experience_uri, "isDir": False},
                                {
                                    "uri": "viking://user/test/memories/experiences/nested",
                                    "isDir": True,
                                },
                                {
                                    "uri": "viking://user/test/memories/preferences/preference.md",
                                    "isDir": False,
                                },
                            ]
                        }
                    },
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.event_type for event in events] == ["memory.recalled"]
    assert [event.resource_uri for event in events] == [experience_uri]


@pytest.mark.asyncio
async def test_memory_usage_extractor_parses_mcp_text_search_and_list_results():
    searched_uri = "viking://user/test/memories/experiences/searched.md"
    listed_uri = "viking://user/test/memories/experiences/listed.md"
    messages = [
        Message(
            id="msg-mcp-text",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-find",
                    tool_name="mcp__openviking__find",
                    tool_status="completed",
                    tool_output=(
                        "Found 2 item(s):\n\n"
                        f"- [memory 90%] {searched_uri}\n    matching experience\n"
                        "- [memory 80%] viking://user/test/memories/preferences/a.md\n"
                    ),
                ),
                ToolPart(
                    tool_id="call-list",
                    tool_name="mcp__openviking__list",
                    tool_status="completed",
                    tool_input={"uri": "viking://user/test/memories/experiences/"},
                    tool_output="[file] listed.md\n[dir] nested\n[file] .overview.md",
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [searched_uri, listed_uri]


@pytest.mark.asyncio
async def test_memory_usage_extractor_preserves_spaces_in_openclaw_table_uri():
    experience_uri = "viking://user/test/memories/experiences/order exchange.md"
    messages = [
        Message(
            id="msg-openclaw-table",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name="ov_search",
                    tool_status="completed",
                    tool_output=(
                        "no  type    uri                                                        "
                        "level  score  abstract\n"
                        f"1   memory  {experience_uri}  2      0.91   exchange procedure"
                    ),
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [experience_uri]


@pytest.mark.asyncio
async def test_memory_usage_extractor_canonicalizes_short_user_list_uri():
    messages = [
        Message(
            id="msg-short-list",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-list",
                    tool_name="mcp__openviking__list",
                    tool_status="completed",
                    tool_input={"uri": "viking://user/memories/experiences"},
                    tool_output="[file] listed experience.md",
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [
        "viking://user/test/memories/experiences/listed experience.md"
    ]


@pytest.mark.asyncio
async def test_memory_usage_extractor_canonicalizes_home_alias_list_uri():
    messages = [
        Message(
            id="msg-home-alias-list",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-list",
                    tool_name="mcp__openviking__list",
                    tool_status="completed",
                    tool_input={"uri": "viking://~/memories/experiences"},
                    tool_output="[file] listed experience.md",
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [
        "viking://user/test/memories/experiences/listed experience.md"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("read", {"uris": "viking://user/test/memories/experiences/read.md"}),
        ("openviking_read", {"uris": "viking://user/test/memories/experiences/read.md"}),
        ("ov_read", {"uri": "viking://user/test/memories/experiences/read.md"}),
        (
            "mcp__openviking__read",
            {"uris": "viking://user/test/memories/experiences/read.md"},
        ),
    ],
)
async def test_memory_usage_extractor_recognizes_generic_read_tools(tool_name, tool_input):
    messages = [
        Message(
            id="msg-read",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name=tool_name,
                    tool_status="completed",
                    tool_input=tool_input,
                    tool_output="experience content",
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.event_type for event in events] == ["memory.injected"]
    assert [event.resource_uri for event in events] == [
        "viking://user/test/memories/experiences/read.md"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    [
        "multi_read",
        "openviking_multi_read",
        "ov_multi_read",
        "mcp__openviking__multi_read",
    ],
)
async def test_memory_usage_extractor_multi_read_counts_only_successful_experiences(tool_name):
    success_uri = "viking://user/test/memories/experiences/success.md"
    failed_uri = "viking://user/test/memories/experiences/failed.md"
    preference_uri = "viking://user/test/memories/preferences/preference.md"
    messages = [
        Message(
            id="msg-multi-read",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-multi-read",
                    tool_name=tool_name,
                    tool_status="completed",
                    tool_input={"uris": [success_uri, failed_uri, preference_uri]},
                    tool_output={
                        "details": {
                            "results": [
                                {"uri": success_uri, "success": True},
                                {"uri": failed_uri, "success": False},
                                {"uri": preference_uri, "success": True},
                            ]
                        }
                    },
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.event_type for event in events] == ["memory.injected"]
    assert [event.resource_uri for event in events] == [success_uri]


@pytest.mark.asyncio
async def test_memory_usage_extractor_does_not_fuzzy_match_other_mcp_tools():
    experience_uri = "viking://user/test/memories/experiences/not-openviking.md"
    messages = [
        Message(
            id="msg-other-mcp",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name="mcp__other__search",
                    tool_status="completed",
                    tool_output={"results": [{"uri": experience_uri}]},
                ),
                ToolPart(
                    tool_id="call-read",
                    tool_name="mcp__other__read",
                    tool_status="completed",
                    tool_input={"uri": experience_uri},
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events == []


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_generic_read_not_found_result():
    experience_uri = "viking://user/test/memories/experiences/missing.md"
    messages = [
        Message(
            id="msg-missing-read",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name="mcp__openviking__read",
                    tool_status="completed",
                    tool_input={"uris": experience_uri},
                    tool_output=f"(nothing found at {experience_uri})",
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events == []


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_short_user_read_not_found_result():
    short_uri = "viking://user/memories/experiences/missing.md"
    messages = [
        Message(
            id="msg-missing-short-read",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name="mcp__openviking__read",
                    tool_status="completed",
                    tool_input={"uris": short_uri},
                    tool_output=f"(nothing found at {short_uri})",
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events == []


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_home_alias_read_not_found_result():
    alias_uri = "viking://~/memories/experiences/missing.md"
    messages = [
        Message(
            id="msg-missing-home-alias-read",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name="mcp__openviking__read",
                    tool_status="completed",
                    tool_input={"uris": alias_uri},
                    tool_output=f"(nothing found at {alias_uri})",
                )
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events == []


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
                    tool_name="read",
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
                    tool_name="find",
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
                    tool_name="read",
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
                    tool_name="find",
                    tool_status="completed",
                    tool_output={"results": [{"uri": own_uri}, {"uri": other_uri}]},
                ),
                ToolPart(
                    tool_id="call-read",
                    tool_name="read",
                    tool_status="completed",
                    tool_input={"uri": other_uri},
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [own_uri]


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_noncanonical_experience_uris():
    canonical_uri = "viking://user/test/memories/experiences/own.md"
    query_alias = f"{canonical_uri}?source=codex"
    fragment_alias = f"{canonical_uri}#approach"
    messages = [
        Message(
            id="msg-1",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name="find",
                    tool_status="completed",
                    tool_output={
                        "results": [
                            {"uri": canonical_uri},
                            {"uri": query_alias},
                            {"uri": fragment_alias},
                        ]
                    },
                ),
                ToolPart(
                    tool_id="call-read-query",
                    tool_name="read",
                    tool_status="completed",
                    tool_input={"uri": query_alias},
                ),
                ToolPart(
                    tool_id="call-read-fragment",
                    tool_name="read",
                    tool_status="completed",
                    tool_input={"uri": fragment_alias},
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [canonical_uri]


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_internal_experience_sidecars():
    custom_uri = "viking://user/test/memories/experiences/.custom-experience.md"
    messages = [
        Message(
            id="msg-1",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-search",
                    tool_name="find",
                    tool_status="completed",
                    tool_output={
                        "results": [
                            {"uri": "viking://user/test/memories/experiences/.abstract.md"},
                            {"uri": "viking://user/test/memories/experiences/.overview.md"},
                            {"uri": custom_uri},
                        ]
                    },
                ),
                ToolPart(
                    tool_id="call-read",
                    tool_name="read",
                    tool_status="completed",
                    tool_input={"uri": "viking://user/test/memories/experiences/.abstract.md"},
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.resource_uri for event in events] == [custom_uri]


@pytest.mark.asyncio
async def test_memory_usage_extractor_correlates_read_input_by_tool_id():
    experience_uri = "viking://user/test/memories/experiences/long.md"
    messages = [
        Message(
            id="msg-call",
            role="assistant",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name="read",
                    tool_status="running",
                    tool_input={"uri": experience_uri},
                )
            ],
        ),
        Message(
            id="msg-result",
            role="user",
            parts=[
                ToolPart(
                    tool_id="call-read",
                    tool_name="read",
                    tool_status="completed",
                    tool_output='{"uri":"truncated',
                )
            ],
        ),
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert [event.event_type for event in events] == ["memory.injected"]
    assert events[0].resource_uri == experience_uri


@pytest.mark.asyncio
async def test_memory_usage_extractor_replay_uses_stable_event_id():
    experience_uri = "viking://user/test/memories/experiences/replay.md"
    messages = [
        Message(
            id="msg-replay",
            role="user",
            created_at="2026-07-16T10:00:00Z",
            parts=[
                ToolPart(
                    tool_id="call-replay",
                    tool_name="mcp__openviking__read",
                    tool_status="completed",
                    tool_input={"uris": experience_uri},
                )
            ],
        )
    ]
    extractor = MemoryUsageExtractor()

    first = await extractor.extract(messages=messages, context=_context())
    replay = await extractor.extract(
        messages=messages,
        context=UsageContext(
            account_id="new",
            user_id="test",
            session_id="session-1",
            archive_uri="viking://user/test/sessions/session-1/history/archive_002",
            task_id="task-2",
        ),
    )

    assert first[0].event_id
    assert replay[0].event_id == first[0].event_id
    assert replay[0].task_id == "task-2"
    assert replay[0].evidence["archive_uri"].endswith("archive_002")


@pytest.mark.asyncio
async def test_memory_usage_extractor_ignores_completed_tools_without_tool_id():
    experience_uri = "viking://user/test/memories/experiences/missing-tool-id.md"
    messages = [
        Message(
            id="msg-missing-tool-id",
            role="user",
            parts=[
                ToolPart(
                    tool_name="find",
                    tool_status="completed",
                    tool_output={"results": [{"uri": experience_uri}]},
                ),
                ToolPart(
                    tool_name="read",
                    tool_status="completed",
                    tool_input={"uri": experience_uri},
                ),
            ],
        )
    ]

    events = await MemoryUsageExtractor().extract(messages=messages, context=_context())

    assert events == []
