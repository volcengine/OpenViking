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
            create_message("user", peer_id="web-visitor-alice"),
            create_message("user", peer_id="web-visitor-bob"),
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

    def test_get_read_scope_filters_self_sentinel_from_peer_scope(self):
        ctx = create_ctx(user_id="support_bot")
        messages = [create_message("user")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"__self", "web-visitor-alice"},
        )

        scope = handler.get_read_scope()

        assert scope.user_ids == ["support_bot"]
        assert scope.peer_ids == ["web-visitor-alice"]

    def test_render_schema_directories_self_sentinel_maps_to_user_space(self):
        from openviking.session.memory.dataclass import MemoryTypeSchema
        from openviking.session.memory.memory_isolation_handler import peer_user_space

        assert peer_user_space("support_bot", "__self") == "support_bot"

        ctx = create_ctx(user_id="support_bot")
        messages = [create_message("user")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"__self", "web-visitor-alice"},
        )
        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{{ user_space }}/memories",
        )

        dirs = handler.render_schema_directories(schema)

        assert "viking://user/support_bot/memories" in dirs
        assert "viking://user/support_bot/peers/__self/memories" not in dirs
        assert "viking://user/support_bot/peers/web-visitor-alice/memories" in dirs

    def test_render_schema_directories_peer_enabled_false_uses_self_only(self):
        from openviking.session.memory.dataclass import MemoryTypeSchema

        ctx = create_ctx(user_id="support_bot")
        messages = [create_message("user", peer_id="web-visitor-alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"web-visitor-alice"},
        )
        schema = MemoryTypeSchema(
            memory_type="cases",
            filename_template="{{ case_name }}.md",
            directory="viking://user/{{ user_space }}/memories/cases",
            peer_enabled=False,
        )

        dirs = handler.render_schema_directories(schema)

        assert dirs == ["viking://user/support_bot/memories/cases"]


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
        messages = [create_message("user", peer_id="web-visitor-alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        role_scope = handler.get_read_scope()

        item_dict = {"peer_id": "web-visitor-alice"}
        handler.fill_identity_fields(item_dict, role_scope)

        assert item_dict["user_id"] == "user_a"
        assert item_dict["peer_id"] == "web-visitor-alice"


class TestPrepareMessages:
    """Tests for prepare_messages under the user/peer model."""

    def test_prepare_messages_keeps_peer_metadata(self):
        ctx = create_ctx(user_id="login_user")
        messages = [
            create_message("user", "Hello"),
            create_message("assistant", "Hi"),
            create_message("user", "Hey", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(ctx, extract_ctx)
        handler.prepare_messages()

        assert messages[2].peer_id == "web-visitor-alice"

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
            create_message("user", "Hello", peer_id="web-visitor-alice"),
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
            create_message("user", "Hello", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allowed_peer_ids={"web-visitor-alice"},
        )
        handler.prepare_messages()
        scope = handler.get_read_scope()

        assert scope.user_ids == ["login_user"]
        assert scope.peer_ids == ["web-visitor-alice"]


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
        messages = [create_message("user", peer_id="web-visitor-alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allowed_peer_ids={"web-visitor-alice"},
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
                "peer_id": "web-visitor-alice",
            },
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/support_bot/peers/web-visitor-alice/memories/preferences"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert operation.memory_fields["peer_id"] == "web-visitor-alice"

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_routes_ranges_to_self_and_peer(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/events/demo"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("user", "self event"),
            create_message("user", "peer event", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        mock_range = MagicMock()
        mock_range.elements = [messages]
        extract_ctx.read_message_ranges.return_value = mock_range
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"web-visitor-alice"},
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
            "viking://user/support_bot/peers/web-visitor-alice/memories/events/demo",
        }
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_ranges_ignore_assistant_peer_ids(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/events/demo"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("assistant", "bot ack", peer_id="assistant-bot"),
            create_message("user", "peer event", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        mock_range = MagicMock()
        mock_range.elements = [messages]
        extract_ctx.read_message_ranges.return_value = mock_range
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"assistant-bot", "web-visitor-alice"},
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

        assert uris == ["viking://user/support_bot/peers/web-visitor-alice/memories/events/demo"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_unallowed_peer_id_does_not_fallback(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("user", peer_id="web-visitor-bob"),
            create_message("user", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allowed_peer_ids={"web-visitor-bob"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"peer_id": "web-visitor-alice"},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == []
        assert "peer_id" not in operation.memory_fields
        mock_generate_uri.assert_not_called()

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_missing_peer_id_prefers_self_when_allowed(
        self, mock_generate_uri
    ):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("user", "self turn"),
            create_message("assistant", "ack", peer_id="web-visitor-alice"),
            create_message("user", "peer turn", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"web-visitor-alice"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/support_bot/memories/preferences"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_missing_peer_id_uses_unique_user_peer_when_self_absent(
        self, mock_generate_uri
    ):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("user", "peer turn one", peer_id="web-visitor-bob"),
            create_message("assistant", "ack", peer_id="web-visitor-bob"),
            create_message("assistant", "bot ack", peer_id="assistant-bot"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=False,
            allowed_peer_ids={"assistant-bot", "web-visitor-bob"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/support_bot/peers/web-visitor-bob/memories/preferences"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert operation.memory_fields["peer_id"] == "web-visitor-bob"

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_missing_peer_id_ignores_assistant_peer_when_self_absent(
        self, mock_generate_uri
    ):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("assistant", "bot ack", peer_id="assistant-bot"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=False,
            allowed_peer_ids={"assistant-bot"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == []
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields
        mock_generate_uri.assert_not_called()

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_missing_peer_id_drops_ambiguous_peer_when_self_absent(
        self, mock_generate_uri
    ):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("user", "peer turn one", peer_id="web-visitor-bob"),
            create_message("user", "peer turn two", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=False,
            allowed_peer_ids={"web-visitor-alice", "web-visitor-bob"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == []
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields
        mock_generate_uri.assert_not_called()

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_peer_enabled_false_forces_self_scope_and_strips_peer_id(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/cases/demo"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [create_message("user", peer_id="web-visitor-alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"web-visitor-alice"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="cases",
            filename_template="demo.md",
            directory="viking://user/{user_space}/memories/cases",
            peer_enabled=False,
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"case_name": "demo", "peer_id": "web-visitor-alice"},
            memory_type="cases",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/support_bot/memories/cases/demo"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_peer_enabled_false_does_not_write_self_when_self_disabled(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/cases/demo"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [create_message("user", peer_id="web-visitor-alice")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=False,
            allowed_peer_ids={"web-visitor-alice"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="cases",
            filename_template="demo.md",
            directory="viking://user/{user_space}/memories/cases",
            peer_enabled=False,
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"case_name": "demo", "peer_id": "web-visitor-alice"},
            memory_type="cases",
            uris=[],
        )

        assert handler.allows_schema(schema) is False

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == []
        mock_generate_uri.assert_not_called()

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_peer_enabled_false_ignores_ranges_peer_targets(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/cases/demo"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [
            create_message("user", "self turn"),
            create_message("user", "peer turn", peer_id="web-visitor-alice"),
        ]
        extract_ctx = create_mock_extract_context(messages)
        mock_range = MagicMock()
        mock_range.elements = [messages]
        extract_ctx.read_message_ranges.return_value = mock_range
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allow_self=True,
            allowed_peer_ids={"web-visitor-alice"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="cases",
            filename_template="demo.md",
            directory="viking://user/{user_space}/memories/cases",
            peer_enabled=False,
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"case_name": "demo", "ranges": "0-1"},
            memory_type="cases",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == ["viking://user/support_bot/memories/cases/demo"]
        assert operation.memory_fields["user_id"] == "support_bot"
        assert "peer_id" not in operation.memory_fields

    @patch("openviking.session.memory.memory_isolation_handler.generate_uri")
    def test_calculate_memory_uris_invalid_peer_id_does_not_fallback(self, mock_generate_uri):
        mock_generate_uri.side_effect = lambda **kwargs: (
            f"viking://user/{kwargs.get('user_space')}/memories/preferences"
        )

        ctx = create_ctx(user_id="support_bot")
        messages = [create_message("user", peer_id="web-visitor-bob")]
        extract_ctx = create_mock_extract_context(messages)
        handler = MemoryIsolationHandler(
            ctx,
            extract_ctx,
            allowed_peer_ids={"web-visitor-bob"},
        )

        from openviking.session.memory.dataclass import MemoryTypeSchema, ResolvedOperation

        schema = MemoryTypeSchema(
            memory_type="preferences",
            filename_template="preferences.md",
            directory="viking://user/{user_space}/memories",
        )
        operation = ResolvedOperation(
            old_memory_file_content=None,
            memory_fields={"peer_id": "web/visitor/alice"},
            memory_type="preferences",
            uris=[],
        )

        uris = handler.calculate_memory_uris(schema, operation, extract_ctx)

        assert uris == []
        assert "peer_id" not in operation.memory_fields
        mock_generate_uri.assert_not_called()


class TestPeerOwnerMessageRoleExpansion:
    """Verify the fix for issue #3171: Extract VLM tools blocked by Role.USER peer isolation.

    MemoryIsolationHandler used to accept only role=="user" messages as valid
    "peer owner" inputs. When the ExtractLoop VLM orchestrator produced a
    peer-tagged memory draft via an assistant- or tool-role sideband
    (ToolPart follow-ups, assistant reply attribution), the peer_id was
    silently dropped and the resulting write fell back to self-scope or was
    skipped entirely. We now accept any non-sentinel role so VLM tool flows
    keep their peer attribution.
    """

    def test_is_peer_owner_message_accepts_all_speaker_roles(self):
        roles_accepted = ["user", "assistant", "tool", "peer", "USER", "Assistant", "ToolCall"]
        for role in roles_accepted:
            msg = create_message(role=role, content="x", peer_id=None)
            assert MemoryIsolationHandler._is_peer_owner_message(msg) is True, (
                f"Expected role {role!r} to pass _is_peer_owner_message()"
            )

    def test_is_peer_owner_message_rejects_sentinel_and_empty_roles(self):
        roles_rejected = [
            "system",
            "developer",
            "",
            None,
            "SYSTEM",
            "Developer",
            "none",
            "NONE",
        ]
        for role in roles_rejected:
            msg = MagicMock()
            msg.role = role
            # Test through getattr behavior, matching the implementation's
            # defensive str(None) / empty fallback.
            assert MemoryIsolationHandler._is_peer_owner_message(msg) is False, (
                f"Expected role {role!r} to be rejected by _is_peer_owner_message()"
            )

    def test_message_target_id_preserves_assistant_peer_tag(self):
        """Reproduce #3171 failure case: assistant tool output tagged with peer_id.

        Before the fix, the ``peer_id + _is_peer_owner_message + _can_write_peer``
        chain would reject assistant/tool-role messages, causing the target to
        collapse to self or None even though the caller's allowed_peer_ids
        contained the peer and the message explicitly carried it.
        """
        peer = "web/visitor/alice"
        handler = MemoryIsolationHandler(
            ctx=create_ctx(),
            extract_context=create_mock_extract_context([]),
            allow_self=True,
            allowed_peer_ids={peer},
        )
        assert handler.allow_peer is True, "sanity: allow_peer resolves True here"

        for role in ("assistant", "tool", "peer", "user"):
            msg = create_message(role=role, content="peer-tagged output", peer_id=peer)
            target = handler._message_target_id(msg)
            assert target == peer, (
                f"role={role!r} with peer_id={peer!r} should target the peer, "
                f"got {target!r}"
            )

    def test_message_target_id_still_skips_system_messages_with_peer_id(self):
        peer = "web/visitor/alice"
        handler = MemoryIsolationHandler(
            ctx=create_ctx(),
            extract_context=create_mock_extract_context([]),
            allow_self=False,
            allowed_peer_ids={peer},
        )
        # A system prompt accidentally tagged with a peer_id must not leak into
        # peer-scoped memory.
        msg = create_message(role="system", content="sys-prompt", peer_id=peer)
        target = handler._message_target_id(msg)
        assert target is None, (
            "system role + peer_id should not produce a valid peer target_id, "
            f"got {target!r}"
        )

    def test_unique_peer_target_id_in_messages_counts_assistant_tool_user(self):
        """Regression covering the other caller of _is_peer_owner_message."""
        peer = "u/bob"
        msgs = [
            create_message("assistant", "ok", peer_id=peer),
            create_message("tool", "result", peer_id=peer),
            create_message("user", "hi", peer_id=peer),
            create_message("system", "sys", peer_id=peer),
            create_message("user", "no peer"),
        ]
        handler = MemoryIsolationHandler(
            ctx=create_ctx(),
            extract_context=create_mock_extract_context(msgs),
            allowed_peer_ids={peer},
        )
        found = handler._unique_peer_target_id_in_messages()
        assert found == peer, (
            f"Expected assistant+tool+user trio to resolve to peer {peer!r}, got {found!r}"
        )
