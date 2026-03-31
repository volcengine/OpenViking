"""Tests for memory scope_mode isolation behavior."""

import pytest
from pydantic import ValidationError

from openviking.server.identity import RequestContext, Role
from openviking.session.memory_extractor import MemoryCategory, MemoryExtractor
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.memory_config import MemoryConfig


class TestGetOwnerSpaceScopeMode:
    """Verify _get_owner_space routes categories based on scope_mode."""

    def _make_ctx(self) -> RequestContext:
        user = UserIdentifier("acct", "alice", "bot1")
        return RequestContext(user=user, role=Role.USER)

    def test_default_profile_returns_user_space(self):
        """scope_mode='default': PROFILE belongs to user space."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PROFILE, ctx, scope_mode="default"
        )
        assert result == ctx.user.user_space_name()

    def test_default_preferences_returns_user_space(self):
        """scope_mode='default': PREFERENCES belongs to user space."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PREFERENCES, ctx, scope_mode="default"
        )
        assert result == ctx.user.user_space_name()

    def test_default_cases_returns_agent_space(self):
        """scope_mode='default': CASES belongs to agent space."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.CASES, ctx, scope_mode="default"
        )
        assert result == ctx.user.agent_space_name()

    def test_isolated_profile_returns_agent_space(self):
        """scope_mode='isolated': PROFILE is routed to agent space."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PROFILE, ctx, scope_mode="isolated"
        )
        assert result == ctx.user.agent_space_name()

    def test_isolated_preferences_returns_agent_space(self):
        """scope_mode='isolated': PREFERENCES is routed to agent space."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.PREFERENCES, ctx, scope_mode="isolated"
        )
        assert result == ctx.user.agent_space_name()

    def test_isolated_cases_returns_agent_space(self):
        """scope_mode='isolated': CASES stays in agent space (same as default)."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.CASES, ctx, scope_mode="isolated"
        )
        assert result == ctx.user.agent_space_name()

    def test_default_events_returns_user_space(self):
        """scope_mode='default': EVENTS belongs to user space."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.EVENTS, ctx, scope_mode="default"
        )
        assert result == ctx.user.user_space_name()

    def test_isolated_events_returns_agent_space(self):
        """scope_mode='isolated': EVENTS is routed to agent space."""
        ctx = self._make_ctx()
        result = MemoryExtractor._get_owner_space(
            MemoryCategory.EVENTS, ctx, scope_mode="isolated"
        )
        assert result == ctx.user.agent_space_name()


class TestMemoryConfigScopeModeValidation:
    """Verify MemoryConfig validates scope_mode values."""

    def test_default_value(self):
        """Default scope_mode should be 'default'."""
        config = MemoryConfig()
        assert config.scope_mode == "default"

    def test_valid_default(self):
        """scope_mode='default' is accepted."""
        config = MemoryConfig(scope_mode="default")
        assert config.scope_mode == "default"

    def test_valid_isolated(self):
        """scope_mode='isolated' is accepted."""
        config = MemoryConfig(scope_mode="isolated")
        assert config.scope_mode == "isolated"

    def test_invalid_value_raises(self):
        """Invalid scope_mode value raises ValidationError."""
        with pytest.raises(ValidationError, match="scope_mode"):
            MemoryConfig(scope_mode="invalid")

    def test_invalid_value_per_agent_raises(self):
        """A plausible but unsupported value raises ValidationError."""
        with pytest.raises(ValidationError, match="scope_mode"):
            MemoryConfig(scope_mode="per-agent")
