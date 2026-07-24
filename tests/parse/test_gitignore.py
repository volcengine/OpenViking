# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for gitignore-aware matching helpers."""

from pathlib import Path

from openviking.parse.gitignore import GitignoreMatcher, _transform_gitignore_line


class TestTransformGitignoreLine:
    """Directory-only patterns (trailing '/') must not be treated as anchored."""

    def test_trailing_slash_only_pattern_matches_any_depth(self) -> None:
        # A bare "build/" has no beginning/middle separator, so per git it should
        # match a `build` directory at any depth below the nested .gitignore.
        assert _transform_gitignore_line("build/", "pkg") == "pkg/**/build/"

    def test_no_slash_pattern_matches_any_depth(self) -> None:
        assert _transform_gitignore_line("*.tmp", "pkg") == "pkg/**/*.tmp"

    def test_middle_slash_pattern_is_anchored(self) -> None:
        assert _transform_gitignore_line("logs/app.log", "pkg") == "pkg/logs/app.log"

    def test_leading_slash_pattern_is_anchored(self) -> None:
        assert _transform_gitignore_line("/build", "pkg") == "pkg/build"

    def test_negated_trailing_slash_pattern_matches_any_depth(self) -> None:
        assert _transform_gitignore_line("!build/", "pkg") == "!pkg/**/build/"


class TestNestedGitignoreDirectoryPattern:
    """A directory pattern in a nested .gitignore must match at any depth below it."""

    def test_directory_pattern_matches_direct_and_nested(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / ".gitignore").write_text("build/\n", encoding="utf-8")

        direct = tmp_path / "pkg" / "build"
        direct.mkdir()
        nested = tmp_path / "pkg" / "nested" / "build"
        nested.mkdir(parents=True)

        matcher = GitignoreMatcher(tmp_path)

        # Direct child pkg/build is pruned (spec of its parent dir "pkg").
        spec_pkg = matcher.spec_for_dir(tmp_path / "pkg")
        assert matcher.is_ignored_dir(direct, spec_pkg)

        # Deeply-nested pkg/nested/build must also be pruned; this is the
        # regression: previously "build/" was rewritten to the anchored
        # "pkg/build/" and never matched pkg/nested/build.
        spec_nested = matcher.spec_for_dir(tmp_path / "pkg" / "nested")
        assert matcher.is_ignored_dir(nested, spec_nested)
