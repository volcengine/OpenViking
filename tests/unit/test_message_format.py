# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import hashlib

from openviking.utils.message_format import (
    format_messages,
    normalize_openai_tool_call_ids,
    sanitize_openai_messages,
)


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


def test_normalize_openai_tool_call_ids_rewrites_oversized_pairs_only():
    first_long_id = "vertex_" + "a" * 1000
    second_long_id = "vertex_" + "b" * 1000
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": first_long_id, "function": {"name": "read", "arguments": "{}"}},
                {"id": "call-short", "function": {"name": "find", "arguments": "{}"}},
                {"id": second_long_id, "function": {"name": "search", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": first_long_id, "content": "one"},
        {"role": "tool", "tool_call_id": "call-short", "content": "two"},
        {"role": "tool", "tool_call_id": second_long_id, "content": "three"},
    ]

    normalized = normalize_openai_tool_call_ids(messages)

    first_call_id = normalized[0]["tool_calls"][0]["id"]
    second_call_id = normalized[0]["tool_calls"][2]["id"]
    assert first_call_id == normalized[1]["tool_call_id"]
    assert second_call_id == normalized[3]["tool_call_id"]
    assert first_call_id != second_call_id
    assert len(first_call_id) == 40
    assert len(second_call_id) == 40
    assert normalized[0]["tool_calls"][1]["id"] == "call-short"
    assert normalized[2]["tool_call_id"] == "call-short"
    assert messages[0]["tool_calls"][0]["id"] == first_long_id
    assert normalize_openai_tool_call_ids(messages) == normalized


def test_normalize_openai_tool_call_ids_pairs_reused_ids_by_occurrence():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "read:0", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "call_id": "read:0", "content": "one"},
        {
            "role": "assistant",
            "tool_calls": [{"call_id": "read:0", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "read:0", "content": "two"},
    ]

    normalized = normalize_openai_tool_call_ids(messages)
    call_ids = [
        normalized[0]["tool_calls"][0]["id"],
        normalized[2]["tool_calls"][0]["call_id"],
    ]
    result_ids = [normalized[1]["call_id"], normalized[3]["tool_call_id"]]

    assert call_ids[0] == "read:0"
    assert call_ids == result_ids
    assert len(set(call_ids)) == 2
    assert len(call_ids[1]) == 40
    assert messages[2]["tool_calls"][0]["call_id"] == "read:0"
    assert messages[3]["tool_call_id"] == "read:0"
    assert normalize_openai_tool_call_ids(normalized) == normalized


def test_normalize_openai_tool_call_ids_avoids_preserved_id_collision():
    long_id = "vertex_" + "x" * 1000
    first_candidate = "call_" + hashlib.sha256(long_id.encode()).hexdigest()[:35]
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": long_id, "function": {"name": "read", "arguments": "{}"}},
                {"id": first_candidate, "function": {"name": "find", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": long_id, "content": "one"},
        {"role": "tool", "tool_call_id": first_candidate, "content": "two"},
    ]

    normalized = normalize_openai_tool_call_ids(messages)

    replacement = normalized[0]["tool_calls"][0]["id"]
    assert len(replacement) == 40
    assert replacement != first_candidate
    assert normalized[1]["tool_call_id"] == replacement
    assert normalized[0]["tool_calls"][1]["id"] == first_candidate
    assert normalized[2]["tool_call_id"] == first_candidate


def test_normalize_openai_tool_call_ids_avoids_reused_id_collision():
    reused_id = "read:0"
    first_candidate = "call_" + hashlib.sha256(f"{reused_id}\0{1}".encode()).hexdigest()[:35]
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": reused_id, "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": reused_id, "content": "one"},
        {
            "role": "assistant",
            "tool_calls": [{"id": reused_id, "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": reused_id, "content": "two"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": first_candidate, "function": {"name": "find", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": first_candidate, "content": "three"},
    ]

    normalized = normalize_openai_tool_call_ids(messages)
    call_ids = [normalized[index]["tool_calls"][0]["id"] for index in (0, 2, 4)]
    result_ids = [normalized[index]["tool_call_id"] for index in (1, 3, 5)]

    assert call_ids == result_ids
    assert len(set(call_ids)) == 3
    assert call_ids[1] != first_candidate
    assert call_ids[2] == first_candidate


def test_normalize_openai_tool_call_ids_supports_call_id_aliases():
    long_id = "vertex_" + "x" * 1000
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"call_id": long_id, "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "call_id": long_id, "content": "one"},
    ]

    normalized = normalize_openai_tool_call_ids(messages)

    assert len(normalized[0]["tool_calls"][0]["call_id"]) == 40
    assert normalized[0]["tool_calls"][0]["call_id"] == normalized[1]["call_id"]
    assert messages[0]["tool_calls"][0]["call_id"] == long_id
