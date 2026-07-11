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
