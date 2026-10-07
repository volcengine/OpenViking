# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.utils.line_numbers import (
    add_line_numbers,
    every_line_has_line_numbers,
    extract_start_line_number,
    strip_line_numbers,
)


class TestAddLineNumbers:
    def test_numbers_plain_content(self):
        assert add_line_numbers("alpha\nbeta") == "1\talpha\n2\tbeta"

    def test_respects_start_line(self):
        assert add_line_numbers("alpha\nbeta", start_line=3) == "3\talpha\n4\tbeta"

    def test_empty_content_returns_empty(self):
        assert add_line_numbers("") == ""

    def test_does_not_renumber_already_numbered_content(self):
        """Numbering already-numbered content must not stack prefixes (#4413)."""
        numbered = add_line_numbers("alpha\nbeta")
        assert add_line_numbers(numbered) == numbered


class TestStripLineNumbers:
    def test_strips_single_prefix(self):
        assert strip_line_numbers("1\talpha\n2\tbeta") == "alpha\nbeta"

    def test_strips_all_accumulated_prefixes(self):
        """Merged memories can carry several stacked prefixes (#4413)."""
        assert strip_line_numbers("1\t1\t## Title\n2\t2\t- fact one") == (
            "## Title\n- fact one"
        )

    def test_strip_is_idempotent(self):
        content = "1\t1\t1\t## Title\n2\t2\t2\t- fact one"
        once = strip_line_numbers(content)
        assert once == "## Title\n- fact one"
        assert strip_line_numbers(once) == once

    def test_round_trips_stacked_add_on_plain_content(self):
        plain = "## Title\n- fact one"
        stacked = "1\t1\t## Title\n2\t2\t- fact one"  # add_line_numbers applied twice
        assert strip_line_numbers(stacked) == plain

    def test_plain_content_unchanged(self):
        assert strip_line_numbers("alpha\nbeta") == "alpha\nbeta"

    def test_keeps_inner_tabs_after_prefix(self):
        assert strip_line_numbers("1\talpha\tkeeps inner tabs") == "alpha\tkeeps inner tabs"

    def test_non_aggressive_keeps_leading_whitespace(self):
        assert strip_line_numbers(" 1\talpha") == " 1\talpha"

    def test_aggressive_strips_all_accumulated_prefixes_with_whitespace(self):
        assert strip_line_numbers(" 1\t 1\talpha", aggressive=True) == "alpha"


class TestEveryLineHasLineNumbers:
    def test_true_for_fully_numbered_content(self):
        assert every_line_has_line_numbers("1\ta\n2\tb") is True

    def test_true_for_content_with_accumulated_prefixes(self):
        assert every_line_has_line_numbers("1\t1\ta\n2\t2\tb") is True

    def test_false_for_plain_content(self):
        assert every_line_has_line_numbers("a\nb") is False

    def test_false_for_partially_numbered_content(self):
        assert every_line_has_line_numbers("1\ta\nb") is False

    def test_false_for_empty_content(self):
        assert every_line_has_line_numbers("") is False


class TestExtractStartLineNumber:
    def test_reads_first_prefix(self):
        assert extract_start_line_number("3\talpha\n4\tbeta") == 3

    def test_reads_first_prefix_from_accumulated_prefixes(self):
        assert extract_start_line_number("3\t3\talpha") == 3

    def test_none_for_plain_content(self):
        assert extract_start_line_number("alpha\nbeta") is None
