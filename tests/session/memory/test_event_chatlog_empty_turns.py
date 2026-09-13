# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""A turn that carries no text must not become a bare speaker line.

``MessageRange._format_contiguous_group`` guarded on "are there messages", not on "did
they produce any text". A tool-call-only turn carries no ``TextPart``, so the merged
content is ``""`` and the ChatLog got ``**speaker**: `` with nothing after it.

That reaches two places: the stored event body, and the vector — ``events.yaml``'s
``embedding_template`` embeds the same ``content``. Reported in #3598, where 112 of 203
turns across 17 event documents were empty.
"""

import pytest

from openviking.message import Message
from openviking.message.part import TextPart
from openviking.session.memory.memory_updater import MessageRange


def _msg(role: str, text: str | None, msg_id: str) -> Message:
    return Message(
        id=msg_id,
        role=role,
        parts=[TextPart(text=text)] if text is not None else [],
        created_at="2026-07-29T10:00:00Z",
    )


def _lines(*messages: Message) -> list[str]:
    return MessageRange([list(messages)]).pretty_print().splitlines()


def test_a_turn_with_no_text_is_dropped():
    lines = _lines(_msg("user", "hello", "m1"), _msg("assistant", None, "m2"))

    assert lines == ["**user**: hello"]


def test_a_whitespace_only_turn_is_dropped():
    lines = _lines(_msg("user", "hello", "m1"), _msg("assistant", "   \n ", "m2"))

    assert lines == ["**user**: hello"]


def test_turns_with_text_are_untouched():
    lines = _lines(_msg("user", "hello", "m1"), _msg("assistant", "hi there", "m2"))

    assert lines == ["**user**: hello", "**assistant**: hi there"]


def test_an_all_empty_range_produces_no_lines():
    assert _lines(_msg("assistant", None, "m1"), _msg("assistant", None, "m2")) == []


@pytest.mark.parametrize("text", ["0", "false", " x "])
def test_falsy_looking_but_real_text_is_kept(text):
    # The guard has to test emptiness, not truthiness: "0" is content.
    assert _lines(_msg("user", text, "m1")) == [f"**user**: {text}"]
