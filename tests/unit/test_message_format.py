# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from copy import deepcopy
from typing import Any

import pytest

from openviking.utils.message_format import (
    format_messages,
    sanitize_openai_messages,
    tool_images_as_user_messages,
)


def test_tool_images_follow_all_parallel_results_with_source_attribution():
    first = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AA==", "detail": "high"},
    }
    second = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AQ=="}}
    third = {"type": "image_url", "image_url": {"url": "data:image/png;base64,Ag=="}}
    messages: list[dict[str, Any]] = [
        {"role": "assistant", "tool_calls": [{"id": "read-a"}, {"id": "read-b"}, {"id": "read-c"}]},
        {
            "role": "tool",
            "tool_call_id": "read-a",
            "name": "read",
            "content": [
                {"type": "text", "text": "viking://resources/a.png"},
                first,
                {"type": "text", "text": "viking://resources/b.png"},
                second,
            ],
        },
        {"role": "tool", "tool_call_id": "read-b", "content": "text-only result"},
        {"role": "tool", "tool_call_id": "read-c", "content": [third]},
        {"role": "assistant", "content": "next turn"},
    ]
    original = deepcopy(messages)

    converted = tool_images_as_user_messages(messages)

    assert [m["role"] for m in converted] == [
        "assistant",
        "tool",
        "tool",
        "tool",
        "user",
        "assistant",
    ]
    assert [m["tool_call_id"] for m in converted[1:4]] == ["read-a", "read-b", "read-c"]
    assert "viking://resources/a.png" in converted[1]["content"]
    assert "viking://resources/b.png" in converted[1]["content"]
    assert "image 1 from tool call read-a" in converted[1]["content"]
    assert "image 2 from tool call read-a" in converted[1]["content"]
    assert converted[2] == messages[2]
    assert [p for p in converted[4]["content"] if p["type"] == "image_url"] == [
        first,
        second,
        third,
    ]
    assert "read-c" in converted[4]["content"][-2]["text"]
    assert messages == original
    assert tool_images_as_user_messages(converted) == converted


def test_unsupported_tool_images_fail_without_exposing_image_bytes():
    message: dict[str, Any] = {
        "role": "tool",
        "name": "read_source",
        "tool_call_id": "read-1",
        "content": [{"type": "input_image", "image_url": "data:image/png;base64,PRIVATE"}],
    }
    original = deepcopy(message)
    with pytest.raises(ValueError, match="input_image") as caught:
        tool_images_as_user_messages([message])
    assert "read_source[read-1]" in str(caught.value)
    assert "PRIVATE" not in str(caught.value)
    assert message == original


def test_format_messages_renders_roles_and_content():
    rendered = format_messages(
        [
            {"role": "system", "content": "Follow the contract."},
            {"role": "user", "content": "Remember blue."},
        ]
    )

    assert rendered == (
        "=== Messages ===\n"
        "\n[system]\n"
        "Follow the contract.\n"
        "\n[user]\n"
        "Remember blue.\n"
        "\n=== End Messages ==="
    )


@pytest.mark.parametrize("role", ["user", "tool"])
def test_format_messages_renders_multimodal_content_as_json(role):
    rendered = format_messages(
        [
            {
                "role": role,
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image_url", "image_url": {"url": "[redacted]"}},
                ],
            }
        ]
    )

    assert f"[{role}]" in rendered
    assert '"type": "image_url"' in rendered
    assert '"url": "[redacted]"' in rendered


def test_format_messages_renders_tool_call_details():
    rendered = format_messages(
        [
            {
                "role": "assistant",
                "content": "Checking.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "read", "arguments": '{"uri":"viking://x"}'},
                    }
                ],
            }
        ]
    )

    assert "[assistant tool_call] (id=call-1, name=read)" in rendered
    assert '"uri": "viking://x"' in rendered


def test_sanitize_openai_messages_drops_only_empty_assistant_turns():
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": "   "},
        {"role": "assistant", "content": []},
        {"role": "assistant", "content": [{"type": "text", "text": ""}]},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        },
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": ""},
    ]

    sanitized = sanitize_openai_messages(messages)

    assert sanitized == [messages[0], messages[5], messages[6], messages[7]]
    assert sanitized is not messages
    assert sanitized[1] is not messages[5]


def test_sanitize_openai_messages_preserves_nonempty_multimodal_assistant_content():
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": "answer"}],
    }

    assert sanitize_openai_messages([message]) == [message]
