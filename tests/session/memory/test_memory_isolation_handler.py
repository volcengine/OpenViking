# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MemoryIsolationHandler.
"""

from unittest.mock import MagicMock, patch

from openviking.message.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.session.memory.memory_isolation_handler import (
    MemoryIsolationHandler,
)
from openviking_cli.session.user_id import UserIdentifier


def create_message(
    role: str,
    role_id: str | None,
    content: str = "test",
    peer_id: str | None = None,
) -> Message:
    """Helper to create a test message."""
    return Message(
        id=f"msg_{role}_{role_id}",
        role=role,
        parts=[TextPart(text=content)],
        role_id=role_id,
        peer_id=peer_id,
    )


def create_ctx(
    account_id: str = "test_account",
    user_id: str = "user_a",
    isolate_user_by_agent: bool = False,
    isolate_agent_by_user: bool = False,
) -> RequestContext:
    """Helper to create a test RequestContext."""
    user = UserIdentifier(
        account_id=account_id,
        user_id=user_id,
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

    def test_single_user_scope(self):
        """Test extracting the authenticated user scope."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a", "Hello"),
            create_message("assistant", "agent_a", "Hi there"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        scope = handler.get_read_scope()

        assert scope.user_ids == ["user_a"]

    def test_message_role_ids_do_not_expand_user_scope(self):
        """Message role_id does not change the authenticated user scope."""
        ctx = create_ctx()
        messages = [
            create_message("user", "user_a", "Hello from A"),
            create_message("user", "user_b", "Hello from B"),
            create_message("assistant", "agent_a", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        scope = handler.get_read_scope()

        assert scope.user_ids == ["user_a"]

    def test_assistant_role_ids_do_not_create_agent_scope(self):
        """Assistant role_id does not create a separate write scope."""
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

    def test_message_peer_ids_do_not_expand_self_extraction_scope(self):
        """Message peer_id should not make self extraction read or write peer memory."""
        ctx = create_ctx()
        messages = [
            create_message("user", "visitor_a", peer_id="web:visitor:alice"),
            create_message("user", "visitor_b", peer_id="web:visitor:bob"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        scope = handler.get_read_scope()

        assert scope.user_ids == ["user_a"]
        assert scope.peer_ids == []

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
        ctx = create_ctx(
            user_id="default_user",
        )
        messages = []
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        scope = handler.get_read_scope()

        assert scope.user_ids == ["default_user"]

    def test_messages_without_role_id_uses_ctx_defaults(self):
        """Test that messages without role_id fall back to ctx defaults."""
        ctx = create_ctx(
            user_id="default_user",
        )

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


class TestFillRoleIds:
    """Tests for fill_role_ids."""

    def test_fill_role_ids_with_specified_values(self):
        """Test fill_role_ids ignores deprecated agent_id."""
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
        assert "agent_id" not in item_dict

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

        assert item_dict["user_id"] == "user_a"
        assert "agent_id" not in item_dict

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
        assert "agent_id" not in item_dict

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
        assert item_dict["user_ids"] == ["user_a"]
        assert "agent_ids" not in item_dict

    def test_fill_role_ids_adds_target_peer_only_when_explicit(self):
        ctx = create_ctx()
        messages = [create_message("user", "user_a", peer_id="web:visitor:alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            target_peer_id="web:visitor:alice",
        )
        role_scope = handler.get_read_scope()

        item_dict = {}
        handler.fill_role_ids(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"
        assert "agent_id" not in item_dict
        assert item_dict["peer_id"] == "web:visitor:alice"


class TestPrepareMessages:
    """Tests for prepare_messages under the user/peer model."""

    def test_prepare_messages_keeps_legacy_metadata(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", None, "Hello"),
            create_message("assistant", None, "Hi"),
            create_message("user", "user_b", "Hey", peer_id="web:visitor:alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()

        assert messages[0].role_id is None
        assert messages[1].role_id is None
        assert messages[2].role_id == "user_b"
        assert messages[2].peer_id == "web:visitor:alice"

    def test_get_read_scope_uses_ctx_user(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", None, "Hello"),
            create_message("assistant", None, "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert scope.user_ids == ["login_user"]

    def test_get_read_scope_ignores_message_peer_id_without_target(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", "user_a", "Hello", peer_id="web:visitor:alice"),
            create_message("assistant", "agent_a", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert scope.user_ids == ["login_user"]
        assert scope.peer_ids == []


class TestCalculateMemoryUris:
    """Tests for calculate_memory_uris (integration with URI generation)."""

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_single_user(self, mock_generate_uri):
        """Test calculate_memory_uris with a single user."""
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
    def test_calculate_memory_uris_ignores_extracted_user_ids(self, mock_generate_uri):
        """LLM-extracted user_ids cannot redirect memory writes."""
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/test"
        )

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

        assert uris == ["viking://user/user_a/memories/test"]
        assert operation.memory_fields["user_id"] == "user_a"
        assert "agent_id" not in operation.memory_fields
        assert "agent_ids" not in operation.memory_fields

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_routes_peer_memory_to_target_peer(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(
            user_id="support_bot",
        )
        messages = [create_message("user", "visitor_a", peer_id="web:visitor:alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            target_peer_id="web:visitor:alice",
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"user_id": "alice", "agent_id": "other_agent"},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/support_bot/peers/web:visitor:alice/memories/preferences"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "agent_id" not in operation.memory_fields
        assert operation.memory_fields["peer_id"] == "web:visitor:alice"
