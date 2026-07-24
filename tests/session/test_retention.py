# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.message import Message, TextPart, ToolPart
from openviking.server.routers.sessions import CommitRequest
from openviking.session.retention import (
    build_turns,
    fit_active_messages_to_budget,
    plan_retention,
)


def _message(message_id: str, role: str, text: str) -> Message:
    return Message(id=message_id, role=role, parts=[TextPart(text)])


def test_build_turns_does_not_treat_tool_transport_as_user_query():
    messages = [
        _message("u1", "user", "find the issue"),
        Message(
            id="a1",
            role="assistant",
            parts=[TextPart("I will inspect it"), ToolPart(tool_id="t1", tool_name="read")],
        ),
        Message(
            id="tr1",
            role="user",
            parts=[
                ToolPart(
                    tool_id="t1",
                    tool_name="read",
                    tool_output="result",
                    tool_status="completed",
                )
            ],
        ),
        _message("a2", "assistant", "done"),
    ]

    turns = build_turns(messages)

    assert len(turns) == 1
    assert turns[0].anchor.id == "u1"
    assert [[message.id for message in step.messages] for step in turns[0].steps] == [
        ["a1", "tr1"],
        ["a2"],
    ]


def test_explicit_assistant_role_tool_transport_stays_with_previous_step():
    messages = [
        _message("u1", "user", "find the issue"),
        Message(
            id="a1",
            role="assistant",
            parts=[TextPart("I will inspect it"), ToolPart(tool_id="t1", tool_name="read")],
        ),
        Message(
            id="tr1",
            role="assistant",
            message_kind="tool_transport",
            parts=[
                ToolPart(
                    tool_id="t1",
                    tool_name="read",
                    tool_output="result",
                    tool_status="completed",
                )
            ],
        ),
    ]

    turns = build_turns(messages)

    assert [[message.id for message in step.messages] for step in turns[0].steps] == [
        ["a1", "tr1"]
    ]


def test_message_semantic_fields_round_trip_without_affecting_legacy_rows():
    message = Message(
        id="checkpoint-1",
        role="assistant",
        parts=[TextPart("state")],
        turn_id="turn-1",
        message_kind="checkpoint",
        source_message_ids=["a1", "a2"],
    )

    restored = Message.from_dict(message.to_dict())
    legacy = Message.from_dict({"id": "legacy", "role": "user", "content": "hello"})

    assert restored.turn_id == "turn-1"
    assert restored.message_kind == "checkpoint"
    assert restored.source_message_ids == ["a1", "a2"]
    assert legacy.message_kind is None


def test_one_user_and_ten_assistant_steps_retains_the_user_anchor():
    messages = [_message("u1", "user", "investigate")]
    messages.extend(_message(f"a{i}", "assistant", f"step {i}") for i in range(10))

    plan = plan_retention(
        messages,
        keep_recent_turn_count=1,
        token_budget=12_000,
    )

    assert plan.archive_messages == []
    assert [message.id for message in plan.retained_messages] == [
        "u1",
        *(f"a{i}" for i in range(10)),
    ]


def test_oversized_latest_turn_keeps_anchor_and_atomic_raw_tail():
    messages = [_message("u1", "user", "investigate")]
    for index in range(6):
        messages.append(_message(f"a{index}", "assistant", str(index) * 1000))
        messages.append(
            Message(
                id=f"tr{index}",
                role="user",
                parts=[
                    ToolPart(
                        tool_id=f"t{index}",
                        tool_name="read",
                        tool_output=f"result-{index}",
                        tool_status="completed",
                    )
                ],
            )
        )

    plan = plan_retention(
        messages,
        keep_recent_turn_count=1,
        token_budget=700,
        min_raw_tail_steps=1,
    )

    retained_ids = [message.id for message in plan.retained_messages]
    archived_ids = [message.id for message in plan.archive_messages]
    assert plan.partial_turn is True
    assert retained_ids[0] == "u1"
    assert retained_ids[-2:] == ["a5", "tr5"]
    assert archived_ids[0] == "u1"  # duplicate anchor makes Phase 2 self-contained
    assert ("a4" in archived_ids) == ("tr4" in archived_ids)
    assert ("a4" in retained_ids) == ("tr4" in retained_ids)
    assert plan.checkpoint_source_message_ids == archived_ids[1:]


def test_oversized_legacy_assistant_only_turn_archives_without_checkpoint():
    messages = [
        _message("a1", "assistant", "A" * 1000),
        _message("a2", "assistant", "B" * 1000),
        _message("a3", "assistant", "C" * 1000),
    ]

    plan = plan_retention(
        messages,
        keep_recent_turn_count=1,
        token_budget=150,
        min_raw_tail_steps=1,
    )

    assert [message.id for message in plan.archive_messages] == ["a1", "a2", "a3"]
    assert plan.retained_messages == []
    assert plan.turn_anchor is None
    assert plan.checkpoint_source_message_ids == []
    assert plan.partial_turn is False
    assert plan.budget_exceeded is False


def test_complete_old_turns_are_archived_before_splitting_latest_turn():
    messages = [
        _message("u1", "user", "first"),
        _message("a1", "assistant", "answer one"),
        _message("u2", "user", "second"),
        _message("a2", "assistant", "answer two"),
        _message("u3", "user", "third"),
        _message("a3", "assistant", "answer three"),
    ]

    plan = plan_retention(
        messages,
        keep_recent_turn_count=2,
        token_budget=20,
    )

    assert [message.id for message in plan.archive_messages] == ["u1", "a1"]
    assert [message.id for message in plan.retained_messages] == ["u2", "a2", "u3", "a3"]


def test_turn_retention_fields_require_explicit_mode_opt_in():
    with pytest.raises(ValueError, match="retention_mode='turn_budget'"):
        CommitRequest(keep_recent_turn_count=3)

    request = CommitRequest(
        retention_mode="turn_budget",
        keep_recent_turn_count=3,
    )
    assert request.retention_mode == "turn_budget"


def test_active_budget_keeps_latest_anchor_and_final_without_mutating_raw():
    user_text = "latest query"
    final_text = "F" * 1200
    messages = [
        _message("u1", "user", user_text),
        _message("a1", "assistant", "intermediate" * 200),
        _message("a2", "assistant", final_text),
    ]

    plan = fit_active_messages_to_budget(messages, token_budget=120)

    assert [message.id for message in plan.messages] == ["u1", "a2"]
    assert plan.estimated_tokens <= 120
    assert plan.truncated_message_ids == ["a2"]
    assert messages[0].content == user_text
    assert messages[-1].content == final_text


def test_active_budget_reserves_space_for_long_anchor_and_final():
    messages = [
        _message("u1", "user", "U" * 100),
        _message("a1", "assistant", "done"),
    ]

    plan = fit_active_messages_to_budget(messages, token_budget=5)

    assert [message.id for message in plan.messages] == ["u1", "a1"]
    assert plan.messages[0].content
    assert plan.messages[1].content == "done"
    assert plan.estimated_tokens <= 5
    assert plan.dropped_message_ids == []
    assert plan.truncated_message_ids == ["u1"]


def test_active_budget_never_splits_an_assistant_tool_step():
    assistant = Message(
        id="a1",
        role="assistant",
        parts=[TextPart("inspect"), ToolPart(tool_id="t1", tool_name="read")],
    )
    transport = Message(
        id="tr1",
        role="user",
        parts=[
            ToolPart(
                tool_id="t1",
                tool_name="read",
                tool_output="result" * 1000,
                tool_status="completed",
            )
        ],
    )
    messages = [
        _message("u1", "user", "query"),
        assistant,
        transport,
        _message("a2", "assistant", "final"),
    ]

    plan = fit_active_messages_to_budget(messages, token_budget=40)
    returned_ids = {message.id for message in plan.messages}

    assert plan.estimated_tokens <= 40
    assert ("a1" in returned_ids) == ("tr1" in returned_ids)
