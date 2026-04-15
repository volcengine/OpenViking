# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Comprehensive tests for Message and Part classes."""

import json
from datetime import datetime, timezone

from openviking.message import ContextPart, Message, TextPart, ToolPart
from openviking.message.part import part_from_dict


class TestTextPart:
    """Test TextPart dataclass."""

    def test_default_values(self):
        """Test default values."""
        part = TextPart()

        assert part.text == ""
        assert part.type == "text"

    def test_custom_text(self):
        """Test custom text."""
        part = TextPart(text="Hello, world!")

        assert part.text == "Hello, world!"
        assert part.type == "text"

    def test_empty_text(self):
        """Test empty text."""
        part = TextPart(text="")

        assert part.text == ""
        assert part.type == "text"

    def test_long_text(self):
        """Test long text."""
        long_text = "x" * 10000
        part = TextPart(text=long_text)

        assert part.text == long_text

    def test_unicode_text(self):
        """Test Unicode text."""
        part = TextPart(text="你好世界 🌍")

        assert part.text == "你好世界 🌍"

    def test_multiline_text(self):
        """Test multiline text."""
        part = TextPart(text="Line 1\nLine 2\nLine 3")

        assert "\n" in part.text
        assert part.text.count("\n") == 2


class TestContextPart:
    """Test ContextPart dataclass."""

    def test_default_values(self):
        """Test default values."""
        part = ContextPart()

        assert part.uri == ""
        assert part.context_type == "memory"
        assert part.abstract == ""
        assert part.type == "context"

    def test_custom_values(self):
        """Test custom values."""
        part = ContextPart(
            uri="viking://resources/docs/test.md",
            context_type="resource",
            abstract="This is a test document",
        )

        assert part.uri == "viking://resources/docs/test.md"
        assert part.context_type == "resource"
        assert part.abstract == "This is a test document"
        assert part.type == "context"

    def test_memory_context_type(self):
        """Test memory context type."""
        part = ContextPart(
            uri="viking://memories/profile/test.md",
            context_type="memory",
        )

        assert part.context_type == "memory"

    def test_skill_context_type(self):
        """Test skill context type."""
        part = ContextPart(
            uri="viking://skills/my-skill/",
            context_type="skill",
        )

        assert part.context_type == "skill"

    def test_resource_context_type(self):
        """Test resource context type."""
        part = ContextPart(
            uri="viking://resources/docs/readme.md",
            context_type="resource",
        )

        assert part.context_type == "resource"


class TestToolPart:
    """Test ToolPart dataclass."""

    def test_default_values(self):
        """Test default values."""
        part = ToolPart()

        assert part.tool_id == ""
        assert part.tool_name == ""
        assert part.tool_uri == ""
        assert part.skill_uri == ""
        assert part.tool_input is None
        assert part.tool_output == ""
        assert part.tool_status == "pending"
        assert part.duration_ms is None
        assert part.prompt_tokens is None
        assert part.completion_tokens is None
        assert part.type == "tool"

    def test_custom_values(self):
        """Test custom values."""
        part = ToolPart(
            tool_id="call-123",
            tool_name="search",
            tool_uri="viking://session/test/tools/call-123",
            skill_uri="viking://agent/test/skills/search",
            tool_input={"query": "test"},
            tool_output="Result",
            tool_status="completed",
            duration_ms=150.5,
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert part.tool_id == "call-123"
        assert part.tool_name == "search"
        assert part.tool_uri == "viking://session/test/tools/call-123"
        assert part.skill_uri == "viking://agent/test/skills/search"
        assert part.tool_input == {"query": "test"}
        assert part.tool_output == "Result"
        assert part.tool_status == "completed"
        assert part.duration_ms == 150.5
        assert part.prompt_tokens == 100
        assert part.completion_tokens == 50

    def test_tool_statuses(self):
        """Test various tool statuses."""
        for status in ["pending", "running", "completed", "error"]:
            part = ToolPart(tool_status=status)
            assert part.tool_status == status

    def test_tool_input_none(self):
        """Test tool input can be None."""
        part = ToolPart(tool_input=None)

        assert part.tool_input is None

    def test_tool_input_dict(self):
        """Test tool input as dict."""
        part = ToolPart(tool_input={"key": "value", "nested": {"a": 1}})

        assert part.tool_input["key"] == "value"
        assert part.tool_input["nested"]["a"] == 1

    def test_tool_output_empty(self):
        """Test tool output can be empty."""
        part = ToolPart(tool_output="")

        assert part.tool_output == ""


class TestPartFromDict:
    """Test part_from_dict function."""

    def test_text_part_from_dict(self):
        """Test creating TextPart from dict."""
        data = {"type": "text", "text": "Hello"}

        part = part_from_dict(data)

        assert isinstance(part, TextPart)
        assert part.text == "Hello"
        assert part.type == "text"

    def test_context_part_from_dict(self):
        """Test creating ContextPart from dict."""
        data = {
            "type": "context",
            "uri": "viking://test/",
            "context_type": "resource",
            "abstract": "Test abstract",
        }

        part = part_from_dict(data)

        assert isinstance(part, ContextPart)
        assert part.uri == "viking://test/"
        assert part.context_type == "resource"
        assert part.abstract == "Test abstract"

    def test_tool_part_from_dict(self):
        """Test creating ToolPart from dict."""
        data = {
            "type": "tool",
            "tool_id": "call-123",
            "tool_name": "search",
            "tool_uri": "viking://session/test/tools/call-123",
            "skill_uri": "viking://agent/test/skills/search",
            "tool_input": {"query": "test"},
            "tool_output": "Result",
            "tool_status": "completed",
            "duration_ms": 150.0,
            "prompt_tokens": 100,
            "completion_tokens": 50,
        }

        part = part_from_dict(data)

        assert isinstance(part, ToolPart)
        assert part.tool_id == "call-123"
        assert part.tool_name == "search"
        assert part.tool_status == "completed"

    def test_unknown_type_defaults_to_text(self):
        """Test unknown type defaults to TextPart."""
        data = {"type": "unknown", "value": "something"}

        part = part_from_dict(data)

        assert isinstance(part, TextPart)
        # The entire dict is converted to string
        assert "unknown" in part.text

    def test_missing_type_defaults_to_text(self):
        """Test missing type defaults to TextPart."""
        data = {"text": "Hello"}

        part = part_from_dict(data)

        assert isinstance(part, TextPart)
        assert part.text == "Hello"

    def test_empty_dict(self):
        """Test empty dict creates empty TextPart."""
        data = {}

        part = part_from_dict(data)

        assert isinstance(part, TextPart)
        assert part.text == ""


class TestMessageInit:
    """Test Message initialization."""

    def test_minimal_init(self):
        """Test minimal initialization."""
        msg = Message(
            id="msg-123",
            role="user",
            parts=[TextPart(text="Hello")],
        )

        assert msg.id == "msg-123"
        assert msg.role == "user"
        assert len(msg.parts) == 1
        # Note: created_at defaults to None; to_dict() generates timestamp if None
        assert msg.created_at is None

    def test_with_created_at(self):
        """Test with explicit created_at."""
        now = datetime.now(timezone.utc)
        msg = Message(
            id="msg-123",
            role="assistant",
            parts=[],
            created_at=now,
        )

        assert msg.created_at == now

    def test_user_role(self):
        """Test user role."""
        msg = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text="Hello")],
        )

        assert msg.role == "user"

    def test_assistant_role(self):
        """Test assistant role."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[TextPart(text="Hi there")],
        )

        assert msg.role == "assistant"

    def test_multiple_parts(self):
        """Test message with multiple parts."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                TextPart(text="Here's what I found:"),
                ContextPart(uri="viking://resources/docs/test.md"),
                ToolPart(tool_id="call-1", tool_name="search"),
            ],
        )

        assert len(msg.parts) == 3


class TestMessageContent:
    """Test Message.content property."""

    def test_content_returns_first_text_part(self):
        """Test content returns first TextPart text."""
        msg = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text="Hello")],
        )

        assert msg.content == "Hello"

    def test_content_returns_empty_if_no_text_part(self):
        """Test content returns empty string if no TextPart."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[ContextPart(uri="viking://test/")],
        )

        assert msg.content == ""

    def test_content_ignores_other_parts(self):
        """Test content ignores non-TextPart parts."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                ContextPart(uri="viking://test/"),
                TextPart(text="Hello"),
                ToolPart(tool_id="call-1"),
            ],
        )

        assert msg.content == "Hello"

    def test_content_returns_first_when_multiple_text_parts(self):
        """Test content returns first TextPart when multiple exist."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                TextPart(text="First"),
                TextPart(text="Second"),
            ],
        )

        assert msg.content == "First"


class TestMessageToDict:
    """Test Message.to_dict."""

    def test_to_dict_basic(self):
        """Test basic to_dict conversion."""
        msg = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text="Hello")],
        )

        d = msg.to_dict()

        assert d["id"] == "msg-1"
        assert d["role"] == "user"
        assert len(d["parts"]) == 1
        assert d["parts"][0]["type"] == "text"
        assert d["parts"][0]["text"] == "Hello"
        assert "created_at" in d

    def test_to_dict_with_multiple_parts(self):
        """Test to_dict with multiple parts."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                TextPart(text="Hello"),
                ContextPart(uri="viking://test/", context_type="memory"),
            ],
        )

        d = msg.to_dict()

        assert len(d["parts"]) == 2
        assert d["parts"][0]["type"] == "text"
        assert d["parts"][1]["type"] == "context"

    def test_to_dict_with_tool_part(self):
        """Test to_dict with ToolPart."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                ToolPart(
                    tool_id="call-1",
                    tool_name="search",
                    tool_uri="viking://tools/1",
                    tool_status="completed",
                    duration_ms=100,
                    prompt_tokens=50,
                    completion_tokens=25,
                )
            ],
        )

        d = msg.to_dict()

        assert d["parts"][0]["type"] == "tool"
        assert d["parts"][0]["tool_id"] == "call-1"
        assert d["parts"][0]["duration_ms"] == 100

    def test_to_dict_timestamp_format(self):
        """Test timestamp format in to_dict."""
        now = datetime(2026, 3, 26, 10, 30, 0, tzinfo=timezone.utc)
        msg = Message(
            id="msg-1",
            role="user",
            parts=[],
            created_at=now,
        )

        d = msg.to_dict()

        assert d["created_at"] == "2026-03-26T10:30:00.000Z"


class TestMessageFromDict:
    """Test Message.from_dict."""

    def test_from_dict_basic(self):
        """Test basic from_dict conversion."""
        d = {
            "id": "msg-1",
            "role": "user",
            "parts": [{"type": "text", "text": "Hello"}],
            "created_at": "2026-03-26T10:30:00Z",
        }

        msg = Message.from_dict(d)

        assert msg.id == "msg-1"
        assert msg.role == "user"
        assert len(msg.parts) == 1
        assert isinstance(msg.parts[0], TextPart)
        assert msg.parts[0].text == "Hello"

    def test_from_dict_with_context_part(self):
        """Test from_dict with ContextPart."""
        d = {
            "id": "msg-1",
            "role": "assistant",
            "parts": [
                {
                    "type": "context",
                    "uri": "viking://test/",
                    "context_type": "memory",
                    "abstract": "Test",
                }
            ],
            "created_at": "2026-03-26T10:30:00Z",
        }

        msg = Message.from_dict(d)

        assert isinstance(msg.parts[0], ContextPart)
        assert msg.parts[0].uri == "viking://test/"

    def test_from_dict_with_tool_part(self):
        """Test from_dict with ToolPart."""
        d = {
            "id": "msg-1",
            "role": "assistant",
            "parts": [
                {
                    "type": "tool",
                    "tool_id": "call-1",
                    "tool_name": "search",
                    "tool_uri": "viking://tools/1",
                    "tool_status": "completed",
                }
            ],
            "created_at": "2026-03-26T10:30:00Z",
        }

        msg = Message.from_dict(d)

        assert isinstance(msg.parts[0], ToolPart)
        assert msg.parts[0].tool_id == "call-1"

    def test_from_dict_supports_legacy_content_only_messages(self):
        """Legacy messages with only content should load as a TextPart."""
        d = {
            "id": "msg-legacy",
            "role": "user",
            "content": "Hello from legacy storage",
        }

        msg = Message.from_dict(d)

        assert msg.id == "msg-legacy"
        assert msg.role == "user"
        assert len(msg.parts) == 1
        assert isinstance(msg.parts[0], TextPart)
        assert msg.parts[0].text == "Hello from legacy storage"
        assert msg.content == "Hello from legacy storage"
        assert msg.created_at is None

    def test_roundtrip(self):
        """Test to_dict -> from_dict roundtrip."""
        original = Message(
            id="msg-1",
            role="assistant",
            parts=[
                TextPart(text="Hello"),
                ContextPart(uri="viking://test/", context_type="memory"),
            ],
        )

        d = original.to_dict()
        restored = Message.from_dict(d)

        assert restored.id == original.id
        assert restored.role == original.role
        assert len(restored.parts) == len(original.parts)
        assert isinstance(restored.parts[0], TextPart)
        assert isinstance(restored.parts[1], ContextPart)

    def test_legacy_message_can_be_reloaded_and_extended(self):
        """Legacy content-only rows should survive reload before appending new messages."""
        legacy_row = {
            "id": "msg-legacy",
            "role": "user",
            "content": "Legacy message",
            "created_at": "2026-03-26T10:30:00Z",
        }
        fresh = Message.create_user("Fresh message", msg_id="msg-fresh")

        reloaded_messages = [Message.from_dict(legacy_row), Message.from_dict(fresh.to_dict())]

        assert [message.content for message in reloaded_messages] == [
            "Legacy message",
            "Fresh message",
        ]
        assert [
            json.loads(message.to_jsonl())["parts"][0]["text"] for message in reloaded_messages
        ] == [
            "Legacy message",
            "Fresh message",
        ]


class TestMessageFactoryMethods:
    """Test Message factory methods."""

    def test_create_user(self):
        """Test create_user factory method."""
        msg = Message.create_user("Hello, assistant!")

        assert msg.role == "user"
        assert msg.content == "Hello, assistant!"
        assert len(msg.parts) == 1
        assert msg.id.startswith("msg_")

    def test_create_user_with_id(self):
        """Test create_user with custom ID."""
        msg = Message.create_user("Hello", msg_id="custom-id")

        assert msg.id == "custom-id"

    def test_create_assistant(self):
        """Test create_assistant factory method."""
        msg = Message.create_assistant("Hello, user!")

        assert msg.role == "assistant"
        assert msg.content == "Hello, user!"
        assert len(msg.parts) == 1

    def test_create_assistant_with_context_refs(self):
        """Test create_assistant with context references."""
        msg = Message.create_assistant(
            content="Here's what I found:",
            context_refs=[
                {"uri": "viking://test/1.md", "context_type": "memory"},
                {"uri": "viking://test/2.md", "context_type": "resource"},
            ],
        )

        assert msg.role == "assistant"
        assert len(msg.parts) == 3  # 1 text + 2 context

    def test_create_assistant_with_tool_calls(self):
        """Test create_assistant with tool calls."""
        msg = Message.create_assistant(
            content="Let me search for that.",
            tool_calls=[
                {"id": "call-1", "name": "search", "uri": "viking://tools/1"},
            ],
        )

        assert msg.role == "assistant"
        assert len(msg.parts) == 2  # 1 text + 1 tool

    def test_create_assistant_empty(self):
        """Test create_assistant with no content."""
        msg = Message.create_assistant()

        assert msg.role == "assistant"
        assert msg.content == ""
        assert len(msg.parts) == 0


class TestMessageMethods:
    """Test Message methods."""

    def test_get_context_parts(self):
        """Test get_context_parts method."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                TextPart(text="Hello"),
                ContextPart(uri="viking://test/1.md"),
                TextPart(text="More text"),
                ContextPart(uri="viking://test/2.md"),
            ],
        )

        context_parts = msg.get_context_parts()

        assert len(context_parts) == 2
        assert all(isinstance(p, ContextPart) for p in context_parts)

    def test_get_tool_parts(self):
        """Test get_tool_parts method."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                TextPart(text="Hello"),
                ToolPart(tool_id="call-1"),
                ToolPart(tool_id="call-2"),
            ],
        )

        tool_parts = msg.get_tool_parts()

        assert len(tool_parts) == 2
        assert all(isinstance(p, ToolPart) for p in tool_parts)

    def test_find_tool_part(self):
        """Test find_tool_part method."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                ToolPart(tool_id="call-1"),
                ToolPart(tool_id="call-2"),
            ],
        )

        part = msg.find_tool_part("call-1")

        assert part is not None
        assert part.tool_id == "call-1"

    def test_find_tool_part_not_found(self):
        """Test find_tool_part when not found."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[ToolPart(tool_id="call-1")],
        )

        part = msg.find_tool_part("nonexistent")

        assert part is None

    def test_to_jsonl(self):
        """Test to_jsonl method."""
        msg = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text="Hello")],
        )

        jsonl = msg.to_jsonl()

        # Should be valid JSON
        parsed = json.loads(jsonl)
        assert parsed["id"] == "msg-1"
        assert parsed["role"] == "user"

    def test_to_jsonl_unicode(self):
        """Test to_jsonl with Unicode."""
        msg = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text="你好世界")],
        )

        jsonl = msg.to_jsonl()

        assert "你好世界" in jsonl


class TestMessageEdgeCases:
    """Test edge cases for Message."""

    def test_empty_parts(self):
        """Test message with empty parts."""
        msg = Message(
            id="msg-1",
            role="user",
            parts=[],
        )

        assert len(msg.parts) == 0
        assert msg.content == ""

    def test_very_long_text(self):
        """Test message with very long text."""
        long_text = "x" * 100000
        msg = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text=long_text)],
        )

        assert len(msg.content) == 100000

    def test_special_characters_in_text(self):
        """Test message with special characters."""
        msg = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text='Special: "quotes", \n newlines, \t tabs')],
        )

        assert '"' in msg.content
        assert "\n" in msg.content
        assert "\t" in msg.content

    def test_json_in_tool_input(self):
        """Test tool input with nested JSON."""
        msg = Message(
            id="msg-1",
            role="assistant",
            parts=[
                ToolPart(
                    tool_id="call-1",
                    tool_input={"nested": {"key": "value"}, "array": [1, 2, 3]},
                )
            ],
        )

        assert msg.parts[0].tool_input["nested"]["key"] == "value"
        assert msg.parts[0].tool_input["array"] == [1, 2, 3]

    def test_multiple_messages_independence(self):
        """Test multiple messages are independent."""
        msg1 = Message(
            id="msg-1",
            role="user",
            parts=[TextPart(text="First")],
        )
        msg2 = Message(
            id="msg-2",
            role="user",
            parts=[TextPart(text="Second")],
        )

        assert msg1.content == "First"
        assert msg2.content == "Second"
        assert msg1.id != msg2.id
