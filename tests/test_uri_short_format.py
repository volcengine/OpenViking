# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for VikingURI short-format path support (Issue #259)."""

import pytest

from openviking_cli.utils.uri import VikingURI


class TestVikingURIShortFormat:
    """Test that VikingURI accepts short-format paths like '/resources'."""

    def test_short_format_with_leading_slash(self):
        uri = VikingURI("/resources")
        assert uri.uri == "viking://resources"
        assert uri.scope == "resources"

    def test_short_format_without_leading_slash(self):
        uri = VikingURI("resources")
        assert uri.uri == "viking://resources"
        assert uri.scope == "resources"

    def test_short_format_with_path(self):
        uri = VikingURI("/resources/my_project/docs")
        assert uri.uri == "viking://resources/my_project/docs"
        assert uri.scope == "resources"

    def test_short_format_user_scope(self):
        uri = VikingURI("/user/memories")
        assert uri.uri == "viking://user/memories"
        assert uri.scope == "user"

    def test_short_format_agent_scope(self):
        uri = VikingURI("agent/skills")
        assert uri.uri == "viking://agent/skills"
        assert uri.scope == "agent"

    def test_full_format_unchanged(self):
        uri = VikingURI("viking://resources/my_project")
        assert uri.uri == "viking://resources/my_project"
        assert uri.scope == "resources"

    def test_invalid_scope_still_raises(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            VikingURI("/invalid_scope/path")


class TestVikingURINormalize:
    """Test the normalize static method."""

    def test_normalize_full_uri(self):
        assert VikingURI.normalize("viking://resources") == "viking://resources"

    def test_normalize_leading_slash(self):
        assert VikingURI.normalize("/resources/images") == "viking://resources/images"

    def test_normalize_no_slash(self):
        assert VikingURI.normalize("resources/images") == "viking://resources/images"

    def test_normalize_multiple_leading_slashes(self):
        assert VikingURI.normalize("///resources") == "viking://resources"
