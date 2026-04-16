# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for memory.scope_mode configuration and owner_space routing."""

import pytest
from unittest.mock import MagicMock

from openviking.session.memory_extractor import MemoryExtractor, MemoryCategory
from openviking_cli.utils.config.memory_config import MemoryConfig


class TestGetOwnerSpace:
    """Test _get_owner_space routing with scope_mode."""

    def _make_ctx(self, user_space="user123", agent_space="agent456"):
        ctx = MagicMock()
        ctx.user.user_space_name.return_value = user_space
        ctx.user.agent_space_name.return_value = agent_space
        return ctx

    def test_default_mode_profile_goes_to_user_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PROFILE, ctx, scope_mode="default"
        )
        assert result == "user123"

    def test_default_mode_preferences_goes_to_user_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PREFERENCES, ctx, scope_mode="default"
        )
        assert result == "user123"

    def test_default_mode_events_goes_to_user_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.EVENTS, ctx, scope_mode="default"
        )
        assert result == "user123"

    def test_default_mode_cases_goes_to_agent_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.CASES, ctx, scope_mode="default"
        )
        assert result == "agent456"

    def test_default_mode_patterns_goes_to_agent_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PATTERNS, ctx, scope_mode="default"
        )
        assert result == "agent456"

    def test_isolated_mode_profile_goes_to_agent_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PROFILE, ctx, scope_mode="isolated"
        )
        assert result == "agent456"

    def test_isolated_mode_preferences_goes_to_agent_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PREFERENCES, ctx, scope_mode="isolated"
        )
        assert result == "agent456"

    def test_isolated_mode_cases_still_goes_to_agent_space(self):
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.CASES, ctx, scope_mode="isolated"
        )
        assert result == "agent456"

    def test_no_scope_mode_defaults_to_default_behavior(self):
        """When scope_mode is omitted, should behave like 'default'."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(MemoryCategory.PROFILE, ctx)
        assert result == "user123"


class TestMemoryConfigScopeMode:
    """Test MemoryConfig scope_mode validation."""

    def test_default_scope_mode(self):
        config = MemoryConfig()
        assert config.scope_mode == "default"

    def test_valid_scope_mode_default(self):
        config = MemoryConfig(scope_mode="default")
        assert config.scope_mode == "default"

    def test_valid_scope_mode_isolated(self):
        config = MemoryConfig(scope_mode="isolated")
        assert config.scope_mode == "isolated"

    def test_invalid_scope_mode_raises(self):
        with pytest.raises(ValueError, match="scope_mode"):
            MemoryConfig(scope_mode="invalid")
