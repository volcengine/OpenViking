# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for SemanticMsg dataclass, focusing on new fields target_uri and skip_vectorization."""

import json

import pytest

from openviking.storage.queuefs.semantic_msg import SemanticMsg


class TestTargetUriField:
    """Tests for target_uri field serialization and deserialization."""

    def test_target_uri_default_value(self):
        msg = SemanticMsg(uri="viking://resource/test", context_type="resource")
        assert msg.target_uri == ""

    def test_target_uri_set_in_constructor(self):
        msg = SemanticMsg(
            uri="viking://resource/temp",
            context_type="resource",
            target_uri="viking://resource/target",
        )
        assert msg.target_uri == "viking://resource/target"

    def test_target_uri_serialization_to_dict(self):
        msg = SemanticMsg(
            uri="viking://resource/temp",
            context_type="resource",
            target_uri="viking://resource/target",
        )
        data = msg.to_dict()
        assert "target_uri" in data
        assert data["target_uri"] == "viking://resource/target"

    def test_target_uri_deserialization_from_dict(self):
        data = {
            "uri": "viking://resource/temp",
            "context_type": "resource",
            "target_uri": "viking://resource/target",
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.target_uri == "viking://resource/target"

    def test_target_uri_empty_string_serialization(self):
        msg = SemanticMsg(
            uri="viking://resource/test",
            context_type="resource",
            target_uri="",
        )
        data = msg.to_dict()
        assert data["target_uri"] == ""

    def test_target_uri_with_memory_context(self):
        msg = SemanticMsg(
            uri="viking://memory/temp/session",
            context_type="memory",
            target_uri="viking://session/abc123",
        )
        data = msg.to_dict()
        msg_restored = SemanticMsg.from_dict(data)
        assert msg_restored.target_uri == "viking://session/abc123"


class TestSkipVectorizationField:
    """Tests for skip_vectorization field serialization and deserialization."""

    def test_skip_vectorization_default_value(self):
        msg = SemanticMsg(uri="viking://resource/test", context_type="resource")
        assert msg.skip_vectorization is False

    def test_skip_vectorization_set_true_in_constructor(self):
        msg = SemanticMsg(
            uri="viking://resource/test",
            context_type="resource",
            skip_vectorization=True,
        )
        assert msg.skip_vectorization is True

    def test_skip_vectorization_serialization_to_dict(self):
        msg = SemanticMsg(
            uri="viking://resource/test",
            context_type="resource",
            skip_vectorization=True,
        )
        data = msg.to_dict()
        assert "skip_vectorization" in data
        assert data["skip_vectorization"] is True

    def test_skip_vectorization_deserialization_from_dict(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
            "skip_vectorization": True,
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.skip_vectorization is True

    def test_skip_vectorization_false_serialization(self):
        msg = SemanticMsg(
            uri="viking://resource/test",
            context_type="resource",
            skip_vectorization=False,
        )
        data = msg.to_dict()
        assert data["skip_vectorization"] is False

    def test_skip_vectorization_round_trip(self):
        original = SemanticMsg(
            uri="viking://resource/test",
            context_type="resource",
            skip_vectorization=True,
        )
        restored = SemanticMsg.from_dict(original.to_dict())
        assert restored.skip_vectorization == original.skip_vectorization


class TestFromDictBackwardCompatibility:
    """Tests for from_dict() backward compatibility with old format missing new fields."""

    def test_missing_target_uri_uses_default(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.target_uri == ""

    def test_missing_skip_vectorization_uses_default(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.skip_vectorization is False

    def test_missing_both_new_fields_uses_defaults(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
            "recursive": True,
            "account_id": "test_account",
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.target_uri == ""
        assert msg.skip_vectorization is False

    def test_old_format_with_all_legacy_fields(self):
        data = {
            "id": "legacy-id-123",
            "uri": "viking://resource/test",
            "context_type": "resource",
            "status": "pending",
            "recursive": False,
            "account_id": "account1",
            "user_id": "user1",
            "agent_id": "agent1",
            "role": "admin",
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.id == "legacy-id-123"
        assert msg.uri == "viking://resource/test"
        assert msg.context_type == "resource"
        assert msg.status == "pending"
        assert msg.recursive is False
        assert msg.target_uri == ""
        assert msg.skip_vectorization is False

    def test_partial_new_fields_only_target_uri(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
            "target_uri": "viking://resource/target",
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.target_uri == "viking://resource/target"
        assert msg.skip_vectorization is False

    def test_partial_new_fields_only_skip_vectorization(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
            "skip_vectorization": True,
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.target_uri == ""
        assert msg.skip_vectorization is True


class TestToJsonFromJson:
    """Tests for to_json() and from_json() methods."""

    def test_to_json_returns_valid_json_string(self):
        msg = SemanticMsg(
            uri="viking://resource/test",
            context_type="resource",
        )
        json_str = msg.to_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_from_json_creates_valid_object(self):
        json_str = '{"uri": "viking://resource/test", "context_type": "resource"}'
        msg = SemanticMsg.from_json(json_str)
        assert msg.uri == "viking://resource/test"
        assert msg.context_type == "resource"

    def test_to_json_and_from_json_round_trip(self):
        original = SemanticMsg(
            uri="viking://resource/temp",
            context_type="memory",
            target_uri="viking://session/abc",
            skip_vectorization=True,
            recursive=False,
            account_id="test_account",
            user_id="test_user",
            agent_id="test_agent",
            role="admin",
        )
        json_str = original.to_json()
        restored = SemanticMsg.from_json(json_str)

        assert restored.uri == original.uri
        assert restored.context_type == original.context_type
        assert restored.target_uri == original.target_uri
        assert restored.skip_vectorization == original.skip_vectorization
        assert restored.recursive == original.recursive
        assert restored.account_id == original.account_id
        assert restored.user_id == original.user_id
        assert restored.agent_id == original.agent_id
        assert restored.role == original.role

    def test_from_json_with_new_fields(self):
        json_str = json.dumps(
            {
                "uri": "viking://resource/test",
                "context_type": "resource",
                "target_uri": "viking://resource/target",
                "skip_vectorization": True,
            }
        )
        msg = SemanticMsg.from_json(json_str)
        assert msg.target_uri == "viking://resource/target"
        assert msg.skip_vectorization is True

    def test_from_json_without_new_fields(self):
        json_str = json.dumps(
            {
                "uri": "viking://resource/test",
                "context_type": "resource",
            }
        )
        msg = SemanticMsg.from_json(json_str)
        assert msg.target_uri == ""
        assert msg.skip_vectorization is False

    def test_from_json_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid JSON string"):
            SemanticMsg.from_json("not a valid json")

    def test_from_json_missing_required_fields_raises_value_error(self):
        json_str = '{"uri": "viking://resource/test"}'
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_json(json_str)


class TestRequiredFieldsValidation:
    """Tests for required field validation (uri and context_type)."""

    def test_missing_uri_raises_value_error(self):
        data = {"context_type": "resource"}
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_dict(data)

    def test_missing_context_type_raises_value_error(self):
        data = {"uri": "viking://resource/test"}
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_dict(data)

    def test_missing_both_required_fields_raises_value_error(self):
        data = {"target_uri": "viking://resource/target"}
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_dict(data)

    def test_empty_uri_raises_value_error(self):
        data = {"uri": "", "context_type": "resource"}
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_dict(data)

    def test_empty_context_type_raises_value_error(self):
        data = {"uri": "viking://resource/test", "context_type": ""}
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_dict(data)

    def test_none_uri_raises_value_error(self):
        data = {"uri": None, "context_type": "resource"}
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_dict(data)

    def test_none_context_type_raises_value_error(self):
        data = {"uri": "viking://resource/test", "context_type": None}
        with pytest.raises(ValueError, match="Missing required fields"):
            SemanticMsg.from_dict(data)

    def test_empty_dict_raises_value_error(self):
        with pytest.raises(ValueError, match="Data dictionary is empty"):
            SemanticMsg.from_dict({})

    def test_valid_minimal_data_succeeds(self):
        data = {"uri": "viking://resource/test", "context_type": "resource"}
        msg = SemanticMsg.from_dict(data)
        assert msg.uri == "viking://resource/test"
        assert msg.context_type == "resource"

    def test_error_message_lists_all_missing_fields(self):
        data = {"skip_vectorization": True}
        with pytest.raises(ValueError) as exc_info:
            SemanticMsg.from_dict(data)
        error_msg = str(exc_info.value)
        assert "uri" in error_msg
        assert "context_type" in error_msg


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_target_uri_with_special_characters(self):
        special_uri = "viking://resource/test%20space?query=value&other=123"
        msg = SemanticMsg(
            uri="viking://resource/temp",
            context_type="resource",
            target_uri=special_uri,
        )
        restored = SemanticMsg.from_dict(msg.to_dict())
        assert restored.target_uri == special_uri

    def test_target_uri_with_unicode(self):
        unicode_uri = "viking://resource/测试/目录"
        msg = SemanticMsg(
            uri="viking://resource/temp",
            context_type="resource",
            target_uri=unicode_uri,
        )
        restored = SemanticMsg.from_dict(msg.to_dict())
        assert restored.target_uri == unicode_uri

    def test_target_uri_with_long_path(self):
        long_path = "viking://resource/" + "/".join(["dir"] * 100)
        msg = SemanticMsg(
            uri="viking://resource/temp",
            context_type="resource",
            target_uri=long_path,
        )
        restored = SemanticMsg.from_dict(msg.to_dict())
        assert restored.target_uri == long_path

    def test_preserves_existing_id_in_from_dict(self):
        original = SemanticMsg(
            uri="viking://resource/test",
            context_type="resource",
        )
        original_id = original.id
        data = original.to_dict()
        restored = SemanticMsg.from_dict(data)
        assert restored.id == original_id

    def test_from_dict_overwrites_id_if_provided(self):
        data = {
            "id": "custom-id-123",
            "uri": "viking://resource/test",
            "context_type": "resource",
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.id == "custom-id-123"

    def test_from_dict_preserves_status_and_timestamp(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
            "status": "completed",
            "timestamp": 1700000000,
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.status == "completed"
        assert msg.timestamp == 1700000000

    def test_all_context_types(self):
        context_types = ["resource", "memory", "skill", "session"]
        for ctx_type in context_types:
            msg = SemanticMsg(
                uri=f"viking://{ctx_type}/test",
                context_type=ctx_type,
            )
            assert msg.context_type == ctx_type
            restored = SemanticMsg.from_dict(msg.to_dict())
            assert restored.context_type == ctx_type

    def test_extra_fields_in_dict_are_ignored(self):
        data = {
            "uri": "viking://resource/test",
            "context_type": "resource",
            "extra_field": "should_be_ignored",
            "another_extra": 12345,
        }
        msg = SemanticMsg.from_dict(data)
        assert msg.uri == "viking://resource/test"
        assert not hasattr(msg, "extra_field")
        assert not hasattr(msg, "another_extra")
