# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.message import Message
from openviking.message.part import TextPart, ToolPart
from openviking.prompts.manager import PromptManager
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.utils.content_template import (
    ContentTemplateError,
    render_content_template,
    validate_content_template,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.uri import render_template


def messages(count=15):
    return [
        Message(
            id=f"message-{i}",
            role="user" if i % 2 == 0 else "assistant",
            parts=[
                TextPart(
                    f"[message-{i}] "
                    + (f"This sentence belongs to message {i} and ends here. " * 30)
                    + f"End-{i}."
                )
            ],
            created_at=f"2023-05-{i + 1:02d}T00:00:00Z",
        )
        for i in range(count)
    ]


def chunk_index(context, source_index, offset=0):
    return next(
        i
        for i, message in enumerate(context.messages)
        if (meta := context.chunk_meta.get(id(message)))
        and meta.source_message_id == f"message-{source_index}"
        and meta.chunk_index == offset
    )


def assert_messages(body, original, expected):
    for i, message in enumerate(original):
        if i in expected:
            assert message.parts[0].text in body
            assert body.count(f"[message-{i}]") == 1
        else:
            assert f"[message-{i}]" not in body


def test_padding_counts_complete_original_messages_not_extraction_chunks():
    original = messages()
    context = ExtractContext(original)
    selected = str(chunk_index(context, 5, offset=1))

    body = context.get_event_content(selected, "Summary", 0, 3)

    assert_messages(body, original, set(range(2, 9)))
    assert "..." not in body
    assert original[5].parts[0].text not in context.get_event_content(selected, "Summary", 0)
    assert context.get_day(selected) == "06"
    assert context.read_message_ranges(selected).elements == [[context.messages[int(selected)]]]


@pytest.mark.parametrize("indices", [(0,), (14,), (3, 4), (3, 11)])
def test_padding_clips_boundaries_and_deduplicates_overlapping_ranges(indices):
    original = messages()
    context = ExtractContext(original)
    selected = ",".join(str(chunk_index(context, i, 1)) for i in indices)

    body = context.get_event_content(selected, "Summary", 0, 3)

    expected = {j for i in indices for j in range(max(0, i - 3), min(len(original), i + 4))}
    assert_messages(body, original, expected)
    if indices == (3, 11):
        assert "\n...\n" in body


@pytest.mark.parametrize("barrier", ["peer", "system", "tool"])
def test_context_does_not_cross_peer_or_tool_boundaries(barrier):
    original = messages(7)
    if barrier == "peer":
        original[3].peer_id = "other-peer"
    elif barrier == "system":
        original[3].role = "system"
    else:
        original[3].parts.append(ToolPart(tool_name="read", tool_status="completed"))
    context = ExtractContext(original)

    body = context.get_event_content(str(chunk_index(context, 2)), "Summary", 0, 3)

    assert_messages(body, original, {0, 1, 2})


def test_bundled_event_renders_context_without_changing_core_fields():
    original = messages(7)
    context = ExtractContext(original)
    selected = str(chunk_index(context, 3, 1))
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.load_from_yaml(str(PromptManager._get_bundled_templates_dir() / "memory/events.yaml"))
    schema = registry.get("events")
    fields = {"event_name": "core_event", "goal": "core", "summary": "Summary", "ranges": selected}

    validate_content_template(schema.content_template, "events")
    stored = MemoryFileUtils.write(
        MemoryFile(extra_fields=fields),
        content_template=schema.content_template,
        extract_context=context,
    )
    parsed = MemoryFileUtils.read(stored)
    assert_messages(parsed.content, original, set(range(7)))
    assert parsed.extra_fields["ranges"] == selected
    assert parsed.extra_fields["summary"] == "Summary"
    assert "2023-05-04" in parsed.content
    embedded = render_template(
        schema.embedding_template, {**fields, "content": parsed.content}, extract_context=context
    )
    assert_messages(embedded, original, set(range(7)))
    assert "EventName: core_event" in embedded
    assert (
        render_content_template(schema.content_template, "events", fields, context).strip()
        == parsed.content
    )


@pytest.mark.parametrize("count", [-1, 33, True, 1.5, "3"])
def test_context_count_is_bounded_integer(count):
    context = ExtractContext(messages(1))
    with pytest.raises(ValueError, match="context_messages"):
        context.get_event_content("0", "Summary", 0, count)


@pytest.mark.parametrize("expression", ["33", "true", "1.5", "summary"])
def test_account_templates_reject_invalid_context_counts(expression):
    with pytest.raises(ContentTemplateError, match="invalid_context_messages"):
        validate_content_template(
            "{{ extract_context.get_event_content(ranges, summary, 0, " + expression + ") }}",
            "events",
        )


def test_empty_and_out_of_bounds_ranges_do_not_include_neighboring_text():
    context = ExtractContext(messages(1))
    assert context.get_event_content("", "Summary", 0, 3) == "Summary"
    assert context.get_event_content("999", "Summary", 0, 3) == "Summary"
