"""Tests for UserIdentifier, specifically agent_space_name collision safety."""

from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OpenVikingConfigSingleton


class TestAgentSpaceNameCollision:
    """Verify that agent_space_name uses a separator to prevent hash collisions."""

    def test_different_pairs_produce_different_hashes(self):
        """Pairs like (alice, bot) vs (aliceb, ot) must not collide."""
        u1 = UserIdentifier("acct", "alice", "bot")
        u2 = UserIdentifier("acct", "aliceb", "ot")
        assert u1.agent_space_name() != u2.agent_space_name()

    def test_same_pair_produces_same_hash(self):
        """Same (user_id, agent_id) must always produce the same hash."""
        u1 = UserIdentifier("acct", "alice", "bot")
        u2 = UserIdentifier("acct", "alice", "bot")
        assert u1.agent_space_name() == u2.agent_space_name()

    def test_swapped_ids_produce_different_hashes(self):
        """(user_id=a, agent_id=b) vs (user_id=b, agent_id=a) must differ."""
        u1 = UserIdentifier("acct", "alpha", "beta")
        u2 = UserIdentifier("acct", "beta", "alpha")
        assert u1.agent_space_name() != u2.agent_space_name()

    def test_hash_length(self):
        """agent_space_name must return a 12-character hex string."""
        u = UserIdentifier("acct", "user1", "agent1")
        name = u.agent_space_name()
        assert len(name) == 12
        assert all(c in "0123456789abcdef" for c in name)

    def test_agent_scope_mode_is_ignored(self):
        """Deprecated memory.agent_scope_mode no longer changes agent_space_name."""
        OpenVikingConfigSingleton.reset_instance()
        OpenVikingConfigSingleton.initialize(config_dict={"memory": {"agent_scope_mode": "agent"}})

        u1 = UserIdentifier("acct", "alice", "bot")
        u2 = UserIdentifier("acct", "bob", "bot")
        assert u1.agent_space_name() != u2.agent_space_name()

        OpenVikingConfigSingleton.reset_instance()
