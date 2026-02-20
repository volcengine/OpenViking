# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for Qdrant collection implementation.

Prerequisites:
    - Qdrant server running on localhost:6333
    - qdrant-client installed: pip install qdrant-client

Run tests:
    pytest tests/vectordb/test_qdrant_collection.py -v
    pytest tests/vectordb/test_qdrant_collection.py -v -k "basic"
"""

import random
import unittest
import uuid

import pytest

# Skip all tests if qdrant-client is not installed
pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

from openviking.storage.vectordb.collection.qdrant_collection import (
    QdrantCollection,
    get_or_create_qdrant_collection,
    string_to_qdrant_id,
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


class TestStringToQdrantId(unittest.TestCase):
    """Test ID conversion helper function."""

    def test_valid_uuid_unchanged(self):
        """Valid UUIDs should be returned as-is."""
        test_uuid = str(uuid.uuid4())
        result = string_to_qdrant_id(test_uuid)
        self.assertEqual(result, test_uuid)

    def test_string_to_uuid_deterministic(self):
        """Same string should always produce same UUID."""
        result1 = string_to_qdrant_id("doc_123")
        result2 = string_to_qdrant_id("doc_123")
        self.assertEqual(result1, result2)

    def test_different_strings_different_uuids(self):
        """Different strings should produce different UUIDs."""
        result1 = string_to_qdrant_id("doc_1")
        result2 = string_to_qdrant_id("doc_2")
        self.assertNotEqual(result1, result2)

    def test_result_is_valid_uuid(self):
        """Converted result should be a valid UUID string."""
        result = string_to_qdrant_id("any_string_id")
        # Should not raise
        uuid.UUID(result)


class TestQdrantCollectionBasic(unittest.TestCase):
    """Basic CRUD operations for Qdrant collection."""

    @classmethod
    def setUpClass(cls):
        """Set up test client."""
        cls.client = QdrantClient(url="http://localhost:6333")

    def setUp(self):
        """Create a fresh collection for each test."""
        self.collection_name = f"test_basic_{uuid.uuid4().hex[:8]}"
        self.vector_dim = 4
        self.collection = QdrantCollection(
            client=self.client,
            collection_name=self.collection_name,
            vector_dim=self.vector_dim,
            distance_metric="cosine",
        )

    def tearDown(self):
        """Clean up collection after test."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass

    def test_collection_created(self):
        """Collection should be created on initialization."""
        collections = self.client.get_collections().collections
        names = [c.name for c in collections]
        self.assertIn(self.collection_name, names)

    def test_upsert_and_fetch(self):
        """Should insert and retrieve data correctly."""
        test_data = [
            {"id": "doc_1", "vector": [1.0, 0.0, 0.0, 0.0], "content": "test1"},
            {"id": "doc_2", "vector": [0.0, 1.0, 0.0, 0.0], "content": "test2"},
        ]

        # Insert
        result = self.collection.upsert_data(test_data)
        self.assertEqual(len(result.ids), 2)
        self.assertIn("doc_1", result.ids)
        self.assertIn("doc_2", result.ids)

        # Fetch
        fetch_result = self.collection.fetch_data(["doc_1", "doc_2"])
        self.assertEqual(len(fetch_result.items), 2)
        self.assertEqual(len(fetch_result.ids_not_exist), 0)

        # Verify content
        items_by_id = {item.id: item for item in fetch_result.items}
        self.assertEqual(items_by_id["doc_1"].fields["content"], "test1")
        self.assertEqual(items_by_id["doc_2"].fields["content"], "test2")

    def test_fetch_missing_ids(self):
        """Should report missing IDs correctly."""
        test_data = [{"id": "existing", "vector": [1.0, 0.0, 0.0, 0.0]}]
        self.collection.upsert_data(test_data)

        fetch_result = self.collection.fetch_data(["existing", "missing"])
        self.assertEqual(len(fetch_result.items), 1)
        self.assertIn("missing", fetch_result.ids_not_exist)

    def test_delete_data(self):
        """Should delete data correctly."""
        test_data = [
            {"id": "to_delete", "vector": [1.0, 0.0, 0.0, 0.0]},
            {"id": "to_keep", "vector": [0.0, 1.0, 0.0, 0.0]},
        ]
        self.collection.upsert_data(test_data)

        # Delete one
        self.collection.delete_data(["to_delete"])

        # Verify
        fetch_result = self.collection.fetch_data(["to_delete", "to_keep"])
        self.assertEqual(len(fetch_result.items), 1)
        self.assertEqual(fetch_result.items[0].id, "to_keep")
        self.assertIn("to_delete", fetch_result.ids_not_exist)

    def test_fetch_by_uri_integration(self):
        """Backend fetch_by_uri should return the record, not raise."""
        import asyncio
        from openviking_cli.utils.config.vectordb_config import QdrantConfig, VectorDBBackendConfig
        from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend

        config = VectorDBBackendConfig(
            backend="qdrant",
            qdrant=QdrantConfig(url="http://localhost:6333"),
            dimension=4,
        )
        backend = VikingVectorIndexBackend(config=config)
        coll_name = f"test_uri_{uuid.uuid4().hex[:8]}"

        async def run():
            await backend.create_collection(coll_name, {
                "CollectionName": coll_name,
                "Fields": [
                    {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                    {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
                    {"FieldName": "uri", "FieldType": "string"},
                ],
            })
            await backend.insert(coll_name, {
                "id": "doc1", "vector": [1, 0, 0, 0], "uri": "/test/path"
            })
            import time
            time.sleep(0.5)
            result = await backend.fetch_by_uri(coll_name, "/test/path")
            await backend.drop_collection(coll_name)
            return result

        result = asyncio.run(run())
        self.assertIsNotNone(result)
        self.assertEqual(result["uri"], "/test/path")

    def test_delete_all_data_preserves_collection(self):
        """delete_all_data should clear data but keep collection intact."""
        import time
        self.collection.upsert_data([
            {"id": "a", "vector": [1, 0, 0, 0]},
            {"id": "b", "vector": [0, 1, 0, 0]},
        ])
        time.sleep(0.5)
        # Verify data exists
        count = self.client.count(self.collection_name).count
        self.assertGreaterEqual(count, 2)

        # Delete all
        self.collection.delete_all_data()
        time.sleep(0.5)

        # Collection should still exist
        collections = [c.name for c in self.client.get_collections().collections]
        self.assertIn(self.collection_name, collections)

        # Data should be gone
        count = self.client.count(self.collection_name).count
        self.assertEqual(count, 0)

        # Should be able to insert again
        self.collection.upsert_data([
            {"id": "c", "vector": [0, 0, 1, 0]},
        ])
        time.sleep(0.5)
        count = self.client.count(self.collection_name).count
        self.assertEqual(count, 1)

    def test_upsert_update_existing(self):
        """Upsert should update existing documents."""
        # Insert
        self.collection.upsert_data([
            {"id": "doc_1", "vector": [1.0, 0.0, 0.0, 0.0], "content": "original"}
        ])

        # Update
        self.collection.upsert_data([
            {"id": "doc_1", "vector": [1.0, 0.0, 0.0, 0.0], "content": "updated"}
        ])

        # Verify
        fetch_result = self.collection.fetch_data(["doc_1"])
        self.assertEqual(fetch_result.items[0].fields["content"], "updated")


class TestQdrantCollectionSearch(unittest.TestCase):
    """Vector search operations for Qdrant collection."""

    @classmethod
    def setUpClass(cls):
        """Set up test client and collection with data."""
        cls.client = QdrantClient(url="http://localhost:6333")
        cls.collection_name = f"test_search_{uuid.uuid4().hex[:8]}"
        cls.vector_dim = 4

        cls.collection = QdrantCollection(
            client=cls.client,
            collection_name=cls.collection_name,
            vector_dim=cls.vector_dim,
            distance_metric="cosine",
        )

        # Insert test data with distinct vectors
        cls.test_data = [
            {"id": "north", "vector": [1.0, 0.0, 0.0, 0.0], "direction": "north"},
            {"id": "east", "vector": [0.0, 1.0, 0.0, 0.0], "direction": "east"},
            {"id": "south", "vector": [-1.0, 0.0, 0.0, 0.0], "direction": "south"},
            {"id": "west", "vector": [0.0, -1.0, 0.0, 0.0], "direction": "west"},
            {"id": "up", "vector": [0.0, 0.0, 1.0, 0.0], "direction": "up"},
        ]
        cls.collection.upsert_data(cls.test_data)

    @classmethod
    def tearDownClass(cls):
        """Clean up collection."""
        try:
            cls.client.delete_collection(cls.collection_name)
        except Exception:
            pass

    def test_dense_vector_search(self):
        """Should find similar vectors."""
        # Search for vector similar to "north"
        query_vector = [0.9, 0.1, 0.0, 0.0]
        results = self.collection.search_by_vector(
            index_name="default",
            dense_vector=query_vector,
            limit=3,
        )

        self.assertGreater(len(results.data), 0)
        # "north" should be the top result
        self.assertEqual(results.data[0].id, "north")

    def test_search_with_limit(self):
        """Should respect limit parameter."""
        query_vector = [1.0, 0.0, 0.0, 0.0]
        results = self.collection.search_by_vector(
            index_name="default",
            dense_vector=query_vector,
            limit=2,
        )
        self.assertEqual(len(results.data), 2)

    def test_search_returns_scores(self):
        """Search results should include similarity scores."""
        query_vector = [1.0, 0.0, 0.0, 0.0]
        results = self.collection.search_by_vector(
            index_name="default",
            dense_vector=query_vector,
            limit=1,
        )
        self.assertIsNotNone(results.data[0].score)
        # Exact match should have high score (close to 1.0 for cosine)
        self.assertGreater(results.data[0].score, 0.9)

    def test_search_by_random(self):
        """Should return documents via scroll."""
        results = self.collection.search_by_random(
            index_name="default",
            limit=3,
        )
        self.assertEqual(len(results.data), 3)

    def test_search_by_id_excludes_self(self):
        """search_by_id should return similar items excluding the query document."""
        results = self.collection.search_by_id(
            index_name="default",
            id="north",
            limit=3,
        )
        # Should not contain the query document itself
        result_ids = [item.id for item in results.data]
        self.assertNotIn("north", result_ids)
        # Should still return up to limit results
        self.assertGreater(len(results.data), 0)
        self.assertLessEqual(len(results.data), 3)

    def test_search_by_id_nonexistent(self):
        """search_by_id with nonexistent ID should return empty."""
        results = self.collection.search_by_id(
            index_name="default",
            id="nonexistent_id_xyz",
            limit=3,
        )
        self.assertEqual(len(results.data), 0)

    def test_multimodal_without_vectorizer_raises(self):
        """Should raise ValueError when no vectorizer is configured."""
        with self.assertRaises(ValueError) as ctx:
            self.collection.search_by_multimodal(
                index_name="default", text="test query",
            )
        self.assertIn("vectorizer", str(ctx.exception).lower())


class TestQdrantCollectionHybridSearch(unittest.TestCase):
    """Hybrid search (dense + sparse) operations."""

    @classmethod
    def setUpClass(cls):
        """Set up test client and collection with hybrid data."""
        cls.client = QdrantClient(url="http://localhost:6333")
        cls.collection_name = f"test_hybrid_{uuid.uuid4().hex[:8]}"
        cls.vector_dim = 4

        cls.collection = QdrantCollection(
            client=cls.client,
            collection_name=cls.collection_name,
            vector_dim=cls.vector_dim,
            distance_metric="cosine",
        )

        # Insert test data with both dense and sparse vectors
        cls.test_data = [
            {
                "id": "hybrid_1",
                "vector": [1.0, 0.0, 0.0, 0.0],
                "sparse_vector": {"0": 1.0, "1": 0.5},
                "content": "first document",
            },
            {
                "id": "hybrid_2",
                "vector": [0.0, 1.0, 0.0, 0.0],
                "sparse_vector": {"2": 1.0, "3": 0.5},
                "content": "second document",
            },
            {
                "id": "hybrid_3",
                "vector": [0.5, 0.5, 0.0, 0.0],
                "sparse_vector": {"0": 0.5, "2": 0.5},
                "content": "third document",
            },
        ]
        cls.collection.upsert_data(cls.test_data)

    @classmethod
    def tearDownClass(cls):
        """Clean up collection."""
        try:
            cls.client.delete_collection(cls.collection_name)
        except Exception:
            pass

    def test_hybrid_search_with_rrf(self):
        """Hybrid search should combine dense and sparse results."""
        results = self.collection.search_by_vector(
            index_name="default",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            sparse_vector={"0": 1.0, "1": 0.5},
            limit=3,
        )

        self.assertEqual(len(results.data), 3)
        # hybrid_1 should be top result (matches both dense and sparse)
        self.assertEqual(results.data[0].id, "hybrid_1")

    def test_sparse_only_search(self):
        """Should support sparse-only search."""
        results = self.collection.search_by_vector(
            index_name="default",
            sparse_vector={"2": 1.0, "3": 0.5},
            limit=2,
        )

        self.assertGreater(len(results.data), 0)
        # hybrid_2 should be top result (best sparse match)
        self.assertEqual(results.data[0].id, "hybrid_2")


class TestQdrantCollectionFilters(unittest.TestCase):
    """Filter operations for Qdrant collection."""

    @classmethod
    def setUpClass(cls):
        """Set up test client and collection with filterable data."""
        cls.client = QdrantClient(url="http://localhost:6333")
        cls.collection_name = f"test_filters_{uuid.uuid4().hex[:8]}"
        cls.vector_dim = 4

        cls.collection = QdrantCollection(
            client=cls.client,
            collection_name=cls.collection_name,
            vector_dim=cls.vector_dim,
            distance_metric="cosine",
        )

        # Insert test data with various field types
        cls.test_data = [
            {"id": "doc_1", "vector": [1.0, 0, 0, 0], "category": "A", "score": 10},
            {"id": "doc_2", "vector": [1.0, 0, 0, 0], "category": "B", "score": 20},
            {"id": "doc_3", "vector": [1.0, 0, 0, 0], "category": "A", "score": 30},
            {"id": "doc_4", "vector": [1.0, 0, 0, 0], "category": "C", "score": 40},
        ]
        cls.collection.upsert_data(cls.test_data)

    @classmethod
    def tearDownClass(cls):
        """Clean up collection."""
        try:
            cls.client.delete_collection(cls.collection_name)
        except Exception:
            pass

    def test_filter_by_single_value(self):
        """Should filter by exact match."""
        results = self.collection.search_by_random(
            index_name="default",
            limit=10,
            filters={"op": "must", "field": "category", "conds": ["A"]},
        )

        self.assertEqual(len(results.data), 2)
        for item in results.data:
            self.assertEqual(item.fields["category"], "A")

    def test_filter_by_multiple_values(self):
        """Should filter by multiple values (OR)."""
        results = self.collection.search_by_random(
            index_name="default",
            limit=10,
            filters={"op": "must", "field": "category", "conds": ["A", "B"]},
        )

        self.assertEqual(len(results.data), 3)
        for item in results.data:
            self.assertIn(item.fields["category"], ["A", "B"])


class TestQdrantCollectionAggregate(unittest.TestCase):
    """Aggregation operations for Qdrant collection."""

    @classmethod
    def setUpClass(cls):
        """Set up test client and collection."""
        cls.client = QdrantClient(url="http://localhost:6333")
        cls.collection_name = f"test_agg_{uuid.uuid4().hex[:8]}"
        cls.vector_dim = 4

        cls.collection = QdrantCollection(
            client=cls.client,
            collection_name=cls.collection_name,
            vector_dim=cls.vector_dim,
            distance_metric="cosine",
        )

        # Insert test data
        cls.test_data = [
            {"id": f"doc_{i}", "vector": [1.0, 0, 0, 0], "category": f"cat_{i % 3}"}
            for i in range(10)
        ]
        cls.collection.upsert_data(cls.test_data)

    @classmethod
    def tearDownClass(cls):
        """Clean up collection."""
        try:
            cls.client.delete_collection(cls.collection_name)
        except Exception:
            pass

    def test_count_total(self):
        """Should count total documents."""
        result = self.collection.aggregate_data(
            index_name="default",
            op="count",
        )
        self.assertEqual(result.agg.get("_total"), 10)

    def test_count_by_field(self):
        """Should count by field value using paginated scroll."""
        result = self.collection.aggregate_data(
            index_name="default",
            op="count",
            field="category",
        )
        # Should have 3 categories: cat_0, cat_1, cat_2
        self.assertEqual(len(result.agg), 3)
        # Total across all groups should equal 10
        self.assertEqual(sum(result.agg.values()), 10)


class TestQdrantCollectionFactory(unittest.TestCase):
    """Test factory function."""

    @classmethod
    def setUpClass(cls):
        """Set up test client."""
        cls.client = QdrantClient(url="http://localhost:6333")

    def setUp(self):
        """Generate unique collection name."""
        self.collection_name = f"test_factory_{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        """Clean up collection."""
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass

    def test_get_or_create_collection(self):
        """Factory should create wrapped collection."""
        collection = get_or_create_qdrant_collection(
            client=self.client,
            collection_name=self.collection_name,
            vector_dim=4,
            distance_metric="cosine",
        )

        # Should be wrapped Collection, not raw QdrantCollection
        from openviking.storage.vectordb.collection.collection import Collection
        self.assertIsInstance(collection, Collection)

        # Should be functional
        collection.upsert_data([{"id": "test", "vector": [1, 0, 0, 0]}])
        result = collection.fetch_data(["test"])
        self.assertEqual(len(result.items), 1)


class TestQdrantCollectionScalarSearch(unittest.TestCase):
    """Scalar sort operations using Qdrant native order_by."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.client = QdrantClient(url="http://localhost:6333")
            cls.client.get_collections()
        except Exception:
            raise unittest.SkipTest("Qdrant server not available")
        cls.collection_name = f"test_scalar_{uuid.uuid4().hex[:8]}"
        cls.vector_dim = 4
        cls.collection = QdrantCollection(
            client=cls.client,
            collection_name=cls.collection_name,
            vector_dim=cls.vector_dim,
            distance_metric="cosine",
        )
        # Create payload index for sorting (required by Qdrant order_by)
        from qdrant_client.models import PayloadSchemaType
        cls.client.create_payload_index(
            collection_name=cls.collection_name,
            field_name="timestamp",
            field_schema=PayloadSchemaType.INTEGER,
        )
        import time
        time.sleep(0.3)
        cls.test_data = [
            {"id": f"doc_{i}", "vector": [1.0, 0, 0, 0], "timestamp": i * 10, "category": f"cat_{i % 2}"}
            for i in range(10)
        ]
        cls.collection.upsert_data(cls.test_data)
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.delete_collection(cls.collection_name)
        except Exception:
            pass

    def test_scalar_sort_desc(self):
        """Should return results sorted by field descending."""
        results = self.collection.search_by_scalar(
            index_name="default", field="timestamp", order="desc", limit=3,
        )
        self.assertEqual(len(results.data), 3)
        timestamps = [item.fields.get("timestamp") for item in results.data]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_scalar_sort_asc(self):
        """Should return results sorted by field ascending."""
        results = self.collection.search_by_scalar(
            index_name="default", field="timestamp", order="asc", limit=3,
        )
        self.assertEqual(len(results.data), 3)
        timestamps = [item.fields.get("timestamp") for item in results.data]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_scalar_sort_with_filter(self):
        """Should apply filter and then sort."""
        results = self.collection.search_by_scalar(
            index_name="default", field="timestamp", order="desc", limit=10,
            filters={"op": "must", "field": "category", "conds": ["cat_0"]},
        )
        for item in results.data:
            self.assertEqual(item.fields["category"], "cat_0")


class TestQdrantCollectionKeywordSearch(unittest.TestCase):
    """Keyword search using Qdrant full-text index."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.client = QdrantClient(url="http://localhost:6333")
            cls.client.get_collections()
        except Exception:
            raise unittest.SkipTest("Qdrant server not available")
        cls.collection_name = f"test_kw_{uuid.uuid4().hex[:8]}"
        cls.vector_dim = 4
        cls.collection = QdrantCollection(
            client=cls.client,
            collection_name=cls.collection_name,
            vector_dim=cls.vector_dim,
            distance_metric="cosine",
        )
        # Create full-text index on "abstract" field (matches OpenViking schema)
        from qdrant_client.models import TextIndexParams, TextIndexType, TokenizerType
        cls.client.create_payload_index(
            collection_name=cls.collection_name,
            field_name="abstract",
            field_schema=TextIndexParams(
                type=TextIndexType.TEXT,
                tokenizer=TokenizerType.WORD,
                min_token_len=2,
                lowercase=True,
            ),
        )
        import time
        time.sleep(0.3)
        cls.test_data = [
            {"id": "doc1", "vector": [1, 0, 0, 0], "abstract": "Python programming language tutorial"},
            {"id": "doc2", "vector": [0, 1, 0, 0], "abstract": "Java programming framework guide"},
            {"id": "doc3", "vector": [0, 0, 1, 0], "abstract": "Machine learning with Python models"},
            {"id": "doc4", "vector": [0, 0, 0, 1], "abstract": "Database systems architecture design"},
        ]
        cls.collection.upsert_data(cls.test_data)
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.delete_collection(cls.collection_name)
        except Exception:
            pass

    def test_keyword_search_single(self):
        """Should find documents matching keyword."""
        results = self.collection.search_by_keywords(
            index_name="default", keywords=["Python"], limit=10,
        )
        self.assertGreater(len(results.data), 0)
        for item in results.data:
            self.assertIn("python", item.fields.get("abstract", "").lower())

    def test_keyword_search_query_string(self):
        """Should find documents matching query string."""
        results = self.collection.search_by_keywords(
            index_name="default", query="programming", limit=10,
        )
        self.assertGreater(len(results.data), 0)
        for item in results.data:
            self.assertIn("programming", item.fields.get("abstract", "").lower())

    def test_keyword_search_no_match(self):
        """Should return empty for non-matching keywords."""
        results = self.collection.search_by_keywords(
            index_name="default", keywords=["nonexistent_xyz_abc"], limit=10,
        )
        self.assertEqual(len(results.data), 0)


class TestQdrantCollectionIndex(unittest.TestCase):
    """Index management operations."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.client = QdrantClient(url="http://localhost:6333")
            cls.client.get_collections()
        except Exception:
            raise unittest.SkipTest("Qdrant server not available")
        cls.collection_name = f"test_idx_{uuid.uuid4().hex[:8]}"
        cls.collection = QdrantCollection(
            client=cls.client,
            collection_name=cls.collection_name,
            vector_dim=4,
            distance_metric="cosine",
        )

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.delete_collection(cls.collection_name)
        except Exception:
            pass

    def test_has_index_default(self):
        """Default index should exist after collection creation."""
        self.assertTrue(self.collection.has_index("default"))

    def test_has_index_nonexistent(self):
        """Non-existent index should return False."""
        self.assertFalse(self.collection.has_index("nonexistent_index_xyz"))

    def test_create_and_has_index(self):
        """Created index should be tracked."""
        self.collection.create_index("test_index", {"ScalarIndex": []})
        self.assertTrue(self.collection.has_index("test_index"))

    def test_list_indexes_includes_default(self):
        """list_indexes should include 'default'."""
        indexes = self.collection.list_indexes()
        self.assertIn("default", indexes)

    def test_drop_index(self):
        """Dropped index should no longer be tracked."""
        self.collection.create_index("temp_index", {"ScalarIndex": []})
        self.assertTrue(self.collection.has_index("temp_index"))
        self.collection.drop_index("temp_index")
        self.assertFalse(self.collection.has_index("temp_index"))


if __name__ == "__main__":
    unittest.main()
