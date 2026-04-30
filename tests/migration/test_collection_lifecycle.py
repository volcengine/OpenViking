# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""RED-phase tests for CollectionLifecycle.

All tests MUST fail because the CollectionLifecycle module doesn't exist yet.
They define the expected API contract for the TDD GREEN phase.
"""

from unittest.mock import MagicMock, PropertyMock

import pytest

from openviking.storage.migration.collection_lifecycle import CollectionLifecycle


# =========================================================================
# Helpers
# =========================================================================


def _make_mock_adapter(
    name: str = "context",
    collection_exists: bool = False,
    collection_info: dict | None = None,
) -> MagicMock:
    """Create a mock CollectionAdapter with configurable behavior."""
    adapter = MagicMock()
    adapter.collection_name = name
    adapter.collection_exists.return_value = collection_exists
    adapter.create_collection.return_value = True
    adapter.drop_collection.return_value = True
    adapter.get_collection_info.return_value = collection_info or {
        "CollectionName": name,
        "Description": "Unified context collection",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "type", "FieldType": "string"},
            {"FieldName": "context_type", "FieldType": "string"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 3072},
            {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
            {"FieldName": "created_at", "FieldType": "date_time"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "active_count", "FieldType": "int64"},
            {"FieldName": "level", "FieldType": "int64"},
            {"FieldName": "name", "FieldType": "string"},
            {"FieldName": "description", "FieldType": "string"},
            {"FieldName": "tags", "FieldType": "string"},
            {"FieldName": "abstract", "FieldType": "string"},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "owner_user_id", "FieldType": "string"},
            {"FieldName": "owner_agent_id", "FieldType": "string"},
        ],
        "ScalarIndex": [
            "uri", "type", "context_type", "created_at", "updated_at",
            "active_count", "level", "name", "tags", "account_id",
            "owner_user_id", "owner_agent_id",
        ],
    }
    return adapter


# =========================================================================
# Tests for generate_target_name
# =========================================================================


class TestGenerateTargetName:
    """Tests for CollectionLifecycle.generate_target_name()."""

    def test_target_collection_naming_convention(self):
        """generate_target_name('context', 'v2') should return 'context_v2'."""
        name = CollectionLifecycle.generate_target_name("context", "v2")
        assert name == "context_v2"

    def test_target_collection_naming_with_special_chars(self):
        """generate_target_name should sanitize special characters in embedder name."""
        name = CollectionLifecycle.generate_target_name("context", "my-embedder-v2")
        # The embedder name may contain hyphens; the method should handle them
        assert name.startswith("context_")
        assert len(name) > len("context_")


# =========================================================================
# Tests for create_target_collection
# =========================================================================


class TestCreateTargetCollection:
    """Tests for CollectionLifecycle.create_target_collection()."""

    def test_create_target_collection(self):
        """Create target collection reusing source schema but with target dimension."""
        source_adapter = _make_mock_adapter(
            name="context",
            collection_exists=True,
        )
        target_adapter = _make_mock_adapter(
            name="context_v2",
            collection_exists=False,
        )
        target_dimension = 1024

        result = CollectionLifecycle.create_target_collection(
            source_adapter=source_adapter,
            target_adapter=target_adapter,
            target_dimension=target_dimension,
        )

        assert result is True
        # Source schema should have been read
        source_adapter.get_collection_info.assert_called_once()
        # Target adapter should have been called with modified schema
        target_adapter.create_collection.assert_called_once()
        call_kwargs = target_adapter.create_collection.call_args.kwargs
        assert call_kwargs["name"] == "context_v2"
        # The vector field dimension should be the target dimension, not source
        fields = call_kwargs["schema"]["Fields"]
        vector_field = next(f for f in fields if f["FieldName"] == "vector")
        assert vector_field["Dim"] == target_dimension

    def test_create_existing_collection_rejects(self):
        """create_target_collection should reject when target already exists."""
        source_adapter = _make_mock_adapter(name="context", collection_exists=True)
        target_adapter = _make_mock_adapter(name="context_v2", collection_exists=True)

        with pytest.raises((ValueError, RuntimeError), match="already exists|exists"):
            CollectionLifecycle.create_target_collection(
                source_adapter=source_adapter,
                target_adapter=target_adapter,
                target_dimension=1024,
            )

        # create_collection should NOT have been called
        target_adapter.create_collection.assert_not_called()


# =========================================================================
# Tests for drop_target_collection
# =========================================================================


class TestDropTargetCollection:
    """Tests for CollectionLifecycle.drop_target_collection()."""

    def test_drop_target_collection(self):
        """drop_target_collection should drop the target collection."""
        target_adapter = _make_mock_adapter(
            name="context_v2",
            collection_exists=True,
        )

        result = CollectionLifecycle.drop_target_collection(target_adapter)

        assert result is True
        target_adapter.drop_collection.assert_called_once()

    def test_drop_target_collection_while_dual_write_rejects(self):
        """drop_target_collection should reject when dual-write is active."""
        target_adapter = _make_mock_adapter(
            name="context_v2",
            collection_exists=True,
        )

        with pytest.raises((ValueError, RuntimeError), match="dual.write|active"):
            CollectionLifecycle.drop_target_collection(
                target_adapter,
                dual_write_active=True,
            )

        # drop_collection should NOT have been called
        target_adapter.drop_collection.assert_not_called()
