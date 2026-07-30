# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit regressions for the four event-sum / recall defects from issue #3598.

The issue describes defects #1..#4 that compound to eat nearly the entire
``max_chars`` recall budget on verbatim chat transcripts (including blank
speaker lines from tool-call-only turns). Defect #2 (fallback fragment type
budget charging) was corrected by the reporter as an intentional design call
(summary-mode fallbacks are charged only to the shared pool because the
summary-only fragment still degrades further to URI at the end of the
pipeline), so this file only exercises defects #1, #3, #4.
"""

from __future__ import annotations

import pytest


class TestExtractEventSummaryAnchorMatch:
    """Defect #1 + comment correction: regex didn't match the current template output.

    The extractor writes a markdown heading (``# Summary``) and the terminator
    line is also a markdown heading (``# YYYY-MM-DD (Weekday) ChatLog:``).
    The old regex expected a field form (``Summary:`` followed by a bare date
    line with no heading marker), so neither anchor nor terminator matched,
    and ``fallback`` (the full stripped document) was always returned. The
    template also has a resource-event content branch that has no ChatLog at
    all, so end-of-string must terminate the capture for that shape.
    """

    @pytest.mark.parametrize(
        ("heading", "terminator", "expected"),
        [
            # The exact template shape that caused the original bug report
            (
                "# Summary\nShipped the thing.\n",
                "# 2026-07-24 (Friday) ChatLog:\n",
                "Shipped the thing.",
            ),
            # Legacy "Summary:" field form, any of the three terminator options
            (
                "Summary: Planned agenda.\n",
                "2026-07-24 ChatLog:\n",
                "Planned agenda.",
            ),
            # Bare ChatLog: terminator (no date) with heading marker
            (
                "# Summary\nA short summary.\n",
                "# ChatLog:\n",
                "A short summary.",
            ),
            # MEMORY_FIELDS sentinel terminator (metadata block form)
            (
                "# Summary\nMeta summary line.\n",
                "<!-- MEMORY_FIELDS {\"k\":\"v\"} -->\n",
                "Meta summary line.",
            ),
            # Summary-only shape (resource-event branch) — end-of-string terminator
            (
                "# Summary\nSingle paragraph.\nSecond line of summary.\n",
                "",
                "Single paragraph. Second line of summary.",
            ),
            # Whitespace + heading number variations are tolerated on both sides
            (
                "  ##  Summary :   Spaces.  \n",
                "  ### 2026-01-01 (Thu)  ChatLog: \n",
                "Spaces.",
            ),
        ],
    )
    def test_anchor_and_terminators_match_all_writer_shapes(
        self, heading: str, terminator: str, expected: str
    ) -> None:
        from openviking.retrieve.type_quota_recall import _extract_event_summary

        doc = heading + terminator + "irrelevant conversation lines here."
        got = _extract_event_summary(doc, fallback=doc)
        assert got == expected, f"mismatch for doc={doc!r}"

    def test_fallback_returned_when_no_summary_shape_present(self) -> None:
        from openviking.retrieve.type_quota_recall import _extract_event_summary

        fallback = "Random raw text that has no Summary heading at all."
        assert _extract_event_summary(fallback, fallback=fallback) == fallback
        assert _extract_event_summary("", fallback=fallback) == fallback

    def test_reporter_minimal_example_from_issue_body(self) -> None:
        from openviking.retrieve.type_quota_recall import _extract_event_summary

        # Exact scenario reproduced verbatim from issue minimal repro
        doc = (
            "# Summary\n"
            "Shipped the thing.\n"
            "# 2026-07-24 (Friday) ChatLog:\n"
            "**alice**: did we ship?\n"
            "**alice**: \n"
        )
        assert _extract_event_summary(doc, fallback=doc) == "Shipped the thing."


class TestGetEventContentDefaultRatio:
    """Defect #3: the single template call-site hardcoded ``ratio_threshold=0``.

    With ``0`` the ``len(summary)/len(original) >= 0`` check always passes,
    so the function would return the full pretty-printed transcript every
    time, never the summary. The template now drops the third argument so
    the method's own default (``0.2``) kicks in, which is the original
    intent. This test guards the default value at the function boundary, so
    a future regression that re-adds the literal ``0`` on the template side
    will still get caught by a reviewer comparing the numeric literals here.
    """

    def test_method_default_ratio_is_0pt2_not_zero(self) -> None:
        import inspect

        from openviking.session.memory.memory_updater import ExtractContext

        sig = inspect.signature(ExtractContext.get_event_content)
        default = sig.parameters["ratio_threshold"].default
        assert default == 0.2, (
            f"Expected ratio_threshold default to be 0.2 (summary must be "
            f"under a 5x size gain), got {default!r}. Zero would short-circuit "
            f"the comparison and always return the full verbatim transcript."
        )

    def test_ratio_0pt2_returns_summary_when_it_saves_space(self) -> None:
        from unittest.mock import MagicMock

        from openviking.session.memory.memory_updater import ExtractContext

        class _FakeMsgRange:
            def __init__(self, s: str) -> None:
                self._s = s

            def pretty_print(self) -> str:
                return self._s

        ctx = ExtractContext.__new__(ExtractContext)
        ctx.read_message_ranges = lambda r, _s=_FakeMsgRange: _s(
            "x" * 500,  # original = 500 chars -> len(summary)/len(original)=0.04 < 0.2
        )  # type: ignore[attr-defined]

        # Summary is 4% the size of the original => should return the summary
        assert ctx.get_event_content("r", "Short summary text.", 0.2) == (
            "Short summary text."
        )

        # Summary is >= 0.2 => returns original (defect #3 path, ratio=0)
        ctx.read_message_ranges = lambda r, _s=_FakeMsgRange: _s(
            "x" * 100
        )  # type: ignore[attr-defined]
        assert ctx.get_event_content("r", "Short.", 0.2) == "x" * 100


class TestFormatContiguousGroupSkipsBlankTurns:
    """Defect #4: ``flush_current`` appended speaker lines even for empty content.

    A tool-call-only turn has no TextPart, so ``_format_merged_content``
    returns ``""`` and the rendered ChatLog gets a ``**speaker**: `` line
    (roughly 36 bytes of nothing). Across a real session this can easily
    account for 40%+ of the rendered bytes once tool calls dominate. The
    embedding template also mirrors the rendered transcript, so those
    blank lines pollute recall vectors as well.
    """

    def test_blank_text_message_group_returns_no_speaker_line(self) -> None:
        from openviking.message.message import Message
        from openviking.message.part import ToolCallPart, ToolPart
        from openviking.session.memory.memory_updater import ExtractContext

        ctx = ExtractContext.__new__(ExtractContext)
        tool_call_msg = Message(
            id="m1",
            role="assistant",
            parts=[
                ToolCallPart(tool_call_id="t1", tool_name="noop", arguments="{}")
            ],
        )
        tool_result_msg = Message(
            id="m2",
            role="tool",
            parts=[ToolPart(tool_call_id="t1", content='{"ok":true}')],
        )
        assert ctx._format_contiguous_group([tool_call_msg, tool_result_msg]) == []  # no TextParts

    def test_blank_then_text_mixture_preserves_only_text_turns(self) -> None:
        from openviking.message.message import Message
        from openviking.message.part import TextPart, ToolCallPart
        from openviking.session.memory.memory_updater import ExtractContext

        ctx = ExtractContext.__new__(ExtractContext)
        text_msg = Message(
            id="m1",
            role="user",
            peer_id="bob",
            parts=[TextPart(text="hello world")],
        )
        only_tool = Message(
            id="m2",
            role="assistant",
            parts=[
                ToolCallPart(tool_call_id="t1", tool_name="noop", arguments="{}")
            ],
        )
        lines = ctx._format_contiguous_group([text_msg, only_tool])
        # Only the user's line (bob) appears. The assistant tool-only turn is
        # skipped completely instead of emitting "**assistant**: ".
        assert lines == ["**bob**: hello world"]
