# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.loop import AgentLoop, _compact_msg_chars
from vikingbot.config.schema import SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse


@pytest.fixture
def loop():
    agent = object.__new__(AgentLoop)
    agent.provider = AsyncMock(spec=LLMProvider)
    agent.provider.chat.return_value = LLMResponse(content="Saved earlier progress.")
    agent.model = "test-model"
    return agent


SESSION = SessionKey(type="test", channel_id="channel", chat_id="chat")
# VLMProviderAdapter reports a failed model call as a response, not an exception.
SUMMARY_ERROR = LLMResponse(
    content="Error calling LLM in VLM Adapter: Summary service unavailable",
    finish_reason="error",
)
RULE_TOOLS = [
    "openviking_rule_search",
    "rule_search",
    "mcp_openviking_rule_search",
]


def tool_turn(name, call_id, content):
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": name, "tool_call_id": call_id, "content": content},
    ]


def history_prefix():
    return [
        {"role": "system", "content": "Follow applicable Rules."},
        {"role": "user", "content": "Prepare the report."},
        {"role": "assistant", "content": "Earlier progress " + "x" * 2_000},
        *tool_turn("read_file", "old-read", "Earlier source."),
    ]


def assert_original_order(original, compacted):
    retained = [message for message in compacted if message in original]
    positions = [original.index(message) for message in retained]
    assert positions == sorted(positions)


def assert_tool_pairs(messages):
    outstanding = set()
    for message in messages:
        if message["role"] != "tool":
            assert not outstanding, "Tool results must directly follow their call."
        for call in message.get("tool_calls", []):
            outstanding.add(call["id"])
        if message["role"] == "tool":
            outstanding.remove(message["tool_call_id"])
    assert not outstanding


def fail_summaries(loop, summary_failure):
    if summary_failure == "exception":
        loop.provider.chat.side_effect = RuntimeError("Summary service unavailable")
    elif summary_failure == "error_response":
        loop.provider.chat.return_value = SUMMARY_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_tool", RULE_TOOLS)
async def test_compaction_keeps_rule_lookup_after_user_correction(loop, rule_tool):
    correction = {"role": "user", "content": "Use the revised deployment target."}
    plan = {"role": "assistant", "content": "I will check Rules for that target."}
    rule_turn = tool_turn(rule_tool, "new-rules", "Require approval for the revised target.")
    messages = [*history_prefix(), correction, plan, *rule_turn]

    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=850)

    assert correction in result
    assert rule_turn[-1] in result
    assert _compact_msg_chars(result) <= 850
    assert_original_order(messages, result)
    assert result[-1] == rule_turn[-1]
    assert_tool_pairs(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_tool", RULE_TOOLS)
@pytest.mark.parametrize("summary_failure", [None, "exception", "error_response"])
async def test_compaction_preserves_latest_user_when_rules_exceed_budget(
    loop, rule_tool, summary_failure
):
    fail_summaries(loop, summary_failure)
    rule_turn = tool_turn(rule_tool, "old-rules", "Exact Rule condition.\n" * 600)
    correction = {"role": "user", "content": "Do not publish; prepare a local draft only."}
    latest_turn = tool_turn("read_file", "latest-read", "Ready to prepare the local draft.")
    messages = [
        *history_prefix(),
        *rule_turn,
        {"role": "assistant", "content": "Work before the correction."},
        correction,
        *latest_turn,
        {"role": "assistant", "content": "READY"},
    ]

    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=900)

    assert correction in result
    assert any(m.get("tool_call_id") == "old-rules" for m in result)
    assert _compact_msg_chars(result) <= 900
    assert result[-1] == messages[-1]
    assert_original_order(messages, result)
    assert_tool_pairs(result)


@pytest.mark.asyncio
async def test_compaction_preserves_entire_parallel_rule_tool_turn(loop):
    rule_turn = tool_turn("mcp_rules_rule_search", "rules", "Rule body.\n" * 1_000)
    read_turn = tool_turn("read_file", "parallel-read", "Parallel file result.")
    rule_turn[0]["tool_calls"].extend(read_turn[0]["tool_calls"])
    rule_turn.append(read_turn[1])
    messages = [
        *history_prefix(),
        *rule_turn,
        {"role": "assistant", "content": "Progress."},
        *tool_turn("read_file", "recent-read", "Recent file result."),
        {"role": "assistant", "content": "Next action."},
    ]

    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=900)

    assert rule_turn[0] in result
    assert read_turn[1] in result
    assert _compact_msg_chars(result) <= 900
    assert_tool_pairs(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_turn_in_tail", [False, True])
@pytest.mark.parametrize("summary_failure", [None, "exception", "error_response"])
async def test_expired_rules_preserve_parallel_write_progress(
    loop, old_turn_in_tail, summary_failure
):
    fail_summaries(loop, summary_failure)
    old_rule = tool_turn("rule_search", "old-rules", "OLD RULE BODY " * 1_000)
    write = tool_turn("write_file", "draft-write", "Saved draft successfully at /draft/report.md.")
    old_rule[0]["content"] = "Saving completed draft."
    old_rule[0]["tool_calls"].extend(write[0]["tool_calls"])
    old_rule.append(write[1])
    progress = (
        []
        if old_turn_in_tail
        else [
            {"role": "assistant", "content": "Other progress."},
            *tool_turn("read_file", "read", "Next source."),
        ]
    )
    messages = [
        *history_prefix(),
        *old_rule,
        *progress,
        *tool_turn("rule_search", "new-rules", "Current Rule."),
        {"role": "assistant", "content": "Continue from saved draft."},
    ]
    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=2_000)
    summary_input = "\n".join(
        str(call.args) + str(call.kwargs) for call in loop.provider.chat.await_args_list
    )
    retained = "\n".join(str(message.get("content", "")) for message in result)
    assert "/draft/report.md" in retained + summary_input
    assert "Saved draft successfully" in retained + summary_input
    if summary_failure:
        assert write[1] in result
        assert "Error calling LLM" not in retained
    assert "OLD RULE BODY" not in retained + summary_input
    assert all(
        call["id"] != "old-rules" for message in result for call in message.get("tool_calls", [])
    )
    assert_tool_pairs(result)
    assert _compact_msg_chars(result) <= 2_000


@pytest.mark.asyncio
async def test_repeated_large_rule_queries_fit_budget_after_recompaction(loop):
    messages = history_prefix()
    for index in range(6):
        messages.extend(
            tool_turn("mcp_openviking_rule_search", f"rules-{index}", "rule\n" * 11_000)
        )
    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=240_000)
    again = await loop._compact_tool_loop(result, SESSION, budget_chars=240_000)

    assert _compact_msg_chars(result) <= 240_000
    assert _compact_msg_chars(again) <= 240_000
    assert_tool_pairs(again)
    assert any("delivery.complete=false" in m.get("content", "") for m in again)


@pytest.mark.asyncio
async def test_short_rule_history_is_also_bounded(loop):
    messages = [*history_prefix()[:2], *tool_turn("rule_search", "large", "rule\n" * 100_000)]
    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=240_000)
    assert _compact_msg_chars(result) <= 240_000
    assert_tool_pairs(result)
    assert_original_order(messages, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_rule", [False, True])
async def test_compaction_refuses_context_when_fixed_instructions_exceed_budget(loop, with_rule):
    messages = [
        {"role": "system", "content": "Mandatory instruction " * 100},
        {"role": "user", "content": "Keep this correction verbatim."},
    ]
    if with_rule:
        messages.extend(tool_turn("rule_search", "rules", "Complete Rule."))
    with pytest.raises(ValueError, match="Context exceeds output budget"):
        await loop._compact_tool_loop(messages, SESSION, budget_chars=900)


@pytest.mark.asyncio
async def test_repeated_compaction_reuses_summary_without_duplicate_rules(loop):
    rule_turn = tool_turn("openviking_rule_search", "rules", "Complete Rule text.")
    messages = [
        *history_prefix(),
        *rule_turn,
        {"role": "user", "content": "Keep working on the draft."},
        *tool_turn("read_file", "recent-read", "Current source."),
        {"role": "assistant", "content": "Current plan."},
    ]
    first = await loop._compact_tool_loop(messages, SESSION, budget_chars=2_000)
    loop.provider.chat.return_value = LLMResponse(content="New progress.")
    continuation = [
        {"role": "assistant", "content": "Additional work."},
        *tool_turn("read_file", "next-read", "New source."),
        {"role": "assistant", "content": "Latest plan."},
    ]

    result = await loop._compact_tool_loop([*first, *continuation], SESSION, budget_chars=2_000)

    notes = [m for m in result if m.get("content", "").startswith("[Context compaction]")]
    assert len(notes) == 1
    assert "Saved earlier progress." in notes[0]["content"]
    assert "New progress." in notes[0]["content"]
    assert result.count(rule_turn[-1]) == 1
    assert result[-1] == continuation[-1]
    assert_tool_pairs(result)


@pytest.mark.asyncio
async def test_compaction_reuses_note_even_when_pinned_rules_leave_a_short_tail(loop):
    rule_turn = tool_turn("openviking_rule_search", "rules", "Complete Rule text.")
    correction = {"role": "user", "content": "Prepare a local draft only."}
    messages = [
        *history_prefix()[:2],
        {"role": "user", "content": "[Context compaction]\n\nSaved draft at /draft/report.md."},
        *rule_turn,
        correction,
        {"role": "assistant", "content": "Continue from the saved draft."},
    ]

    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=2_000)

    notes = [m for m in result if m.get("content", "").startswith("[Context compaction]")]
    assert len(notes) == 1
    assert "Saved draft at /draft/report.md." in notes[0]["content"]
    assert correction in result
    assert result.count(rule_turn[-1]) == 1
    assert result[-1] == messages[-1]
    assert_tool_pairs(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_summary_fails", [False, True])
async def test_compaction_without_rules_summarizes_older_work_and_keeps_recent_turn(
    loop, first_summary_fails
):
    if first_summary_fails:
        loop.provider.chat.side_effect = [
            SUMMARY_ERROR,
            LLMResponse(content="Saved earlier progress."),
        ]
    messages = [
        *history_prefix(),
        {"role": "assistant", "content": "Recent plan."},
        *tool_turn("read_file", "recent-read", "Recent result."),
        {"role": "assistant", "content": "Latest plan."},
    ]

    result = await loop._compact_tool_loop(messages, SESSION, budget_chars=850)

    assert result[:2] == messages[:2]
    assert result[-1] == messages[-1]
    assert any("Saved earlier progress." in m.get("content", "") for m in result)
    assert len(str(result)) < len(str(messages))
    assert_tool_pairs(result)
