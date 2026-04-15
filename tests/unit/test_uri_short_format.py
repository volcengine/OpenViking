# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VikingURI short-format URI normalization.

Verifies that VikingURI accepts short-format paths (e.g., '/resources',
'user/memories') in addition to full-format URIs ('viking://resources').

Ref: https://github.com/volcengine/OpenViking/issues/259
"""

import pytest

from openviking_cli.utils.uri import VikingURI


class TestVikingURIShortFormat:
    """VikingURI should accept and auto-normalize short-format URIs."""

    def test_slash_prefix_path(self):
        """'/resources' should be normalized to 'viking://resources'."""
        uri = VikingURI("/resources")
        assert uri.uri == "viking://resources"
        assert uri.scope == "resources"

    def test_bare_path(self):
        """'resources' should be normalized to 'viking://resources'."""
        uri = VikingURI("resources")
        assert uri.uri == "viking://resources"
        assert uri.scope == "resources"

    def test_slash_prefix_nested(self):
        """'/user/memories/preferences' should normalize correctly."""
        uri = VikingURI("/user/memories/preferences")
        assert uri.uri == "viking://user/memories/preferences"
        assert uri.scope == "user"

    def test_bare_nested_path(self):
        """'agent/skills/pdf' should normalize correctly."""
        uri = VikingURI("agent/skills/pdf")
        assert uri.uri == "viking://agent/skills/pdf"
        assert uri.scope == "agent"

    def test_full_format_unchanged(self):
        """Full-format URIs should pass through unchanged."""
        uri = VikingURI("viking://resources/my_project")
        assert uri.uri == "viking://resources/my_project"

    def test_root_slash(self):
        """'/' should normalize to 'viking://'."""
        uri = VikingURI("/")
        assert uri.uri == "viking://"
        assert uri.scope == ""

    def test_full_root(self):
        """'viking://' should work as before."""
        uri = VikingURI("viking://")
        assert uri.uri == "viking://"
        assert uri.scope == ""

    def test_join_after_short_format(self):
        """join() should work on auto-normalized URIs."""
        uri = VikingURI("/resources")
        joined = uri.join("my_project")
        assert joined.uri == "viking://resources/my_project"

    def test_parent_after_short_format(self):
        """parent should work on auto-normalized URIs."""
        uri = VikingURI("/user/memories/preferences")
        parent = uri.parent
        assert parent is not None
        assert parent.uri == "viking://user/memories"

    def test_is_valid_short_format(self):
        """is_valid should accept short-format URIs after normalization."""
        assert VikingURI.is_valid("/resources")
        assert VikingURI.is_valid("user/memories")

    def test_invalid_scope_still_rejected(self):
        """Invalid scopes should still raise ValueError."""
        with pytest.raises(ValueError, match="Invalid scope"):
            VikingURI("/invalid_scope/foo")

    def test_normalize_idempotent(self):
        """Normalizing an already-normalized URI should be idempotent."""
        original = "viking://resources/docs"
        assert VikingURI.normalize(original) == original
        assert (
            VikingURI.normalize(VikingURI.normalize("/resources/docs")) == "viking://resources/docs"
        )

    @pytest.mark.parametrize(
        "short,expected",
        [
            ("/resources", "viking://resources"),
            ("/user", "viking://user"),
            ("/agent/skills", "viking://agent/skills"),
            ("/session/abc123", "viking://session/abc123"),
            ("/queue", "viking://queue"),
            ("/temp", "viking://temp"),
            ("resources/images", "viking://resources/images"),
        ],
    )
    def test_all_scopes(self, short, expected):
        """All valid scopes should work with short format."""
        uri = VikingURI(short)
        assert uri.uri == expected
