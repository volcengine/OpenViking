# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MemoryIsolationHandler.
"""

import pytest
from unittest.mock import MagicMock, patch

from openviking.message.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from openviking.session.memory.memory_isolation_handler import (
    MemoryIsolationHandler,
    RoleScope,
)


def create_message(role: str, role_id: str, content: str = "test") -> Message:
    """Helper to create a test message."""
    return Message(
        id=f"msg_{role}_{role_id}",
        role=role,
        parts=[TextPart(text=content)],
        role_id=role_id,
    )


def create_ctx(
    account_id: str = "test_account",
    user_id: str = "user_a",
    agent_id: str = "agent_a",
    isolate_user_by_agent: bool = False,
    isolate_agent_by_user: bool = False,
) -> RequestContext:
    """Helper to create a test RequestContext."""
    user = UserIdentifier(
        account_id=account_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    policy = AccountNamespacePolicy(
        isolate_user_scope_by_agent=isolate_user_by_agent,
        isolate_agent_scope_by_user=isolate_agent_by_user,
    )
    return RequestContext(user=user, role=Role.USER, namespace_policy=policy)


def create_mock_extract_context(messages):
    """Helper to create a mock ExtractContext."""
    mock_ctx = MagicMock()
    mock_ctx.messages = messages
    return mock_ctx


class TestLoadParticipants:
    """Tests for load_participants."""

    def test_single_user_single_agent(self):
        """Test extracting single user and agent."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Hi there"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.get_participant_user_ids() == ["user_a"]
        assert handler.get_participant_agent_ids() == ["agent_a"]

    def test_multiple_users(self):
        """Test extracting multiple users."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a", "Hello from A"),
            create_message("user", "user_b", "Hello from B"),
            create_message("assistant", "agent_a", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert set(handler.get_participant_user_ids()) == {"user_a", "user_b"}
        assert handler.get_participant_agent_ids() == ["agent_a"]

    def test_multiple_agents(self):
        """Test extracting multiple agents."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Response from A"),
            create_message("assistant", "agent_b", "Response from B"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.get_participant_user_ids() == ["user_a"]
        assert set(handler.get_participant_agent_ids()) == {"agent_a", "agent_b"}

    def test_deduplicate_users(self):
        """Test that duplicate users are deduplicated."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a", "First message"),
            create_message("user", "user_a", "Second message"),
            create_message("user", "user_a", "Third message"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.get_participant_user_ids() == ["user_a"]

    def test_empty_messages_uses_ctx_defaults(self):
        """Test that empty messages fall back to ctx defaults."""
        ctx = create_ctx(user_id="default_user", agent_id="default_agent")
        messages = []
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.get_participant_user_ids() == ["default_user"]
        assert handler.get_participant_agent_ids() == ["default_agent"]

    def test_messages_without_role_id_uses_ctx_defaults(self):
        """Test that messages without role_id fall back to ctx defaults."""
        ctx = create_ctx(user_id="default_user", agent_id="default_agent")

        # Message without role_id
        msg = Message(
            id="msg_no_role_id",
            role="user",
            parts=[TextPart(text="Hello")],
            role_id=None,
        )
        messages = [msg]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # Should use ctx defaults since no valid role_id found
        assert "default_user" in handler.get_participant_user_ids()


class TestValidateRoleId:
    """Tests for validate_role_id."""

    def test_valid_user_role_id(self):
        """Test validating a valid user role_id."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_a"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.validate_role_id("user_a", "user") is True

    def test_invalid_user_role_id(self):
        """Test validating an invalid user role_id."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_a"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.validate_role_id("user_b", "user") is False

    def test_valid_agent_role_id(self):
        """Test validating a valid agent role_id."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_a"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.validate_role_id("agent_a", "agent") is True

    def test_invalid_agent_role_id(self):
        """Test validating an invalid agent role_id."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_a"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.validate_role_id("agent_b", "agent") is False

    def test_get_valid_role_ids(self):
        """Test getting valid role_ids."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("user", "user_b"),
            create_message("assistant", "agent_a"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        assert handler.get_valid_role_ids("user") == ["user_a", "user_b"]
        assert handler.get_valid_role_ids("agent") == ["agent_a"]


class TestCalculateUserSpace:
    """Tests for _calculate_user_space."""

    def test_user_space_no_isolation(self):
        """Test user_space when isolate_user_scope_by_agent is false."""
        ctx = create_ctx(isolate_user_by_agent=False)
        extract_ctx = create_mock_extract_context([])
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        assert handler._calculate_user_space("user_a") == "user_a"

    def test_user_space_with_isolation(self):
        """Test user_space when isolate_user_scope_by_agent is true."""
        ctx = create_ctx(isolate_user_by_agent=True, user_id="user_a", agent_id="agent_x")
        extract_ctx = create_mock_extract_context([])
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        assert handler._calculate_user_space("user_a") == "user_a/agent/agent_x"


class TestCalculateAgentSpace:
    """Tests for _calculate_agent_space."""

    def test_agent_space_no_isolation(self):
        """Test agent_space when isolate_agent_scope_by_user is false."""
        ctx = create_ctx(isolate_agent_by_user=False)
        extract_ctx = create_mock_extract_context([])
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        assert handler._calculate_agent_space("agent_a") == "agent_a"

    def test_agent_space_with_isolation(self):
        """Test agent_space when isolate_agent_scope_by_user is true."""
        ctx = create_ctx(isolate_agent_by_user=True, user_id="user_x", agent_id="agent_a")
        extract_ctx = create_mock_extract_context([])
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        assert handler._calculate_agent_space("agent_a") == "agent_a/user/user_x"


class TestCalculateTargetForRole:
    """Tests for _calculate_target_for_role."""

    def test_user_target_no_isolation(self):
        """Test user target when isolate_user_scope_by_agent is false."""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_a",
            isolate_user_by_agent=False,
        )
        messages = [create_message("user", "user_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        target = handler._calculate_target_for_role("user_a", "user", "preferences")

        assert target.uri == "viking://user/user_a/memories/preferences"
        assert target.owner_user_id == "user_a"
        assert target.owner_agent_id is None

    def test_user_target_with_isolation(self):
        """Test user target when isolate_user_scope_by_agent is true."""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_x",
            isolate_user_by_agent=True,
        )
        messages = [create_message("user", "user_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        target = handler._calculate_target_for_role("user_a", "user", "preferences")

        assert target.uri == "viking://user/user_a/agent/agent_x/memories/preferences"
        assert target.owner_user_id == "user_a"
        assert target.owner_agent_id == "agent_x"

    def test_agent_target_no_isolation(self):
        """Test agent target when isolate_agent_scope_by_user is false."""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_a",
            isolate_agent_by_user=False,
        )
        messages = [create_message("assistant", "agent_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        target = handler._calculate_target_for_role("agent_a", "agent", "skills")

        assert target.uri == "viking://agent/agent_a/memories/skills"
        assert target.owner_agent_id == "agent_a"
        assert target.owner_user_id is None

    def test_agent_target_with_isolation(self):
        """Test agent target when isolate_agent_scope_by_user is true."""
        ctx = create_ctx(
            user_id="user_x",
            agent_id="agent_a",
            isolate_agent_by_user=True,
        )
        messages = [create_message("assistant", "agent_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        target = handler._calculate_target_for_role("agent_a", "agent", "skills")

        assert target.uri == "viking://agent/agent_a/user/user_x/memories/skills"
        assert target.owner_agent_id == "agent_a"
        assert target.owner_user_id == "user_x"


class TestCalculateMemoryTargets:
    """Tests for calculate_memory_targets."""

    def test_single_target_no_role_id(self):
        """Test single target when role_id is None (use ctx default)."""
        ctx = create_ctx(user_id="user_a", agent_id="agent_a")
        messages = [create_message("user", "user_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        targets = handler.calculate_memory_targets(
            role_id=None,
            role_type="user",
            memory_type="preferences",
        )

        assert len(targets) == 1
        assert targets[0].owner_user_id == "user_a"

    def test_single_target_with_valid_role_id(self):
        """Test single target with valid role_id."""
        ctx = create_ctx(user_id="user_a", agent_id="agent_a")
        messages = [
            create_message("user", "user_a"),
            create_message("user", "user_b"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        targets = handler.calculate_memory_targets(
            role_id="user_b",
            role_type="user",
            memory_type="preferences",
        )

        assert len(targets) == 1
        assert targets[0].owner_user_id == "user_b"

    def test_single_target_with_invalid_role_id(self):
        """Test single target with invalid role_id raises error."""
        ctx = create_ctx(user_id="user_a", agent_id="agent_a")
        messages = [create_message("user", "user_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        with pytest.raises(ValueError) as exc_info:
            handler.calculate_memory_targets(
                role_id="user_invalid",
                role_type="user",
                memory_type="preferences",
            )

        assert "user_invalid" in str(exc_info.value)
        assert "not in session participants" in str(exc_info.value)

    def test_events_multiple_targets(self):
        """Test events type returns multiple targets."""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_a",
        )
        messages = [
            create_message("user", "user_a"),
            create_message("user", "user_b"),
            create_message("user", "user_c"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # events without explicit operation uses all user participants
        targets = handler.calculate_memory_targets(
            role_id=None,
            role_type="user",
            memory_type="events",
        )

        assert len(targets) == 3
        owner_ids = [t.owner_user_id for t in targets]
        assert "user_a" in owner_ids
        assert "user_b" in owner_ids
        assert "user_c" in owner_ids

    def test_operation_with_user_id_field(self):
        """Test operation with user_id field returns single target."""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_a",
        )
        messages = [
            create_message("user", "user_a"),
            create_message("user", "user_b"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # operation with user_id field
        operation = {"user_id": "user_b"}
        targets = handler.calculate_memory_targets(
            role_id=None,
            role_type="user",
            memory_type="preferences",
            operation=operation,
        )

        assert len(targets) == 1
        assert targets[0].owner_user_id == "user_b"

    def test_operation_with_ranges_field(self):
        """Test operation with ranges field extracts user_ids from range."""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_a",
        )
        messages = [
            create_message("user", "user_a"),
            create_message("user", "user_b"),
            create_message("user", "user_c"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # Mock _extract_role_ids_from_events_range to return specific user_ids
        with patch.object(
            handler,
            "_extract_role_ids_from_events_range",
            return_value=["user_a", "user_c"],
        ):
            operation = {"ranges": "0-1"}
            targets = handler.calculate_memory_targets(
                role_id=None,
                role_type="user",
                memory_type="events",
                operation=operation,
            )

        assert len(targets) == 2
        owner_ids = [t.owner_user_id for t in targets]
        assert "user_a" in owner_ids
        assert "user_c" in owner_ids


class TestNamespacePolicyCombinations:
    """Tests for all four namespace policy combinations."""

    def test_policy_false_false(self):
        """Test: isolate_user=false, isolate_agent=false"""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_x",
            isolate_user_by_agent=False,
            isolate_agent_by_user=False,
        )
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_x"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # User target
        user_target = handler._calculate_target_for_role("user_a", "user", "preferences")
        assert user_target.uri == "viking://user/user_a/memories/preferences"
        assert user_target.owner_user_id == "user_a"
        assert user_target.owner_agent_id is None

        # Agent target
        agent_target = handler._calculate_target_for_role("agent_x", "agent", "skills")
        assert agent_target.uri == "viking://agent/agent_x/memories/skills"
        assert agent_target.owner_agent_id == "agent_x"
        assert agent_target.owner_user_id is None

    def test_policy_false_true(self):
        """Test: isolate_user=false, isolate_agent=true"""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_x",
            isolate_user_by_agent=False,
            isolate_agent_by_user=True,
        )
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_x"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # User target (no isolation)
        user_target = handler._calculate_target_for_role("user_a", "user", "preferences")
        assert user_target.uri == "viking://user/user_a/memories/preferences"

        # Agent target (isolated by user)
        agent_target = handler._calculate_target_for_role("agent_x", "agent", "skills")
        assert agent_target.uri == "viking://agent/agent_x/user/user_a/memories/skills"
        assert agent_target.owner_user_id == "user_a"

    def test_policy_true_false(self):
        """Test: isolate_user=true, isolate_agent=false"""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_x",
            isolate_user_by_agent=True,
            isolate_agent_by_user=False,
        )
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_x"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # User target (isolated by agent)
        user_target = handler._calculate_target_for_role("user_a", "user", "preferences")
        assert user_target.uri == "viking://user/user_a/agent/agent_x/memories/preferences"
        assert user_target.owner_agent_id == "agent_x"

        # Agent target (no isolation)
        agent_target = handler._calculate_target_for_role("agent_x", "agent", "skills")
        assert agent_target.uri == "viking://agent/agent_x/memories/skills"

    def test_policy_true_true(self):
        """Test: isolate_user=true, isolate_agent=true"""
        ctx = create_ctx(
            user_id="user_a",
            agent_id="agent_x",
            isolate_user_by_agent=True,
            isolate_agent_by_user=True,
        )
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_x"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.load_participants()

        # User target (isolated by agent)
        user_target = handler._calculate_target_for_role("user_a", "user", "preferences")
        assert user_target.uri == "viking://user/user_a/agent/agent_x/memories/preferences"

        # Agent target (isolated by user)
        agent_target = handler._calculate_target_for_role("agent_x", "agent", "skills")
        assert agent_target.uri == "viking://agent/agent_x/user/user_a/memories/skills"


class TestPrepareMessages:
    """Tests for prepare_messages with enable_role_id_memory_isolate toggle."""

    @patch("openviking.session.memory.memory_isolation_handler.get_openviking_config")
    def test_prepare_messages_disabled_clears_role_ids(self, mock_config):
        """开关关闭时，prepare_messages 清空所有 message 的 role_id。"""
        mock_memory_config = MagicMock()
        mock_memory_config.enable_role_id_memory_isolate = False
        mock_config.return_value.memory = mock_memory_config

        ctx = create_ctx(user_id="login_user", agent_id="login_agent")
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Hi"),
            create_message("user", "user_b", "Hey"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()

        for msg in messages:
            assert msg.role_id is None

    @patch("openviking.session.memory.memory_isolation_handler.get_openviking_config")
    def test_prepare_messages_enabled_keeps_role_ids(self, mock_config):
        """开关开启时，prepare_messages 不修改 role_id。"""
        mock_memory_config = MagicMock()
        mock_memory_config.enable_role_id_memory_isolate = True
        mock_config.return_value.memory = mock_memory_config

        ctx = create_ctx(user_id="login_user", agent_id="login_agent")
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()

        assert messages[0].role_id == "user_a"
        assert messages[1].role_id == "agent_a"

    @patch("openviking.session.memory.memory_isolation_handler.get_openviking_config")
    def test_get_read_scope_uses_login_user_when_disabled(self, mock_config):
        """开关关闭时，get_read_scope 只返回登录用户（因为 role_id 被清空）。"""
        mock_memory_config = MagicMock()
        mock_memory_config.enable_role_id_memory_isolate = False
        mock_config.return_value.memory = mock_memory_config

        ctx = create_ctx(user_id="login_user", agent_id="login_agent")
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert scope.user_ids == ["login_user"]
        assert scope.agent_ids == ["login_agent"]

    @patch("openviking.session.memory.memory_isolation_handler.get_openviking_config")
    def test_get_read_scope_uses_role_ids_when_enabled(self, mock_config):
        """开关开启时，get_read_scope 从 message role_id 提取参与者。"""
        mock_memory_config = MagicMock()
        mock_memory_config.enable_role_id_memory_isolate = True
        mock_config.return_value.memory = mock_memory_config

        ctx = create_ctx(user_id="login_user", agent_id="login_agent")
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert set(scope.user_ids) == {"user_a"}
        assert set(scope.agent_ids) == {"agent_a"}

    @patch("openviking.session.memory.memory_isolation_handler.get_openviking_config")
    def test_prepare_messages_no_config(self, mock_config):
        """没有 memory 配置时，默认关闭，清空 role_id。"""
        mock_config.return_value.memory = None

        ctx = create_ctx(user_id="login_user", agent_id="login_agent")
        messages = [
            create_message("user", "user_a", "Hello"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()

        assert messages[0].role_id is None
