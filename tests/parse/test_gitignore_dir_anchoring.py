# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for _transform_gitignore_line directory-pattern anchoring.

A trailing-slash directory pattern (e.g. ``build/``) from a nested .gitignore
must remain unanchored (match anywhere under base_rel) instead of being scoped
directly to base_rel because the trailing ``/`` made it look like a path with
a separator.
"""

from openviking.parse.gitignore import _transform_gitignore_line


class TestTransformGitignoreDirAnchoring:
    def test_dir_only_pattern_stays_unanchored(self):
        """``build/`` must expand to ``src/**/build/`` (match anywhere), not ``src/build/``."""
        result = _transform_gitignore_line("build/", "src")
        assert result == "src/**/build/"

    def test_plain_pattern_without_slash_is_unanchored(self):
        """``build`` (no slash) matches anywhere under base_rel."""
        assert _transform_gitignore_line("build", "src") == "src/**/build"

    def test_leading_slash_pattern_is_anchored(self):
        """``/build`` is anchored directly to base_rel."""
        assert _transform_gitignore_line("/build", "src") == "src/build"

    def test_dir_only_with_leading_slash_stays_anchored(self):
        """``/build/`` stays anchored to base_rel but preserves the dir-only slash."""
        assert _transform_gitignore_line("/build/", "src") == "src/build/"

    def test_negated_dir_only_pattern_preserves_bang(self):
        """Negation prefix is preserved through the unanchored transform."""
        assert _transform_gitignore_line("!build/", "src") == "!src/**/build/"

    def test_nested_dir_pattern_with_internal_slash_stays_anchored(self):
        """``foo/bar/`` has an internal '/' so it should anchor to base_rel."""
        assert _transform_gitignore_line("foo/bar/", "src") == "src/foo/bar/"

    def test_multiple_trailing_slashes_match_nothing(self):
        """``build///`` matches nothing in git (empty trailing segment), so the
        transform emits an empty no-op pattern instead of inventing a match."""
        assert _transform_gitignore_line("build///", "src") == ""

    def test_double_star_dir_only(self):
        """``**/build/`` (a globstar directory pattern) remains unanchored."""
        assert _transform_gitignore_line("**/build/", "src") == "src/**/build/"

    def test_bare_slash_matches_nothing(self):
        """``/`` (just a slash) matches nothing in git; emit a no-op pattern."""
        assert _transform_gitignore_line("/", "src") == ""

    def test_double_slash_matches_nothing(self):
        """``//`` also has an empty body and matches nothing in git."""
        assert _transform_gitignore_line("//", "src") == ""

    def test_deeply_nested_base_rel(self):
        """Pattern from a .gitignore at ``a/b/c/`` must prefix correctly."""
        assert _transform_gitignore_line("build/", "a/b/c") == "a/b/c/**/build/"

    def test_root_base_rel_no_transform(self):
        """Empty base_rel returns the line unchanged."""
        assert _transform_gitignore_line("build/", "") == "build/"

    def test_comment_line_passed_through(self):
        """Comment lines are returned as-is regardless of content."""
        assert _transform_gitignore_line("# build/", "src") == "# build/"

    def test_negated_anchored_dir_only(self):
        """``!/build/`` negates an anchored directory-only pattern."""
        assert _transform_gitignore_line("!/build/", "src") == "!src/build/"

    def test_trailing_spaces_stripped_per_gitignore_spec(self):
        """``build/   `` -- trailing spaces are ignored (gitignore spec),
        stripping them yields a clean unanchored directory-only pattern."""
        result = _transform_gitignore_line("build/   ", "src")
        assert result == "src/**/build/"

    def test_escaped_trailing_space_preserved(self):
        """``foo\\ `` -- a backslash-escaped trailing space is part of the
        pattern (a file named ``foo ``) and must survive the transform.
        Stripping it would leave a dangling backslash, which pathspec rejects."""
        assert _transform_gitignore_line("foo\\ ", "src") == "src/**/foo\\ "

    def test_unescaped_spaces_after_escaped_space_trimmed(self):
        """``foo\\<sp><sp>`` -- only the escaped space survives; the trailing
        unescaped space is trimmed (mirrors git's trim_trailing_spaces)."""
        assert _transform_gitignore_line("foo\\  ", "src") == "src/**/foo\\ "

    def test_pattern_with_globstar_prefix(self):
        """``**/foo`` -- a globstar-prefixed non-dir pattern stays unanchored."""
        assert _transform_gitignore_line("**/foo", "src") == "src/**/foo"

    def test_negation_of_simple_pattern(self):
        """``!build`` (negation, no dir marker) should stay unanchored."""
        assert _transform_gitignore_line("!build", "src") == "!src/**/build"

    def test_whitespace_only_line_returns_empty(self):
        """A line with only spaces produces an empty (no-op) pattern."""
        assert _transform_gitignore_line("   ", "src") == ""
