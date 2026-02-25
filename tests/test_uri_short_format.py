# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for short-format URI parsing support.

Verifies that both VikingURI and VikingFS._uri_to_path correctly handle
short-format paths (e.g., '/resources') in addition to full viking:// URIs.

Related issue: https://github.com/volcengine/OpenViking/issues/259
"""

import pytest

from openviking_cli.utils.uri import VikingURI


class TestVikingURIShortFormat:
    """Test VikingURI accepts and normalizes short-format paths."""

    def test_slash_resources(self):
        uri = VikingURI("/resources")
        assert uri.scope == "resources"
        assert uri.uri == "viking://resources"

    def test_bare_resources(self):
        uri = VikingURI("resources")
        assert uri.scope == "resources"
        assert uri.uri == "viking://resources"

    def test_slash_user_memories(self):
        uri = VikingURI("/user/memories")
        assert uri.scope == "user"
        assert uri.full_path == "user/memories"

    def test_bare_agent_skills(self):
        uri = VikingURI("agent/skills")
        assert uri.scope == "agent"
        assert uri.full_path == "agent/skills"

    def test_full_uri_unchanged(self):
        uri = VikingURI("viking://resources/my_project")
        assert uri.uri == "viking://resources/my_project"
        assert uri.scope == "resources"

    def test_invalid_scope_still_raises(self):
        with pytest.raises(ValueError, match="Invalid scope"):
            VikingURI("/invalid_scope/path")

    def test_normalize_static_method(self):
        assert VikingURI.normalize("/resources") == "viking://resources"
        assert VikingURI.normalize("resources") == "viking://resources"
        assert VikingURI.normalize("viking://resources") == "viking://resources"
        assert VikingURI.normalize("/user/memories") == "viking://user/memories"


class TestUriToPathShortFormat:
    """Test VikingFS._uri_to_path handles short-format paths."""

    @pytest.fixture
    def viking_fs(self):
        """Create a minimal VikingFS-like object for testing _uri_to_path."""
        from openviking.storage.viking_fs import VikingFS

        class MinimalFS(VikingFS):
            def __init__(self):
                # Skip parent __init__ to avoid needing AGFS
                pass

        return MinimalFS()

    def test_full_uri(self, viking_fs):
        assert viking_fs._uri_to_path("viking://resources") == "/local/resources"

    def test_full_uri_nested(self, viking_fs):
        assert viking_fs._uri_to_path("viking://user/memories/preferences") == "/local/user/memories/preferences"

    def test_slash_resources(self, viking_fs):
        """The original bug: /resources was converted to /local/s instead of /local/resources."""
        assert viking_fs._uri_to_path("/resources") == "/local/resources"

    def test_slash_user_memories(self, viking_fs):
        assert viking_fs._uri_to_path("/user/memories") == "/local/user/memories"

    def test_bare_path(self, viking_fs):
        assert viking_fs._uri_to_path("resources/images") == "/local/resources/images"

    def test_root_uri(self, viking_fs):
        assert viking_fs._uri_to_path("viking://") == "/local"

    def test_slash_only(self, viking_fs):
        assert viking_fs._uri_to_path("/") == "/local"

    def test_empty_string(self, viking_fs):
        assert viking_fs._uri_to_path("") == "/local"
