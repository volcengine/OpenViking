# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.utils.message_format import format_messages, sanitize_openai_messages


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


def test_format_messages_renders_multimodal_content_as_json():
    rendered = format_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image."},
                    {"type": "image_url", "image_url": {"url": "[redacted]"}},
                ],
            }
        ]
    )

    assert "[user]" in rendered
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
