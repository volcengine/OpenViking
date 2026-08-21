# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the explicit Viking URI contract."""

import pytest

from openviking_cli.utils.uri import VikingURI


class TestVikingURIExplicitFormat:
    """VikingURI only accepts explicit ``viking://`` values."""

    @pytest.mark.parametrize(
        "value",
        [
            "/resources",
            "resources",
            "/user/memories/preferences",
            "user/skills/pdf",
            "/",
        ],
    )
    def test_path_input_is_rejected(self, value: str):
        with pytest.raises(ValueError, match="must start with 'viking://'"):
            VikingURI(value)

    def test_full_format_unchanged(self):
        uri = VikingURI("viking://resources/my_project")
        assert uri.uri == "viking://resources/my_project"

    def test_full_root(self):
        uri = VikingURI("viking://")
        assert uri.scope == ""

    def test_join(self):
        joined = VikingURI("viking://resources").join("my_project")
        assert joined.uri == "viking://resources/my_project"

    def test_parent(self):
        parent = VikingURI("viking://user/alice/memories/preferences").parent
        assert parent is not None
        assert parent.uri == "viking://user/alice/memories"

    def test_is_valid_rejects_path_input(self):
        assert not VikingURI.is_valid("/resources")
        assert not VikingURI.is_valid("user/memories")

    def test_invalid_scope_still_rejected(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            VikingURI("viking://invalid_scope/foo")

    def test_home_alias_is_parsable(self):
        """'viking://~' should parse with the home alias scope."""
        uri = VikingURI("viking://~")
        assert uri.uri == "viking://~"
        assert uri.scope == "~"

    def test_home_alias_with_suffix_is_parsable(self):
        """'viking://~/notes' keeps the alias scope and its suffix."""
        uri = VikingURI("viking://~/notes")
        assert uri.uri == "viking://~/notes"
        assert uri.scope == "~"
        assert uri.full_path == "~/notes"

    def test_invalid_scope_error_copy_hides_home_alias(self):
        """The parser's own scope enumeration never advertises the alias."""
        with pytest.raises(ValueError, match="Invalid scope") as exc_info:
            VikingURI("viking://invalid_scope/foo")
        message = str(exc_info.value)
        assert "Must be one of:" in message
        assert "~" not in message.split("Must be one of:", 1)[1]

    def test_build_rejects_home_alias_scope(self):
        """build() must never mint an alias URI: responses stay canonical."""
        with pytest.raises(ValueError, match="Invalid scope"):
            VikingURI.build("~")
        with pytest.raises(ValueError, match="Invalid scope"):
            VikingURI.build("~", "notes")
        assert VikingURI.build("user", "alice") == "viking://user/alice"

    @pytest.mark.parametrize(
        "value,scope",
        [
            ("viking://resources", "resources"),
            ("viking://user", "user"),
            ("viking://session/abc123", "session"),
            ("viking://queue", "queue"),
            ("viking://temp", "temp"),
        ],
    )
    def test_explicit_scopes(self, value: str, scope: str):
        assert VikingURI(value).scope == scope
