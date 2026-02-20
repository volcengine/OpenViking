# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for Qdrant project implementation.

Prerequisites:
    - Qdrant server running on localhost:6333
    - qdrant-client installed: pip install qdrant-client

Run tests:
    pytest tests/vectordb/test_qdrant_project.py -v
"""

import unittest
import uuid

import pytest

# Skip all tests if qdrant-client is not installed
pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient

from openviking.storage.vectordb.project.qdrant_project import (
    QdrantProject,
    get_or_create_qdrant_project,
)


def is_qdrant_available():
    """Check if Qdrant server is available."""
    try:
        client = QdrantClient(url="http://localhost:6333", timeout=5)
        client.get_collections()
        return True
    except Exception:
        return False


# Skip all tests if Qdrant server is not available
pytestmark = pytest.mark.skipif(
    not is_qdrant_available(),
    reason="Qdrant server not available on localhost:6333"
)


class TestQdrantProjectBasic(unittest.TestCase):
    """Basic project operations."""

    def setUp(self):
        """Set up test project."""
        self.project_name = f"test_project_{uuid.uuid4().hex[:8]}"
        self.project = QdrantProject(
            url="http://localhost:6333",
            project_name=self.project_name,
            vector_dim=4,
            distance_metric="cosine",
        )
        self.created_collections = []

    def tearDown(self):
        """Clean up created collections."""
        for name in self.created_collections:
            try:
                self.project.drop_collection(name)
            except Exception:
                pass
        self.project.close()

    def test_list_collections(self):
        """Should list existing collections."""
        collections = self.project.list_collections()
        self.assertIsInstance(collections, list)

    def test_create_collection(self):
        """Should create a new collection."""
        collection_name = f"test_create_{uuid.uuid4().hex[:8]}"
        self.created_collections.append(collection_name)

        schema = {
            "CollectionName": collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }

        collection = self.project.create_collection(collection_name, schema)
        self.assertIsNotNone(collection)

        # Verify collection exists
        collections = self.project.list_collections()
        self.assertIn(collection_name, collections)

    def test_get_collection(self):
        """Should retrieve existing collection."""
        collection_name = f"test_get_{uuid.uuid4().hex[:8]}"
        self.created_collections.append(collection_name)

        # Create first
        schema = {
            "CollectionName": collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }
        self.project.create_collection(collection_name, schema)

        # Get it back
        collection = self.project.get_collection(collection_name)
        self.assertIsNotNone(collection)

    def test_drop_collection(self):
        """Should drop collection."""
        collection_name = f"test_drop_{uuid.uuid4().hex[:8]}"

        # Create
        schema = {
            "CollectionName": collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }
        self.project.create_collection(collection_name, schema)

        # Verify exists
        self.assertIn(collection_name, self.project.list_collections())

        # Drop
        self.project.drop_collection(collection_name)

        # Verify gone
        self.assertNotIn(collection_name, self.project.list_collections())

    def test_has_collection(self):
        """Should check collection existence."""
        collection_name = f"test_has_{uuid.uuid4().hex[:8]}"
        self.created_collections.append(collection_name)

        # Should not exist initially
        self.assertFalse(self.project.has_collection(collection_name))

        # Create
        schema = {
            "CollectionName": collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }
        self.project.create_collection(collection_name, schema)

        # Should exist now
        self.assertTrue(self.project.has_collection(collection_name))


class TestQdrantProjectFactory(unittest.TestCase):
    """Test factory function."""

    def setUp(self):
        """Set up test data."""
        self.created_collections = []

    def tearDown(self):
        """Clean up."""
        client = QdrantClient(url="http://localhost:6333")
        for name in self.created_collections:
            try:
                client.delete_collection(name)
            except Exception:
                pass

    def test_get_or_create_project(self):
        """Factory should create wrapped project."""
        project = get_or_create_qdrant_project(
            url="http://localhost:6333",
            project_name="test_factory",
            vector_dim=4,
            distance_metric="cosine",
        )

        # Should be wrapped Project
        from openviking.storage.vectordb.project.project import Project
        self.assertIsInstance(project, Project)

        # Should be functional
        collection_name = f"test_factory_{uuid.uuid4().hex[:8]}"
        self.created_collections.append(collection_name)

        schema = {
            "CollectionName": collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            ],
        }
        collection = project.create_collection(collection_name, schema)
        self.assertIsNotNone(collection)

        project.close()


class TestQdrantProjectCollectionOperations(unittest.TestCase):
    """Test collection operations through project."""

    def setUp(self):
        """Set up test project and collection."""
        self.project_name = f"test_ops_{uuid.uuid4().hex[:8]}"
        self.project = QdrantProject(
            url="http://localhost:6333",
            project_name=self.project_name,
            vector_dim=4,
            distance_metric="cosine",
        )

        self.collection_name = f"test_col_{uuid.uuid4().hex[:8]}"
        schema = {
            "CollectionName": self.collection_name,
            "Fields": [
                {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                {"FieldName": "content", "FieldType": "string"},
            ],
        }
        self.collection = self.project.create_collection(self.collection_name, schema)

    def tearDown(self):
        """Clean up."""
        try:
            self.project.drop_collection(self.collection_name)
        except Exception:
            pass
        self.project.close()

    def test_collection_upsert_through_project(self):
        """Collection created through project should be functional."""
        test_data = [
            {"id": "doc_1", "vector": [1.0, 0.0, 0.0, 0.0], "content": "test1"},
            {"id": "doc_2", "vector": [0.0, 1.0, 0.0, 0.0], "content": "test2"},
        ]

        result = self.collection.upsert_data(test_data)
        self.assertEqual(len(result.ids), 2)

    def test_collection_search_through_project(self):
        """Search should work on collection created through project."""
        test_data = [
            {"id": "north", "vector": [1.0, 0.0, 0.0, 0.0], "content": "north"},
            {"id": "south", "vector": [-1.0, 0.0, 0.0, 0.0], "content": "south"},
        ]
        self.collection.upsert_data(test_data)

        results = self.collection.search_by_vector(
            index_name="default",
            dense_vector=[0.9, 0.1, 0.0, 0.0],
            limit=1,
        )

        self.assertEqual(len(results.data), 1)
        self.assertEqual(results.data[0].id, "north")


if __name__ == "__main__":
    unittest.main()
