# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Fuzzy matching in the patch handler must stay cheap on long lines.

Field case: a memory file of 123 lines with one 41,742-character line and a 15,945-character
SEARCH block that does not match. The pure-Python Levenshtein ran the full matrix for every
candidate position and held the GIL for hours, stalling the whole server. These tests pin the
guards: the distance cutoff, the length-gap bound, exact behaviour at the threshold, and
unchanged results elsewhere.
"""

import time

import pytest

from openviking.session.memory.merge_op import patch_handler
from openviking.session.memory.merge_op.base import SearchReplaceBlock, StrPatch
from openviking.session.memory.merge_op.patch_handler import (
    PatchParseError,
    _find_best_substring_match,
    apply_str_patch,
    fuzzy_search,
    get_similarity,
    levenshtein_distance,
)


class TestLevenshteinDistance:
    def test_exact_value_without_cutoff(self):
        assert levenshtein_distance("kitten", "sitting") == 3
        assert levenshtein_distance("", "abc") == 3
        assert levenshtein_distance("abc", "") == 3
        assert levenshtein_distance("same", "same") == 0

    def test_cutoff_returns_exact_value_when_within(self):
        assert levenshtein_distance("kitten", "sitting", max_distance=3) == 3
        assert levenshtein_distance("kitten", "sitting", max_distance=10) == 3

    def test_cutoff_returns_cutoff_plus_one_when_exceeded(self):
        assert levenshtein_distance("kitten", "sitting", max_distance=2) == 3
        assert levenshtein_distance("a" * 50, "b" * 50, max_distance=5) == 6

    def test_cutoff_applies_to_empty_strings_in_both_orders(self):
        assert levenshtein_distance("abc", "", max_distance=1) == 2
        assert levenshtein_distance("", "abc", max_distance=1) == 2
        assert levenshtein_distance("abc", "", max_distance=3) == 3

    def test_length_gap_alone_exceeds_cutoff(self):
        assert levenshtein_distance("a" * 100, "a" * 10, max_distance=20) == 21

    def test_one_edit_on_long_strings_is_exact(self):
        a = "a" * 2001
        b = "a" * 2000 + "b"
        assert levenshtein_distance(a, b) == 1
        assert levenshtein_distance(a, b, max_distance=1) == 1


class TestGetSimilarity:
    def test_default_is_exact_as_before(self):
        assert get_similarity("hello world", "hello world") == 1.0
        # "world" versus "there": five substitutions over eleven characters
        assert get_similarity("hello world", "hello there") == pytest.approx(1 - 5 / 11)
        assert get_similarity("anything", "") == 0.0

    def test_min_score_keeps_scores_that_reach_it(self):
        score = get_similarity("hello world", "hello there", min_score=0.5)
        assert score == pytest.approx(1 - 5 / 11)

    def test_min_score_zeroes_scores_below_it(self):
        assert get_similarity("hello world", "hello there", min_score=0.8) == 0.0

    def test_score_exactly_on_threshold_is_kept(self):
        # distance 1 over 5 characters is exactly 0.8; int((1 - 0.8) * 5) would be 0
        assert get_similarity("abcde", "abXde") == pytest.approx(0.8)
        assert get_similarity("abcde", "abXde", min_score=0.8) == pytest.approx(0.8)
        # and one more edit is below it
        assert get_similarity("abcde", "aXXde", min_score=0.8) == 0.0

    def test_one_edit_on_long_strings_keeps_its_score(self):
        a = "a" * 2001
        b = "a" * 2000 + "b"
        assert get_similarity(a, b, min_score=0.8) == pytest.approx(2000 / 2001)

    def test_length_gap_bound_short_circuits(self, monkeypatch):
        # 16k search string against a 42k line: the best possible score is 1 - 26k/42k = 0.38,
        # so the distance must never be computed
        def forbidden(*_args, **_kwargs):
            raise AssertionError("levenshtein_distance must not run for a hopeless pair")

        monkeypatch.setattr(patch_handler, "levenshtein_distance", forbidden)
        assert get_similarity("x" * 42_000, "x" * 16_000, min_score=0.8) == 0.0

    def test_score_numerically_below_threshold_is_zero(self):
        # distance 4 over 5 is 0.19999999999999996 in floating point, below min_score 0.2
        # by the same comparison the callers make, so it must not be reported as a match
        assert get_similarity("a", "aaaaa", min_score=0.2) == 0.0
        assert get_similarity("a", "aaaaa") == pytest.approx(0.2)

    def test_length_gap_exactly_at_cutoff_still_computes(self):
        # gap of 1 on 5 characters with threshold 0.8: cutoff is 1, the pair is not ruled out
        assert get_similarity("abcd", "abcde", min_score=0.8) == pytest.approx(0.8)


class TestFuzzySearch:
    def test_finds_the_same_match_with_threshold(self):
        lines = ["alpha", "beta gamma delta", "epsilon", "zeta"]
        plain = fuzzy_search(lines, "beta gamma delts", 0, len(lines))
        gated = fuzzy_search(lines, "beta gamma delts", 0, len(lines), min_score=0.8)
        assert plain["bestMatchIndex"] == gated["bestMatchIndex"] == 1
        assert plain["bestScore"] == pytest.approx(gated["bestScore"])

    def test_candidate_equal_to_best_still_loses(self):
        # two identical candidates: the first one seen (middle-out from index 1 goes right
        # first, so index 2) must win, and a later tie must not displace it
        lines = ["beta gamma delts", "other", "beta gamma delts"]
        plain = fuzzy_search(lines, "beta gamma delta", 0, len(lines))
        gated = fuzzy_search(lines, "beta gamma delta", 0, len(lines), min_score=0.8)
        for result in (plain, gated):
            assert result["bestMatchIndex"] == 2
            assert result["bestScore"] == pytest.approx(0.9375)
            assert result["bestMatchContent"] == "beta gamma delts"

    def test_exact_substring_still_scores_one(self):
        lines = ["- The advance payment guarantee was submitted on 14 December 2022."]
        result = fuzzy_search(lines, "submitted on 14 December", 0, 1, min_score=0.8)
        assert result["bestScore"] == 1.0
        assert result["bestMatchIndex"] == 0

    def test_multi_line_chunk_with_threshold(self):
        lines = ["one", "two", "three", "four", "five"]
        plain = fuzzy_search(lines, "two\nthrea", 0, len(lines))
        gated = fuzzy_search(lines, "two\nthrea", 0, len(lines), min_score=0.8)
        assert plain["bestMatchIndex"] == gated["bestMatchIndex"] == 1
        assert plain["bestScore"] == pytest.approx(gated["bestScore"])

    def test_substring_match_threads_cutoff(self, monkeypatch):
        seen = []
        real = patch_handler.get_similarity

        def spy(original, search, min_score=0.0):
            seen.append(min_score)
            return real(original, search, min_score)

        monkeypatch.setattr(patch_handler, "get_similarity", spy)
        line = "prefix " + ("y" * 300) + " suffix"
        score, content = _find_best_substring_match(line, "z" * 300, min_score=0.9)
        assert score == 0.0
        assert content == ""
        # every comparison carried the caller's threshold (nothing beat it, so no higher bar)
        assert seen and all(cutoff == 0.9 for cutoff in seen)

    def test_fuzzy_search_raises_the_bar_as_matches_improve(self, monkeypatch):
        seen = []
        real = patch_handler._find_best_substring_match

        def spy(line, search_str, min_score=0.0):
            seen.append(min_score)
            return real(line, search_str, min_score)

        monkeypatch.setattr(patch_handler, "_find_best_substring_match", spy)
        lines = ["zzzz", "beta gamma delts", "zzzz", "zzzz"]
        fuzzy_search(lines, "beta gamma delta", 0, len(lines), min_score=0.5)
        # after the 0.9375 candidate at index 1 is seen, later comparisons carry that bar
        assert seen[0] == 0.5
        assert max(seen) == pytest.approx(0.9375)


class TestApplyStrPatch:
    """The field shape: one enormous line, a long SEARCH that does not match."""

    def _content(self):
        long_line = "- " + " ".join(
            f"round {i} returned FAIL on finding {i * 7}" for i in range(1200)
        )
        assert len(long_line) > 40_000
        return "\n".join(["# Murmur", "", "## Key Facts", "- short fact"] + [long_line] * 3)

    def test_no_match_fails_fast_instead_of_grinding(self):
        content = self._content()
        search = " ".join(f"gate {i} passed with note {i * 3}" for i in range(500))
        assert len(search) > 15_000
        patch = StrPatch(blocks=[SearchReplaceBlock(search=search, replace="- replaced")])
        start = time.perf_counter()
        with pytest.raises(PatchParseError):
            apply_str_patch(content, patch)
        # was hours; a generous bound so slow CI cannot fail a correct implementation
        assert time.perf_counter() - start < 30.0

    def test_near_match_on_long_line_still_applies(self):
        content = self._content()
        lines = content.split("\n")
        # a close variant of the whole long line (one token changed) must still be found
        search = lines[4].replace("round 17 returned", "round 17 returns")
        patch = StrPatch(blocks=[SearchReplaceBlock(search=search, replace="- collapsed")])
        result = apply_str_patch(content, patch)
        assert "- collapsed" in result

    def test_patch_exactly_at_threshold_still_applies(self):
        # one substitution in five characters scores exactly the 0.8 threshold
        content = "\n".join(["# Title", "abcde", "tail"])
        patch = StrPatch(blocks=[SearchReplaceBlock(search="abXde", replace="fixed")])
        result = apply_str_patch(content, patch)
        assert result == "\n".join(["# Title", "fixed", "tail"])

    def test_one_edit_on_long_line_applies(self):
        long_line = "a" * 2000 + "b"
        content = "\n".join(["# Title", long_line, "tail"])
        patch = StrPatch(blocks=[SearchReplaceBlock(search="a" * 2001, replace="fixed")])
        result = apply_str_patch(content, patch)
        assert result == "\n".join(["# Title", "fixed", "tail"])
