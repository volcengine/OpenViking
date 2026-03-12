# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from openviking.storage.queuefs.embedding_msg import EmbeddingMsg


class TestEmbeddingMsg:
    """Unit tests for EmbeddingMsg class."""

    def test_semantic_msg_id_serialization(self):
        """Test semantic_msg_id field serialization via to_dict()."""
        msg = EmbeddingMsg(
            message="test message",
            context_data={"key": "value"},
            semantic_msg_id="semantic-123",
        )
        result = msg.to_dict()
        assert result["semantic_msg_id"] == "semantic-123"
        assert result["message"] == "test message"
        assert result["context_data"] == {"key": "value"}
        assert hasattr(msg, "id")
        assert msg.id is not None

    def test_semantic_msg_id_deserialization(self):
        """Test semantic_msg_id field deserialization via from_dict()."""
        data = {
            "id": "test-id-123",
            "message": "test message",
            "context_data": {"key": "value"},
            "semantic_msg_id": "semantic-456",
        }
        msg = EmbeddingMsg.from_dict(data)
        assert msg.semantic_msg_id == "semantic-456"
        assert msg.id == "test-id-123"
        assert msg.message == "test message"
        assert msg.context_data == {"key": "value"}

    def test_from_dict_missing_semantic_msg_id_defaults_to_none(self):
        """Test from_dict() compatibility with old format (missing semantic_msg_id)."""
        data = {
            "id": "test-id-789",
            "message": "legacy message",
            "context_data": {"legacy": True},
        }
        msg = EmbeddingMsg.from_dict(data)
        assert msg.semantic_msg_id is None
        assert msg.id == "test-id-789"
        assert msg.message == "legacy message"

    def test_from_dict_semantic_msg_id_explicit_none(self):
        """Test from_dict() with explicit None for semantic_msg_id."""
        data = {
            "id": "test-id-none",
            "message": "message with None",
            "context_data": {},
            "semantic_msg_id": None,
        }
        msg = EmbeddingMsg.from_dict(data)
        assert msg.semantic_msg_id is None

    def test_to_json_with_semantic_msg_id(self):
        """Test to_json() method with semantic_msg_id."""
        msg = EmbeddingMsg(
            message="json test",
            context_data={"json_key": "json_value"},
            semantic_msg_id="semantic-json",
        )
        json_str = msg.to_json()
        parsed = json.loads(json_str)
        assert parsed["semantic_msg_id"] == "semantic-json"
        assert parsed["message"] == "json test"
        assert parsed["context_data"] == {"json_key": "json_value"}

    def test_to_json_without_semantic_msg_id(self):
        """Test to_json() method without semantic_msg_id (None)."""
        msg = EmbeddingMsg(
            message="json test no id",
            context_data={"key": "value"},
        )
        json_str = msg.to_json()
        parsed = json.loads(json_str)
        assert parsed["semantic_msg_id"] is None

    def test_from_json_with_semantic_msg_id(self):
        """Test from_json() method with semantic_msg_id."""
        json_str = json.dumps(
            {
                "id": "json-id-123",
                "message": "from json",
                "context_data": {"from": "json"},
                "semantic_msg_id": "semantic-from-json",
            }
        )
        msg = EmbeddingMsg.from_json(json_str)
        assert msg.semantic_msg_id == "semantic-from-json"
        assert msg.id == "json-id-123"
        assert msg.message == "from json"

    def test_from_json_missing_semantic_msg_id(self):
        """Test from_json() with missing semantic_msg_id (backward compatibility)."""
        json_str = json.dumps(
            {
                "id": "json-id-456",
                "message": "legacy json",
                "context_data": {"legacy": True},
            }
        )
        msg = EmbeddingMsg.from_json(json_str)
        assert msg.semantic_msg_id is None
        assert msg.message == "legacy json"

    def test_from_json_invalid_json_raises_value_error(self):
        """Test from_json() raises ValueError for invalid JSON."""
        with pytest.raises(ValueError, match="Invalid JSON string"):
            EmbeddingMsg.from_json("not a valid json")

    def test_message_field_string_type(self):
        """Test message field with string type."""
        msg = EmbeddingMsg(
            message="simple string message",
            context_data={},
        )
        assert isinstance(msg.message, str)
        assert msg.message == "simple string message"

    def test_message_field_list_of_dicts_type(self):
        """Test message field with List[Dict] type."""
        message_list = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        msg = EmbeddingMsg(
            message=message_list,
            context_data={"conversation": True},
        )
        assert isinstance(msg.message, list)
        assert len(msg.message) == 2
        assert msg.message[0]["role"] == "user"
        assert msg.message[1]["content"] == "Hi there"

    def test_message_list_serialization_deserialization(self):
        """Test serialization and deserialization with List[Dict] message."""
        message_list = [
            {"role": "user", "content": "Question?"},
            {"role": "assistant", "content": "Answer."},
        ]
        msg = EmbeddingMsg(
            message=message_list,
            context_data={"type": "qa"},
            semantic_msg_id="qa-123",
        )
        json_str = msg.to_json()
        restored = EmbeddingMsg.from_json(json_str)
        assert isinstance(restored.message, list)
        assert len(restored.message) == 2
        assert restored.message[0]["role"] == "user"
        assert restored.semantic_msg_id == "qa-123"

    def test_id_auto_generated(self):
        """Test that id is auto-generated as UUID."""
        msg = EmbeddingMsg(
            message="test",
            context_data={},
        )
        assert msg.id is not None
        assert len(msg.id) == 36
        assert msg.id.count("-") == 4

    def test_from_dict_preserves_or_generates_id(self):
        """Test from_dict() preserves provided id or generates new one."""
        data_with_id = {
            "id": "preserved-id",
            "message": "test",
            "context_data": {},
        }
        msg = EmbeddingMsg.from_dict(data_with_id)
        assert msg.id == "preserved-id"

        data_without_id = {
            "message": "test",
            "context_data": {},
        }
        msg = EmbeddingMsg.from_dict(data_without_id)
        assert msg.id is not None
        assert len(msg.id) == 36

    def test_empty_context_data(self):
        """Test with empty context_data."""
        msg = EmbeddingMsg(
            message="test",
            context_data={},
            semantic_msg_id="empty-ctx",
        )
        result = msg.to_dict()
        assert result["context_data"] == {}
        assert result["semantic_msg_id"] == "empty-ctx"

    def test_complex_context_data(self):
        """Test with complex nested context_data."""
        complex_data = {
            "nested": {
                "level1": {
                    "level2": ["a", "b", "c"],
                },
            },
            "list": [1, 2, 3],
            "string": "value",
        }
        msg = EmbeddingMsg(
            message="complex test",
            context_data=complex_data,
            semantic_msg_id="complex-123",
        )
        json_str = msg.to_json()
        restored = EmbeddingMsg.from_json(json_str)
        assert restored.context_data["nested"]["level1"]["level2"] == ["a", "b", "c"]
        assert restored.semantic_msg_id == "complex-123"

    def test_semantic_msg_id_empty_string(self):
        """Test semantic_msg_id with empty string."""
        msg = EmbeddingMsg(
            message="test",
            context_data={},
            semantic_msg_id="",
        )
        assert msg.semantic_msg_id == ""
        result = msg.to_dict()
        assert result["semantic_msg_id"] == ""

    def test_roundtrip_string_message(self):
        """Test complete roundtrip with string message."""
        original = EmbeddingMsg(
            message="roundtrip test",
            context_data={"key": "value"},
            semantic_msg_id="roundtrip-id",
        )
        json_str = original.to_json()
        restored = EmbeddingMsg.from_json(json_str)
        assert restored.message == original.message
        assert restored.context_data == original.context_data
        assert restored.semantic_msg_id == original.semantic_msg_id
        assert restored.id is not None
        assert len(restored.id) == 36

    def test_roundtrip_list_message(self):
        """Test complete roundtrip with List[Dict] message."""
        message_list = [
            {"type": "text", "content": "part1"},
            {"type": "code", "content": "print('hello')"},
        ]
        original = EmbeddingMsg(
            message=message_list,
            context_data={"format": "mixed"},
            semantic_msg_id="list-msg-id",
        )
        json_str = original.to_json()
        restored = EmbeddingMsg.from_json(json_str)
        assert restored.message == original.message
        assert restored.semantic_msg_id == "list-msg-id"
