# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for MemoryIsolationHandler.
"""

from unittest.mock import MagicMock, patch

from openviking.message.message import Message
from openviking.message.part import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.memory_isolation_handler import (
    MemoryIsolationHandler,
)
from openviking_cli.session.user_id import UserIdentifier


def create_message(
    role: str,
    content: str = "test",
    peer_id: str | None = None,
) -> Message:
    """Helper to create a test message."""
    return Message(
        id=f"msg_{role}_{peer_id or 'self'}",
        role=role,
        parts=[TextPart(text=content)],
        peer_id=peer_id,
    )


def create_ctx(
    account_id: str = "test_account",
    user_id: str = "user_a",
) -> RequestContext:
    """Helper to create a test RequestContext."""
    user = UserIdentifier(
        account_id=account_id,
        user_id=user_id,
    )
    return RequestContext(user=user, role=Role.USER)


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
            create_message("user", "Hello"),
            create_message("assistant", "Hi there"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)

        scope = handler.get_read_scope()

        assert scope.user_ids == ["user_a"]

    def test_message_peer_ids_do_not_expand_self_extraction_scope(self):
        """Message peer_id should not make self extraction read or write peer memory."""
        ctx = create_ctx()
        messages = [
            create_message("user", peer_id="web:visitor:alice"),
            create_message("user", peer_id="web:visitor:bob"),
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
            create_message("user", "First message"),
            create_message("user", "Second message"),
            create_message("user", "Third message"),
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


class TestFillIdentityFields:
    """Tests for fill_identity_fields."""

    def test_fill_identity_fields_with_specified_values(self):
        """Test fill_identity_fields keeps writes scoped to the ctx user."""
        ctx = create_ctx()
        messages = [
            create_message("user"),
            create_message("assistant"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"user_id": "user_a"}
        handler.fill_identity_fields(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"

    def test_fill_identity_fields_without_values_uses_default(self):
        """Test fill_identity_fields without values uses ctx user."""
        ctx = create_ctx()
        messages = [
            create_message("user"),
            create_message("user"),
            create_message("assistant"),
            create_message("assistant"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {}
        handler.fill_identity_fields(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"

    def test_fill_identity_fields_invalid_user_id_ignored(self):
        """Test invalid user_id is ignored, uses default."""
        ctx = create_ctx()
        messages = [
            create_message("user"),
            create_message("assistant"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"user_id": "invalid_user"}
        handler.fill_identity_fields(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"  # fallback to default

    def test_fill_identity_fields_with_ranges_keeps_ctx_user_only(self):
        """ranges do not create multi-user write scopes."""
        ctx = create_ctx()
        messages = [
            create_message("user"),
            create_message("assistant"),
            create_message("user"),
            create_message("assistant"),
        ]
        extract_ctx = create_mock_extract_context(messages)

        # Mock read_message_ranges
        mock_range = MagicMock()
        mock_range.elements = [messages]
        extract_ctx.read_message_ranges.return_value = mock_range

        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"ranges": "0-3"}
        handler.fill_identity_fields(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"
        assert "user_ids" not in item_dict

    def test_fill_identity_fields_normalizes_explicit_peer_id(self):
        ctx = create_ctx()
        messages = [create_message("user", peer_id="web:visitor:alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"peer_id": "web:visitor:alice"}
        handler.fill_identity_fields(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"
        assert item_dict["peer_id"] == "web:visitor:alice"


class TestPrepareMessages:
    """Tests for prepare_messages under the user/peer model."""

    def test_prepare_messages_keeps_peer_metadata(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", "Hello"),
            create_message("assistant", "Hi"),
            create_message("user", "Hey", peer_id="web:visitor:alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()

        assert messages[2].peer_id == "web:visitor:alice"

    def test_get_read_scope_uses_ctx_user(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", "Hello"),
            create_message("assistant", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert scope.user_ids == ["login_user"]

    def test_get_read_scope_ignores_message_peer_id_without_target(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", "Hello", peer_id="web:visitor:alice"),
            create_message("assistant", "Hi"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert scope.user_ids == ["login_user"]
        assert scope.peer_ids == []

    def test_get_read_scope_includes_allowed_peer_ids_when_enabled(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", "Hello", peer_id="web:visitor:alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allowed_peer_ids={"web:visitor:alice"},
        )
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert scope.user_ids == ["login_user"]
        assert scope.peer_ids == ["web:visitor:alice"]


class TestCalculateMemoryUris:
    """Tests for calculate_memory_uris (integration with URI generation)."""

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_single_user(self, mock_generate_uri):
        """Test calculate_memory_uris with a single user."""
        mock_generate_uri.return_value = "viking://user/user_a/memories/preferences"

        ctx = create_ctx()
        messages = [create_message("user")]
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
            memory_fields={"user_id": "user_a"},
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
        messages = [create_message("user")]
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
            memory_fields={"user_ids": ["user_a", "user_b"]},
            memory_type="test",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/user_a/memories/test"]
        assert operation.memory_fields["user_id"] == "user_a"

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_routes_explicit_peer_memory(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(
            user_id="support_bot",
        )
        messages = [create_message("user", peer_id="web:visitor:alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allowed_peer_ids={"web:visitor:alice"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={
                "user_id": "alice",
                "peer_id": "web:visitor:alice",
            },
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/support_bot/peers/web:visitor:alice/memories/preferences"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert operation.memory_fields["peer_id"] == "web:visitor:alice"

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_routes_ranges_to_self_and_peer(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/events/demo"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("user", "self event"),
            create_message("user", "peer event", peer_id="web:visitor:alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        mock_range = MagicMock()
        mock_range.elements = [messages]
        extract_ctx.read_message_ranges.return_value = mock_range
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"web:visitor:alice"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="events",
            filename_template="demo.md",
            directory="viking://user/{user_space}/memories/events",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"event_name": "demo", "ranges": "0-1"},
            memory_type="events",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert set(uris) == {
            "viking://user/support_bot/memories/events/demo",
            "viking://user/support_bot/peers/web:visitor:alice/memories/events/demo",
        }
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_rejects_unallowed_peer_id(self, mock_generate_uri):
        ctx = create_ctx(user_id="support_bot")
        messages = [create_message("user", peer_id="web:visitor:alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allowed_peer_ids={"web:visitor:bob"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"peer_id": "web:visitor:alice"},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == []
        mock_generate_uri.assert_not_called()
