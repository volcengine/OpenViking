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


class TestGetReadScope:
    """Tests for get_read_scope."""

    def test_single_user_single_agent(self):
        """Test extracting single user and agent."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Hi there"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        scope = handler.get_read_scope()

        assert scope.user_ids == ["user_a"]
        assert scope.agent_ids == ["agent_a"]

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

        scope = handler.get_read_scope()

        assert set(scope.user_ids) == {"user_a", "user_b"}
        assert scope.agent_ids == ["agent_a"]

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

        scope = handler.get_read_scope()

        assert scope.user_ids == ["user_a"]
        assert set(scope.agent_ids) == {"agent_a", "agent_b"}

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

        scope = handler.get_read_scope()

        assert scope.user_ids == ["user_a"]

    def test_empty_messages_uses_ctx_defaults(self):
        """Test that empty messages fall back to ctx defaults."""
        ctx = create_ctx(user_id="default_user", agent_id="default_agent")
        messages = []
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        scope = handler.get_read_scope()

        assert scope.user_ids == ["default_user"]
        assert scope.agent_ids == ["default_agent"]

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

        scope = handler.get_read_scope()

        # Should use ctx defaults because no valid role_id found in messages
        assert scope.user_ids == ["default_user"]
        assert scope.agent_ids == ["default_agent"]


class TestFillRoleIds:
    """Tests for fill_role_ids."""

    def test_fill_role_ids_with_specified_values(self):
        """Test fill_role_ids with specified user_id and agent_id."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_a"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"user_id": "user_a", "agent_id": "agent_a"}
        handler.fill_role_ids(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"
        assert item_dict["agent_id"] == "agent_a"

    def test_fill_role_ids_without_values_uses_default(self):
        """Test fill_role_ids without values uses first in scope."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("user", "user_b"),
            create_message("assistant", "agent_a"),
            create_message("assistant", "agent_b"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {}
        handler.fill_role_ids(item_dict, role_scope)

        assert item_dict["user_id"] in ["user_a", "user_b"]
        assert item_dict["agent_id"] in ["agent_a", "agent_b"]

    def test_fill_role_ids_invalid_user_id_ignored(self):
        """Test invalid user_id is ignored, uses default."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_a"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"user_id": "invalid_user", "agent_id": "agent_a"}
        handler.fill_role_ids(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"  # fallback to default
        assert item_dict["agent_id"] == "agent_a"

    def test_fill_role_ids_with_ranges(self):
        """Test fill_role_ids with ranges extracts from messages."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a"),
            create_message("assistant", "agent_a"),
            create_message("user", "user_b"),
            create_message("assistant", "agent_b"),
        ]
        extract_ctx = create_mock_extract_context(messages)

        # Mock read_message_ranges
        mock_range = MagicMock()
        mock_range.elements = [messages]
        extract_ctx.read_message_ranges.return_value = mock_range

        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"ranges": "0-3"}
        handler.fill_role_ids(item_dict, role_scope)

        assert "user_ids" in item_dict
        assert "agent_ids" in item_dict
        assert set(item_dict["user_ids"]) == {"user_a", "user_b"}
        assert set(item_dict["agent_ids"]) == {"agent_a", "agent_b"}


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
    def test_get_read_scope_with_prepare_disabled(self, mock_config):
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
    def test_get_read_scope_with_prepare_enabled(self, mock_config):
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


class TestCalculateMemoryUris:
    """Tests for calculate_memory_uris (integration with URI generation)."""

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_single_user_agent(self, mock_generate_uri):
        """Test calculate_memory_uris with single user and agent."""
        mock_generate_uri.return_value = "viking://user/user_a/memories/preferences"

        ctx = create_ctx()
        messages = [create_message("user", "user_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )

        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"user_id": "user_a", "agent_id": "agent_a"},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert len(uris) == 1
        assert "user_a" in uris[0]

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_multiple_users_agents(self, mock_generate_uri):
        """Test calculate_memory_uris with multiple users and agents."""
        mock_generate_uri.side_effect = lambda **kwargs: f"viking://user/{kwargs.get('user_space')}/memories/test"

        ctx = create_ctx()
        messages = [create_message("user", "user_a")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="test",
            filename_template="test.md",
            directory="viking://user/{user_space}/memories",
        )

        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"user_ids": ["user_a", "user_b"], "agent_ids": ["agent_a", "agent_b"]},
            memory_type="test",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert len(uris) == 4  # 2 users * 2 agents
