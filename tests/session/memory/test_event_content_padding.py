from __future__ import annotations

import pytest

from openviking.message import Message, TextPart
from openviking.session.memory.memory_updater import ExtractContext


def messages(count):
    return [
        Message(id=f"m{i}", role="user", parts=[TextPart(text=f"Message {i}.")])
        for i in range(count)
    ]


@pytest.mark.parametrize(
    ("ranges", "expected"),
    [
        ("3", "1-5"),
        ("0", "0-2"),
        ("10", "8-10"),
        ("0-10", "0-10"),
        ("1,9", "0-3,7-10"),
        ("2,5", "0-7"),
        ("2-4,3-5", "0-7"),
        ("9,1,1", "0-3,7-10"),
    ],
)
def test_span_padding_clips_and_unions_within_event(ranges, expected):
    source = messages(11)
    context = ExtractContext(source)
    actual = context.expand_message_ranges(ranges, padding=2)
    assert actual == expected
    assert context._parse_message_ranges(ranges) <= context._parse_message_ranges(actual)


@pytest.mark.parametrize("padding", [1, 2])
def test_padding_counts_original_messages_not_chunks(padding):
    source = messages(8)
    source[2].parts = [TextPart(text="A sentence with lots of source context. " * 20)]
    source[3].parts = [TextPart(text="Another long original message about the event. " * 20)]
    context = ExtractContext(source)

    def source_id(m):
        chunk = context.chunk_meta.get(id(m))
        return chunk.source_message_id if chunk is not None else m.id

    source3_units = [i for i, m in enumerate(context.messages) if source_id(m) == "m3"]
    assert len(source3_units) > 2
    selected = source3_units[1]
    expanded = context._parse_message_ranges(
        context.expand_message_ranges(str(selected), padding=padding)
    )
    expected_ids = {f"m{i}" for i in range(3 - padding, 3 + padding + 1)}
    expected = {i for i, m in enumerate(context.messages) if source_id(m) in expected_ids}
    assert expanded == expected
    assert len(expanded) > 5
    assert context._parse_message_ranges(
        context.expand_message_ranges(str(selected), padding=0)
    ) == {selected}


@pytest.mark.parametrize("ranges", ["", "bad", "-1", "11", "0-11", "3-1", "1,", "1.5"])
def test_invalid_input_is_not_hidden_by_clipping(ranges):
    source = messages(11)
    with pytest.raises(ValueError):
        ExtractContext(source).expand_message_ranges(ranges, padding=2)


@pytest.mark.parametrize("padding", [-1, True, 1.5])
def test_invalid_padding(padding):
    source = messages(3)
    with pytest.raises(ValueError, match="padding"):
        ExtractContext(source).expand_message_ranges("1", padding=padding)


def test_large_padding_never_crosses_session_bounds():
    source = messages(3)
    assert ExtractContext(source).expand_message_ranges("1", padding=100) == "0-2"


def test_missing_source_mapping_is_rejected():
    source = messages(3)
    context = ExtractContext(source[:2])
    context.messages.append(source[2])
    with pytest.raises(ValueError, match="no original source"):
        context.expand_message_ranges("1", padding=2)


@pytest.mark.parametrize(
    ("ranges", "expected"),
    [
        ("3", "2-4"),
        ("0", "0-1"),
        ("10", "9-10"),
        ("0-10", "0-10"),
        ("1,9", "0-2,8-10"),
        ("2,4", "1-5"),
        ("2,5", "1-6"),
        ("2-4", "1-5"),
    ],
)
def test_one_message_padding(ranges, expected):
    source = messages(11)
    context = ExtractContext(source)
    one = context.expand_message_ranges(ranges, padding=1)
    two = context.expand_message_ranges(ranges, padding=2)
    assert one == expected
    assert (
        context._parse_message_ranges(ranges)
        <= context._parse_message_ranges(one)
        <= context._parse_message_ranges(two)
    )


@pytest.mark.parametrize("padding,expected", [(None, "1-3"), (0, "2"), (1, "1-3"), (2, "0-4")])
def test_event_content_pads_only_body_and_preserves_inputs(padding, expected):
    from copy import deepcopy

    source = messages(5)
    before = deepcopy(source)
    context = ExtractContext(source)
    kwargs = {} if padding is None else {"range_padding": padding}
    assert context.get_event_content("2", "Summary", 0, **kwargs) == (
        context.read_message_ranges(expected).pretty_print()
    )
    assert source == before
    assert context.expand_message_ranges("1,2", 0) == "1,2"


def test_ratio_uses_original_selection_not_padded_body():
    source = messages(3)
    source[0].parts = source[2].parts = [TextPart(text="Long adjacent message. " * 30)]
    context = ExtractContext(source)
    index = next(i for i, message in enumerate(context.messages) if message.id == "m1")
    ranges = str(index)
    original = context.read_message_ranges(ranges).pretty_print()
    summary = "A short summary."
    threshold = len(summary) / len(original)
    assert context.get_event_content(ranges, summary, threshold) == (
        context.read_message_ranges(f"0-{len(context.messages) - 1}").pretty_print()
    )
    assert context.get_event_content(ranges, summary, threshold + 0.01) == summary


@pytest.mark.parametrize("summary", [None, "", "Summary", " \t\n"])
def test_empty_selection_or_source_is_not_replaced_by_neighbors(summary):
    from openviking.message.part import ToolPart

    source = messages(3)
    source[1].parts = [ToolPart(tool_name="write", tool_status="completed")]
    context = ExtractContext(source)
    assert context.get_event_content("", summary) == (summary or "")
    assert context.get_event_content("1", summary) == (
        summary if summary and summary.strip() else ""
    )


@pytest.mark.parametrize("ranges", ["-1", "99", "0-99", "1,", "3-1"])
@pytest.mark.parametrize("padding", [0, 1, 2])
def test_event_rendering_rejects_invalid_original_ranges(ranges, padding):
    from openviking.session.memory.memory_updater import EventRangePaddingError

    with pytest.raises(EventRangePaddingError):
        ExtractContext(messages(3)).get_event_content(ranges, "Summary", 0, range_padding=padding)


def test_explicit_chunk_context_pads_original_messages():
    source = messages(3)
    source[1].parts = [TextPart(text="Long source message. " * 40)]
    context = ExtractContext(source)
    restored = ExtractContext(context.messages, chunk_meta=context.chunk_meta)
    assert restored.expand_message_ranges("2") == f"0-{len(context.messages) - 1}"
    assert restored.get_event_content("2", "", 0) == context.get_event_content("2", "", 0)


@pytest.mark.parametrize("failure", ["duplicates", "reordered", "missing"])
def test_inconsistent_original_message_mapping_is_rejected(failure):
    source = messages(3)
    if failure == "duplicates":
        source[2].id = source[0].id
    context = ExtractContext(source)
    if failure == "reordered":
        context.messages.reverse()
    elif failure == "missing":
        context.messages.pop()
    with pytest.raises(ValueError):
        context.expand_message_ranges("0")


@pytest.mark.asyncio
@pytest.mark.parametrize("account_template", [False, True])
async def test_padding_error_propagates_before_any_write_or_delete(account_template):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openviking.server.identity import RequestContext, Role
    from openviking.session.memory.dataclass import (
        MemoryFile,
        ResolvedOperation,
        ResolvedOperations,
    )
    from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
    from openviking.session.memory.memory_updater import EventRangePaddingError, MemoryUpdater
    from openviking_cli.session.user_id import UserIdentifier

    registry = MemoryTypeRegistry()
    schema = registry.get("events")
    if account_template:
        schema.content_template = (
            "{{ extract_context.get_event_content(ranges, summary, 0, range_padding=1) }}"
        )
        schema._account_content_template = True
    source = messages(4)
    source[-1].peer_id = "other-peer"
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_type="events",
                uris=[f"viking://user/alice/memories/events/event_{index}.md"],
                memory_fields={
                    "ranges": str(index),
                    "summary": "Summary",
                    "event_name": f"event_{index}",
                },
            )
            for index in (0, 2)  # The first is safe; the second would include another peer.
        ],
        delete_file_contents=[
            MemoryFile(uri="viking://user/alice/memories/events/old.md", memory_type="events")
        ],
        errors=[],
    )
    fs = SimpleNamespace(read_file=AsyncMock(side_effect=FileNotFoundError), write_file=AsyncMock())
    updater = MemoryUpdater(registry=registry)
    updater._viking_fs = fs
    updater._apply_delete = AsyncMock()
    before = operations.model_dump()
    ctx = RequestContext(user=UserIdentifier("default", "alice"), role=Role.ROOT)
    with pytest.raises(EventRangePaddingError, match="memory-owner boundaries"):
        await updater.apply_operations(operations, ctx, extract_context=ExtractContext(source))
    fs.write_file.assert_not_awaited()
    updater._apply_delete.assert_not_awaited()
    assert operations.model_dump() == before


def test_range_padding_is_optional_and_keyword_only():
    from inspect import Parameter, signature

    parameter = signature(ExtractContext.get_event_content).parameters["range_padding"]
    assert parameter.kind is Parameter.KEYWORD_ONLY
    assert parameter.default == 1
