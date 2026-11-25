# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from dataclasses import replace

import pytest

from openviking.message import Message, TextPart, ToolPart
from openviking.usage_reporter import MemoryUsageExtractor, UsageContext

pytestmark = pytest.mark.asyncio

MEMORIES = "viking://user/test/memories"
EXPERIENCES = f"{MEMORIES}/experiences"
CONTEXT = UsageContext(
    account_id="new",
    user_id="test",
    session_id="session-1",
    archive_uri="viking://user/test/sessions/session-1/history/archive_001",
    task_id="task-1",
)


def tool(name, tool_input=None, output=None, *, call_id=None, status="completed"):
    return ToolPart(
        tool_id=name if call_id is None else call_id,
        tool_name=name,
        tool_status=status,
        tool_input=tool_input,
        tool_output=output,
    )


async def extract(*parts):
    message = Message(id="turn", role="user", parts=list(parts))
    return await MemoryUsageExtractor().extract(messages=[message], context=CONTEXT)


async def test_memory_usage_extractor_emits_recall_and_injection_events():
    uri = f"{EXPERIENCES}/order-exchange.md"
    messages = [
        Message(
            id="search",
            role="user",
            parts=[
                TextPart("Handle an exchange"),
                tool("find", output={"results": [{"uri": uri}]}),
            ],
        ),
        Message(
            id="request", role="assistant", parts=[tool("read", {"uri": uri}, status="running")]
        ),
        Message(
            id="result",
            role="user",
            created_at="2026-07-10T20:30:40.123456+08:00",
            parts=[tool("read", output='{"uri":"truncated')],
        ),
    ]
    extractor = MemoryUsageExtractor()
    events = await extractor.extract(messages=messages, context=CONTEXT)

    assert [(e.event_type, e.resource_uri, e.resource_type) for e in events] == [
        ("memory.recalled", uri, "experience"),
        ("memory.injected", uri, "experience"),
    ]
    assert events[0].evidence == {
        "archive_uri": CONTEXT.archive_uri,
        "message_id": "search",
        "tool_call_id": "find",
        "tool_name": "find",
    }
    assert events[1].evidence["message_id"] == "result"
    assert events[1].occurred_at == "2026-07-10T12:30:40.123Z"

    retry = replace(
        CONTEXT, archive_uri=CONTEXT.archive_uri.replace("001", "002"), task_id="task-2"
    )
    replay = await extractor.extract(messages=messages, context=retry)
    assert [e.event_id for e in replay] == [e.event_id for e in events]
    for event in replay:
        assert event.event_id and event.task_id == retry.task_id
        assert event.evidence["archive_uri"] == retry.archive_uri


@pytest.mark.parametrize(
    "name",
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
async def test_memory_usage_extractor_recognizes_generic_search_tools(name):
    first, second = f"{EXPERIENCES}/first.md", f"{EXPERIENCES}/second.md"
    output = {"details": {"memories": [{"uri": uri} for uri in (first, second, first)]}}
    events = await extract(tool(name, output=output))
    assert [(e.event_type, e.resource_uri) for e in events] == [
        ("memory.recalled", first),
        ("memory.recalled", second),
    ]


async def test_memory_usage_extractor_does_not_count_search_scope_as_result():
    uri = f"{EXPERIENCES}/scope.md"
    events = await extract(
        tool(
            "ov_search",
            {"query": "missing", "uri": uri},
            {"details": {"action": "searched", "uri": uri, "memories": [], "total": 0}},
        ),
        tool("find", output={"results": [{"uri": f"{EXPERIENCES}/hit.md"}]}),
    )
    assert [e.resource_uri for e in events] == [f"{EXPERIENCES}/hit.md"]


@pytest.mark.parametrize("name", ["list", "openviking_list", "ov_list", "mcp__openviking__list"])
async def test_memory_usage_extractor_list_counts_only_experience_files(name):
    uri = f"{EXPERIENCES}/listed.md"
    output = {
        "details": {
            "entries": [
                {"uri": uri, "isDir": False},
                {"uri": f"{EXPERIENCES}/nested", "isDir": True},
                {"uri": f"{MEMORIES}/preferences/p.md", "isDir": False},
            ]
        }
    }
    events = await extract(tool(name, {"uri": EXPERIENCES}, output))
    assert [(e.event_type, e.resource_uri) for e in events] == [("memory.recalled", uri)]


async def test_memory_usage_extractor_drops_the_tags_suffix_from_mcp_text_results():
    spaced = f"{EXPERIENCES}/order exchange.md"
    deep = f"{EXPERIENCES}/nested/deep.md"
    openclaw = f"{EXPERIENCES}/table result.md"
    events = await extract(
        tool(
            "mcp__plugin_openviking-memory_openviking__find",
            output=(
                "Found 4 item(s):\n\n"
                f'- [memory 90%] {spaced} tags=["note=say \\"hi\\"", "{deep}=1"]\n'
                f"- [memory 80%] {deep} tags=[]\n"
                f"- [memory 70%] {EXPERIENCES}/plain.md\n"
                f"- [memory 60%] {MEMORIES}/preferences/p.md\n"
            ),
        ),
        tool(
            "mcp__openviking__list",
            {"uri": EXPERIENCES, "include_tags": True},
            f'[file] tagged.md tags=["team=search"]\n[file] {deep} tags=[]\n[dir] nested',
        ),
        tool(
            "mcp__openviking__list",
            {"uri": EXPERIENCES},
            "[file] notes tags=[]\n[file] .overview.md\n[dir] nested",
            call_id="plain-list",
        ),
        tool("ov_search", output=f"1  memory  {openclaw}  2  0.91  exchange procedure"),
    )
    assert [e.resource_uri for e in events] == [
        spaced,
        deep,
        f"{EXPERIENCES}/plain.md",
        f"{EXPERIENCES}/tagged.md",
        deep,
        f"{EXPERIENCES}/notes tags=[]",
        openclaw,
    ]


@pytest.mark.parametrize("user_root", ["viking://user", "viking://~"], ids=["legacy", "home"])
async def test_memory_usage_extractor_canonicalizes_short_user_list_uri(user_root):
    events = await extract(
        tool(
            "mcp__openviking__list",
            {"uri": f"{user_root}/memories/experiences"},
            "[file] listed experience.md",
        )
    )
    assert [e.resource_uri for e in events] == [f"{EXPERIENCES}/listed experience.md"]


@pytest.mark.parametrize(
    "name,field",
    [
        ("read", "uris"),
        ("openviking_read", "uris"),
        ("ov_read", "uri"),
        ("mcp__openviking__read", "uris"),
        ("mcp__plugin_openviking-memory_openviking__read", "uris"),
    ],
)
async def test_memory_usage_extractor_recognizes_generic_read_tools(name, field):
    uri = f"{EXPERIENCES}/read.md"
    events = await extract(tool(name, {field: uri}, "experience content"))
    assert [(e.event_type, e.resource_uri) for e in events] == [("memory.injected", uri)]


@pytest.mark.parametrize(
    "name",
    [
        "multi_read",
        "openviking_multi_read",
        "ov_multi_read",
        "mcp__openviking__multi_read",
        "mcp__plugin_openviking-memory_openviking__multi_read",
        "mcp__PLUGIN_openviking-memory_OpenViking__MULTI_READ",
    ],
)
async def test_memory_usage_extractor_multi_read_counts_only_successful_experiences(name):
    read = [
        (f"{EXPERIENCES}/success.md", True),
        (f"{EXPERIENCES}/failed.md", False),
        (f"{MEMORIES}/preferences/p.md", True),
    ]
    events = await extract(
        tool(
            name,
            {"uris": [uri for uri, _ in read]},
            {"details": {"results": [{"uri": uri, "success": ok} for uri, ok in read]}},
        )
    )
    assert [(e.event_type, e.resource_uri) for e in events] == [("memory.injected", read[0][0])]


@pytest.mark.parametrize(
    "user_root",
    ["viking://user/test", "viking://user", "viking://~"],
    ids=["canonical", "legacy", "home"],
)
async def test_memory_usage_extractor_ignores_generic_read_not_found_result(user_root):
    uri = f"{user_root}/memories/experiences/missing.md"
    assert (
        await extract(tool("mcp__openviking__read", {"uris": uri}, f"(nothing found at {uri})"))
        == []
    )


async def test_memory_usage_extractor_ignores_other_users_experience_uris():
    own = f"{EXPERIENCES}/.custom-experience.md"
    excluded = [
        "viking://user/other/memories/experiences/other.md",
        f"{MEMORIES}/preferences/p.md",
        f"{MEMORIES}/trajectories/t.md",
        f"{EXPERIENCES}/own.md?source=codex",
        f"{EXPERIENCES}/own.md#approach",
        f"{EXPERIENCES}/.abstract.md",
        f"{EXPERIENCES}/.overview.md",
    ]
    events = await extract(
        tool("find", output={"results": [{"uri": uri} for uri in [own, *excluded]]}),
        *(
            tool("read", {"uri": uri}, "readable", call_id=f"read-{i}")
            for i, uri in enumerate([own, *excluded])
        ),
    )
    assert [(e.event_type, e.resource_uri) for e in events] == [
        ("memory.recalled", own),
        ("memory.injected", own),
    ]


async def test_memory_usage_extractor_does_not_fuzzy_match_other_mcp_tools():
    uri = f"{EXPERIENCES}/ignored.md"
    output = {"results": [{"uri": uri}]}
    events = await extract(
        tool("search_experience", output=output),
        tool("read_experience", {"uri": uri}, "readable"),
        tool("mcp__other__search", output=output),
        tool("mcp__other__read", {"uri": uri}, "readable"),
        tool("mcp__plugin_other-memory_other__read", {"uri": uri}, "readable"),
        tool("find", output=output, call_id=""),
        tool("read", {"uri": uri}, "readable", call_id=""),
    )
    assert events == []
