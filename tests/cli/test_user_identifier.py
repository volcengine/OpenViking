"""Tests for account/user-only UserIdentifier."""

from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OpenVikingConfigSingleton


class TestUserIdentifier:
    """Verify that UserIdentifier is keyed by account/user only."""

    def test_same_user_produces_same_space(self):
        u1 = UserIdentifier("acct", "alice")
        u2 = UserIdentifier("acct", "alice")
        assert u1.user_space_name() == u2.user_space_name()
        assert u1 == u2

    def test_different_users_produce_different_spaces(self):
        u1 = UserIdentifier("acct", "alpha")
        u2 = UserIdentifier("acct", "beta")
        assert u1.user_space_name() != u2.user_space_name()

    def test_memory_space_uri_uses_user_space(self):
        u = UserIdentifier("acct", "user1")
        assert u.user_space_name() == "user1"
        assert u.memory_space_uri() == "viking://user/user1/memories"
        assert u.to_dict() == {"account_id": "acct", "user_id": "user1"}

    def test_agent_scope_mode_is_ignored(self):
        """Deprecated memory.agent_scope_mode no longer affects user space."""
        OpenVikingConfigSingleton.reset_instance()
        OpenVikingConfigSingleton.initialize(config_dict={"memory": {"agent_scope_mode": "agent"}})

        u1 = UserIdentifier("acct", "alice")
        u2 = UserIdentifier("acct", "bob")
        assert u1.user_space_name() != u2.user_space_name()

        OpenVikingConfigSingleton.reset_instance()
