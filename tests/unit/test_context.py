# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Comprehensive tests for Context class."""

from datetime import datetime, timezone

from openviking.core.context import (
    Context,
    ContextLevel,
    ContextType,
    ResourceContentType,
    Vectorize,
)
from openviking_cli.session.user_id import UserIdentifier


class TestResourceContentType:
    """Test ResourceContentType enum."""

    def test_text_type(self):
        """Test TEXT content type."""
        assert ResourceContentType.TEXT == "text"
        assert ResourceContentType.TEXT.value == "text"

    def test_image_type(self):
        """Test IMAGE content type."""
        assert ResourceContentType.IMAGE == "image"
        assert ResourceContentType.IMAGE.value == "image"

    def test_video_type(self):
        """Test VIDEO content type."""
        assert ResourceContentType.VIDEO == "video"
        assert ResourceContentType.VIDEO.value == "video"

    def test_audio_type(self):
        """Test AUDIO content type."""
        assert ResourceContentType.AUDIO == "audio"
        assert ResourceContentType.AUDIO.value == "audio"

    def test_binary_type(self):
        """Test BINARY content type."""
        assert ResourceContentType.BINARY == "binary"
        assert ResourceContentType.BINARY.value == "binary"


class TestContextType:
    """Test ContextType enum."""

    def test_skill_type(self):
        """Test SKILL context type."""
        assert ContextType.SKILL == "skill"
        assert ContextType.SKILL.value == "skill"

    def test_memory_type(self):
        """Test MEMORY context type."""
        assert ContextType.MEMORY == "memory"
        assert ContextType.MEMORY.value == "memory"

    def test_resource_type(self):
        """Test RESOURCE context type."""
        assert ContextType.RESOURCE == "resource"
        assert ContextType.RESOURCE.value == "resource"


class TestContextLevel:
    """Test ContextLevel enum."""

    def test_abstract_level(self):
        """Test ABSTRACT (L0) level."""
        assert ContextLevel.ABSTRACT == 0
        assert ContextLevel.ABSTRACT.value == 0

    def test_overview_level(self):
        """Test OVERVIEW (L1) level."""
        assert ContextLevel.OVERVIEW == 1
        assert ContextLevel.OVERVIEW.value == 1

    def test_detail_level(self):
        """Test DETAIL (L2) level."""
        assert ContextLevel.DETAIL == 2
        assert ContextLevel.DETAIL.value == 2


class TestVectorize:
    """Test Vectorize class."""

    def test_default_text(self):
        """Test default text is empty."""
        v = Vectorize()
        assert v.text == ""

    def test_custom_text(self):
        """Test custom text."""
        v = Vectorize(text="Hello world")
        assert v.text == "Hello world"


class TestContextInit:
    """Test Context initialization."""

    def test_minimal_init(self):
        """Test minimal initialization."""
        ctx = Context(uri="viking://resources/test/")

        assert ctx.uri == "viking://resources/test/"
        assert ctx.is_leaf is False
        assert ctx.abstract == ""
        assert ctx.context_type == "resource"
        assert ctx.active_count == 0
        assert ctx.meta == {}

    def test_full_init(self):
        """Test full initialization."""
        now = datetime.now(timezone.utc)
        ctx = Context(
            uri="viking://user/test/memories/profile/test.md",
            parent_uri="viking://user/test/memories/profile/",
            is_leaf=True,
            abstract="Test abstract",
            context_type="memory",
            category="profile",
            created_at=now,
            updated_at=now,
            active_count=5,
            related_uri=["viking://other/"],
            meta={"key": "value"},
            level=1,
        )

        assert ctx.uri == "viking://user/test/memories/profile/test.md"
        assert ctx.parent_uri == "viking://user/test/memories/profile/"
        assert ctx.is_leaf is True
        assert ctx.abstract == "Test abstract"
        assert ctx.context_type == "memory"
        assert ctx.category == "profile"
        assert ctx.created_at == now
        assert ctx.updated_at == now
        assert ctx.active_count == 5
        assert ctx.related_uri == ["viking://other/"]
        assert ctx.meta == {"key": "value"}
        assert ctx.level == 1

    def test_auto_generated_id(self):
        """Test auto-generated ID."""
        ctx = Context(uri="viking://test/")

        assert ctx.id is not None
        assert len(ctx.id) == 36  # UUID format

    def test_custom_id(self):
        """Test custom ID."""
        ctx = Context(uri="viking://test/", id="custom-id-123")

        assert ctx.id == "custom-id-123"

    def test_auto_timestamps(self):
        """Test auto-generated timestamps."""
        ctx = Context(uri="viking://test/")

        assert ctx.created_at is not None
        assert ctx.updated_at is not None
        assert ctx.created_at.tzinfo is not None

    def test_vectorize_initialized(self):
        """Test vectorize is initialized."""
        ctx = Context(uri="viking://test/", abstract="Test")

        assert ctx.vectorize is not None
        assert ctx.vectorize.text == "Test"


class TestContextDeriveContextType:
    """Test Context._derive_context_type."""

    def test_derive_skill(self):
        """Test deriving skill type from URI."""
        ctx = Context(uri="viking://agent/test/skills/my-skill/")

        assert ctx.context_type == "skill"

    def test_derive_memory(self):
        """Test deriving memory type from URI."""
        ctx = Context(uri="viking://user/test/memories/preferences/test.md")

        assert ctx.context_type == "memory"

    def test_derive_resource_default(self):
        """Test deriving resource type (default)."""
        ctx = Context(uri="viking://resources/docs/readme.md")

        assert ctx.context_type == "resource"

    def test_explicit_context_type_overrides(self):
        """Test explicit context_type overrides derivation."""
        ctx = Context(uri="viking://resources/docs/readme.md", context_type="skill")

        assert ctx.context_type == "skill"


class TestContextDeriveCategory:
    """Test Context._derive_category."""

    def test_derive_patterns(self):
        """Test deriving patterns category."""
        ctx = Context(uri="viking://memories/patterns/test.md")

        assert ctx.category == "patterns"

    def test_derive_cases(self):
        """Test deriving cases category."""
        ctx = Context(uri="viking://memories/cases/test.md")

        assert ctx.category == "cases"

    def test_derive_profile(self):
        """Test deriving profile category."""
        ctx = Context(uri="viking://memories/profile/test.md")

        assert ctx.category == "profile"

    def test_derive_preferences(self):
        """Test deriving preferences category."""
        ctx = Context(uri="viking://memories/preferences/test.md")

        assert ctx.category == "preferences"

    def test_derive_entities(self):
        """Test deriving entities category."""
        ctx = Context(uri="viking://memories/entities/test.md")

        assert ctx.category == "entities"

    def test_derive_events(self):
        """Test deriving events category."""
        ctx = Context(uri="viking://memories/events/test.md")

        assert ctx.category == "events"

    def test_derive_empty_default(self):
        """Test deriving empty category (default)."""
        ctx = Context(uri="viking://resources/docs/test.md")

        assert ctx.category == ""

    def test_explicit_category_overrides(self):
        """Test explicit category overrides derivation."""
        ctx = Context(uri="viking://resources/docs/test.md", category="custom")

        assert ctx.category == "custom"


class TestContextMethods:
    """Test Context methods."""

    def test_get_context_type(self):
        """Test get_context_type method."""
        ctx = Context(uri="viking://test/", context_type="skill")

        assert ctx.get_context_type() == "skill"

    def test_set_vectorize(self):
        """Test set_vectorize method."""
        ctx = Context(uri="viking://test/")
        v = Vectorize(text="New text")
        ctx.set_vectorize(v)

        assert ctx.vectorize.text == "New text"

    def test_get_vectorization_text(self):
        """Test get_vectorization_text method."""
        ctx = Context(uri="viking://test/", abstract="Test abstract")

        assert ctx.get_vectorization_text() == "Test abstract"

    def test_update_activity(self):
        """Test update_activity method."""
        ctx = Context(uri="viking://test/", active_count=5)
        old_updated = ctx.updated_at

        ctx.update_activity()

        assert ctx.active_count == 6
        assert ctx.updated_at > old_updated


class TestContextToDict:
    """Test Context.to_dict."""

    def test_to_dict_basic(self):
        """Test basic to_dict conversion."""
        ctx = Context(
            uri="viking://test/",
            is_leaf=True,
            abstract="Test abstract",
        )

        d = ctx.to_dict()

        assert d["uri"] == "viking://test/"
        assert d["is_leaf"] is True
        assert d["abstract"] == "Test abstract"
        assert d["context_type"] == "resource"
        assert "id" in d
        assert "created_at" in d
        assert "updated_at" in d

    def test_to_dict_with_optional_fields(self):
        """Test to_dict with optional fields."""
        ctx = Context(
            uri="viking://test/",
            parent_uri="viking://parent/",
            temp_uri="viking://temp/",
            related_uri=["viking://related/"],
            meta={"key": "value"},
            level=1,
            session_id="session-123",
        )

        d = ctx.to_dict()

        assert d["parent_uri"] == "viking://parent/"
        assert d["temp_uri"] == "viking://temp/"
        assert d["related_uri"] == ["viking://related/"]
        assert d["meta"] == {"key": "value"}
        assert d["level"] == 1
        assert d["session_id"] == "session-123"

    def test_to_dict_skill_type(self):
        """Test to_dict adds skill-specific fields."""
        ctx = Context(
            uri="viking://skills/test/",
            context_type="skill",
            meta={"name": "my-skill", "description": "A skill"},
        )

        d = ctx.to_dict()

        assert d["name"] == "my-skill"
        assert d["description"] == "A skill"

    def test_to_dict_timestamps_format(self):
        """Test timestamps are formatted as ISO strings."""
        now = datetime(2026, 3, 26, 10, 30, 0, tzinfo=timezone.utc)
        ctx = Context(
            uri="viking://test/",
            created_at=now,
            updated_at=now,
        )

        d = ctx.to_dict()

        assert d["created_at"] == "2026-03-26T10:30:00.000Z"
        assert d["updated_at"] == "2026-03-26T10:30:00.000Z"


class TestContextFromDict:
    """Test Context.from_dict."""

    def test_from_dict_basic(self):
        """Test basic from_dict conversion."""
        d = {
            "uri": "viking://test/",
            "is_leaf": True,
            "abstract": "Test abstract",
            "context_type": "memory",
            "category": "profile",
        }

        ctx = Context.from_dict(d)

        assert ctx.uri == "viking://test/"
        assert ctx.is_leaf is True
        assert ctx.abstract == "Test abstract"
        assert ctx.context_type == "memory"
        assert ctx.category == "profile"

    def test_from_dict_with_timestamps(self):
        """Test from_dict with ISO timestamps."""
        d = {
            "uri": "viking://test/",
            "created_at": "2026-03-26T10:30:00Z",
            "updated_at": "2026-03-26T11:30:00Z",
        }

        ctx = Context.from_dict(d)

        assert ctx.created_at.year == 2026
        assert ctx.created_at.month == 3
        assert ctx.created_at.day == 26
        assert ctx.created_at.hour == 10

    def test_from_dict_with_level(self):
        """Test from_dict with level."""
        d = {
            "uri": "viking://test/",
            "level": 1,
        }

        ctx = Context.from_dict(d)

        assert ctx.level == 1

    def test_from_dict_with_user(self):
        """Test from_dict with user."""
        d = {
            "uri": "viking://test/",
            "user": {
                "account_id": "account-123",
                "user_id": "user-123",
                "agent_id": "agent-456",
            },
        }

        ctx = Context.from_dict(d)

        assert ctx.user is not None
        assert ctx.user.user_id == "user-123"
        assert ctx.user.account_id == "account-123"

    def test_from_dict_with_vector(self):
        """Test from_dict with vector."""
        d = {
            "uri": "viking://test/",
            "vector": [0.1, 0.2, 0.3],
        }

        ctx = Context.from_dict(d)

        assert ctx.vector == [0.1, 0.2, 0.3]

    def test_roundtrip(self):
        """Test to_dict -> from_dict roundtrip."""
        original = Context(
            uri="viking://test/",
            parent_uri="viking://parent/",
            is_leaf=True,
            abstract="Test abstract",
            context_type="memory",
            category="profile",
            active_count=5,
            related_uri=["viking://related/"],
            meta={"key": "value"},
            level=1,
            session_id="session-123",
        )

        d = original.to_dict()
        restored = Context.from_dict(d)

        assert restored.uri == original.uri
        assert restored.parent_uri == original.parent_uri
        assert restored.is_leaf == original.is_leaf
        assert restored.abstract == original.abstract
        assert restored.context_type == original.context_type
        assert restored.category == original.category
        assert restored.active_count == original.active_count
        assert restored.related_uri == original.related_uri
        assert restored.meta == original.meta
        assert restored.level == original.level
        assert restored.session_id == original.session_id


class TestContextWithUser:
    """Test Context with UserIdentifier."""

    def test_user_account_id_inheritance(self):
        """Test account_id inherited from user."""
        user = UserIdentifier(account_id="account-123", user_id="user-123", agent_id="agent-456")
        ctx = Context(uri="viking://test/", user=user)

        assert ctx.account_id == user.account_id

    def test_owner_space_agent(self):
        """Test owner_space for agent URI."""
        user = UserIdentifier(account_id="account-123", user_id="user-123", agent_id="agent-456")
        ctx = Context(uri="viking://agent/test/skills/my-skill/", user=user)

        assert ctx.owner_space == user.agent_space_name()

    def test_owner_space_user(self):
        """Test owner_space for user URI."""
        user = UserIdentifier(account_id="account-123", user_id="user-123", agent_id="agent-456")
        ctx = Context(uri="viking://user/test/memories/test.md", user=user)

        assert ctx.owner_space == user.user_space_name()

    def test_owner_space_session(self):
        """Test owner_space for session URI."""
        user = UserIdentifier(account_id="account-123", user_id="user-123", agent_id="agent-456")
        ctx = Context(uri="viking://session/test/msg/1.md", user=user)

        assert ctx.owner_space == user.user_space_name()

    def test_owner_space_resource_default(self):
        """Test owner_space default for resource URI."""
        user = UserIdentifier(account_id="account-123", user_id="user-123", agent_id="agent-456")
        ctx = Context(uri="viking://resources/docs/test.md", user=user)

        # Resource URIs don't match agent/user/session patterns
        assert ctx.owner_space == ""


class TestContextEdgeCases:
    """Test edge cases for Context."""

    def test_level_string_to_int(self):
        """Test level conversion from string to int."""
        # The code tries to convert level to int
        ctx = Context(uri="viking://test/", level="1")

        assert ctx.level == 1

    def test_level_none(self):
        """Test level None."""
        ctx = Context(uri="viking://test/", level=None)

        assert ctx.level is None

    def test_level_invalid_string(self):
        """Test invalid level string becomes None."""
        ctx = Context(uri="viking://test/", level="invalid")

        assert ctx.level is None

    def test_empty_abstract(self):
        """Test empty abstract."""
        ctx = Context(uri="viking://test/", abstract="")

        assert ctx.abstract == ""
        assert ctx.get_vectorization_text() == ""

    def test_long_abstract(self):
        """Test long abstract."""
        long_text = "x" * 10000
        ctx = Context(uri="viking://test/", abstract=long_text)

        assert ctx.abstract == long_text
        assert ctx.get_vectorization_text() == long_text

    def test_unicode_in_abstract(self):
        """Test Unicode in abstract."""
        ctx = Context(uri="viking://test/", abstract="你好世界 🌍")

        assert ctx.abstract == "你好世界 🌍"

    def test_special_chars_in_uri(self):
        """Test special characters in URI."""
        ctx = Context(uri="viking://test/path%20with%20spaces/")

        assert ctx.uri == "viking://test/path%20with%20spaces/"
