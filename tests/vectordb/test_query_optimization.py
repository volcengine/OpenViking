# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for query caching and batch search optimization.

This module provides tests and benchmarks for the vector retrieval
optimizations including:
- Query result caching (LRU cache)
- Batch search with parallel processing
"""

import random
import time
from typing import Dict

import pytest

from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection


def create_test_collection(
    collection_name: str = "test_collection",
    dim: int = 128,
    num_docs: int = 1000,
    cache_config: Dict = None,
):
    """Create a test collection with random data."""
    meta_data = {
        "CollectionName": collection_name,
        "Fields": [
            {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
            {"FieldName": "embedding", "FieldType": "vector", "Dim": dim},
            {"FieldName": "text", "FieldType": "text"},
            {"FieldName": "category", "FieldType": "text"},
        ],
    }

    collection = get_or_create_local_collection(meta_data=meta_data, cache_config=cache_config)

    # Insert test data
    categories = ["tech", "science", "art", "sports", "music"]
    data_list = []
    for i in range(num_docs):
        data_list.append(
            {
                "id": i,
                "embedding": [random.random() for _ in range(dim)],
                "text": f"Document {i}",
                "category": categories[i % 5],
            }
        )

    collection.upsert_data(data_list)

    # Create index
    index_meta_data = {
        "IndexName": "test_index",
        "VectorIndex": {
            "IndexType": "flat",
            "Distance": "ip",
        },
        "ScalarIndex": ["category"],
    }
    collection.create_index("test_index", index_meta_data)

    return collection


class TestQueryCache:
    """Tests for query result caching."""

    def test_cache_disabled(self):
        """Test that caching can be disabled."""
        collection = create_test_collection(
            collection_name="test_cache_disabled",
            cache_config={"enabled": False},
        )

        # Get cache stats
        stats = collection.get_index_cache_stats("test_index")
        assert stats is not None
        assert stats["enabled"] is False

        # Perform searches
        query = [random.random() for _ in range(128)]
        _result1 = collection.search_by_vector("test_index", query, limit=5)
        _result2 = collection.search_by_vector("test_index", query, limit=5)

        # Cache should have 0 hits since it's disabled
        stats = collection.get_index_cache_stats("test_index")
        assert stats["hits"] == 0

        collection.close()

    def test_cache_enabled(self):
        """Test that caching works when enabled."""
        collection = create_test_collection(
            collection_name="test_cache_enabled",
            cache_config={"enabled": True, "max_size": 100, "ttl_seconds": 60},
        )

        # Get cache stats
        stats = collection.get_index_cache_stats("test_index")
        assert stats is not None
        assert stats["enabled"] is True
        assert stats["max_size"] == 100
        assert stats["ttl_seconds"] == 60

        # Perform same search multiple times
        query = [random.random() for _ in range(128)]
        result1 = collection.search_by_vector("test_index", query, limit=5)

        # Check cache miss
        stats = collection.get_index_cache_stats("test_index")
        assert stats["misses"] == 1
        assert stats["hits"] == 0

        # Same query should hit cache
        result2 = collection.search_by_vector("test_index", query, limit=5)

        stats = collection.get_index_cache_stats("test_index")
        assert stats["hits"] == 1

        # Results should be identical
        assert len(result1.data) == len(result2.data)
        for i in range(len(result1.data)):
            assert result1.data[i].id == result2.data[i].id
            assert abs(result1.data[i].score - result2.data[i].score) < 1e-6

        collection.close()

    def test_cache_invalidation_on_upsert(self):
        """Test that cache is invalidated when data is modified."""
        collection = create_test_collection(
            collection_name="test_cache_invalidation",
            cache_config={"enabled": True},
        )

        # Perform search to populate cache
        query = [random.random() for _ in range(128)]
        _result1 = collection.search_by_vector("test_index", query, limit=5)

        stats = collection.get_index_cache_stats("test_index")
        assert stats["misses"] == 1
        assert stats["hits"] == 0

        # Insert new data - should invalidate cache
        collection.upsert_data(
            [
                {
                    "id": 10000,
                    "embedding": [random.random() for _ in range(128)],
                    "text": "New document",
                    "category": "tech",
                }
            ]
        )

        # Same query should miss cache (it was invalidated)
        _result2 = collection.search_by_vector("test_index", query, limit=5)

        stats = collection.get_index_cache_stats("test_index")
        # After upsert, cache was invalidated, so another miss
        assert stats["misses"] == 2

        collection.close()

    def test_cache_stats(self):
        """Test cache statistics tracking."""
        collection = create_test_collection(
            collection_name="test_cache_stats",
            cache_config={"enabled": True, "max_size": 10},
        )

        # Perform multiple searches
        queries = [[random.random() for _ in range(128)] for _ in range(5)]

        # First round - all misses
        for query in queries:
            collection.search_by_vector("test_index", query, limit=5)

        stats = collection.get_index_cache_stats("test_index")
        assert stats["misses"] == 5
        assert stats["hits"] == 0

        # Second round - all hits (same queries)
        for query in queries:
            collection.search_by_vector("test_index", query, limit=5)

        stats = collection.get_index_cache_stats("test_index")
        assert stats["hits"] == 5

        # Test hit rate calculation
        assert stats["hit_rate"] == 0.5  # 5 hits / 10 total requests

        collection.close()


class TestBatchSearch:
    """Tests for batch search functionality."""

    def test_batch_search_basic(self):
        """Test basic batch search functionality."""
        collection = create_test_collection(
            collection_name="test_batch_search_basic",
            cache_config={"enabled": True},
        )

        # Perform batch search
        num_queries = 10
        queries = [[random.random() for _ in range(128)] for _ in range(num_queries)]

        results = collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            limit=5,
        )

        assert len(results) == num_queries
        for result in results:
            assert len(result.data) <= 5
            for item in result.data:
                assert item.id is not None
                assert item.score is not None

        collection.close()

    def test_batch_search_with_filters(self):
        """Test batch search with filters."""
        collection = create_test_collection(
            collection_name="test_batch_search_filters",
            cache_config={"enabled": True},
        )

        num_queries = 5
        queries = [[random.random() for _ in range(128)] for _ in range(num_queries)]

        results = collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            limit=10,
            filters={"op": "must", "field": "category", "conds": ["tech"]},
        )

        assert len(results) == num_queries
        for result in results:
            for item in result.data:
                assert item.fields.get("category") == "tech"

        collection.close()

    def test_batch_search_with_sparse_vectors(self):
        """Test batch search with sparse vectors."""
        collection = create_test_collection(
            collection_name="test_batch_search_sparse",
            cache_config={"enabled": True},
        )

        num_queries = 3
        queries = [[random.random() for _ in range(128)] for _ in range(num_queries)]
        sparse_vectors = [{"term1": 0.5, "term2": 0.3} for _ in range(num_queries)]

        results = collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            sparse_vectors=sparse_vectors,
            limit=5,
        )

        assert len(results) == num_queries

        collection.close()

    def test_batch_search_with_offset(self):
        """Test batch search with offset."""
        collection = create_test_collection(
            collection_name="test_batch_search_offset",
            cache_config={"enabled": True},
        )

        queries = [[random.random() for _ in range(128)] for _ in range(3)]

        # Search with offset=0
        results_no_offset = collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            limit=5,
            offset=0,
        )

        # Search with offset=2
        results_with_offset = collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            limit=5,
            offset=2,
        )

        # With offset, we should skip the first 2 results
        for i in range(len(queries)):
            # If there were enough results, the first result with offset
            # should be different from the first result without offset
            if len(results_no_offset[i].data) > 2:
                assert results_with_offset[i].data[0].id != results_no_offset[i].data[0].id

        collection.close()

    def test_batch_search_cache_interaction(self):
        """Test that batch search populates and uses cache."""
        collection = create_test_collection(
            collection_name="test_batch_search_cache",
            cache_config={"enabled": True},
        )

        # Perform batch search
        queries = [[random.random() for _ in range(128)] for _ in range(5)]
        results1 = collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            limit=5,
        )

        # Check cache stats - should have 5 misses
        stats = collection.get_index_cache_stats("test_index")
        assert stats["misses"] == 5

        # Same batch search - should hit cache
        results2 = collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            limit=5,
        )

        stats = collection.get_index_cache_stats("test_index")
        assert stats["hits"] == 5

        # Results should be identical
        for i in range(len(queries)):
            assert len(results1[i].data) == len(results2[i].data)
            for j in range(len(results1[i].data)):
                assert results1[i].data[j].id == results2[i].data[j].id

        collection.close()


class TestPerformanceBenchmark:
    """Performance benchmarks for caching and batch search."""

    @pytest.mark.skip(reason="Benchmark test - run manually")
    def test_cache_performance_benchmark(self):
        """Benchmark cache performance improvement."""
        collection = create_test_collection(
            collection_name="benchmark_cache",
            num_docs=5000,
            cache_config={"enabled": True, "max_size": 1000},
        )

        # Create a set of query vectors (some repeated)
        all_queries = [[random.random() for _ in range(128)] for _ in range(100)]
        # Repeat some queries to simulate cache hits
        repeated_queries = all_queries[:20] * 5 + all_queries[20:]

        # Warm up cache
        for query in repeated_queries[:20]:
            collection.search_by_vector("test_index", query, limit=10)

        # Benchmark with cache
        start_time = time.time()
        for query in repeated_queries:
            collection.search_by_vector("test_index", query, limit=10)
        cached_time = time.time() - start_time

        # Get stats
        stats = collection.get_index_cache_stats("test_index")
        print("\nCache Performance:")
        print(f"  Total queries: {len(repeated_queries)}")
        print(f"  Cache hits: {stats['hits']}")
        print(f"  Cache misses: {stats['misses']}")
        print(f"  Hit rate: {stats['hit_rate']:.2%}")
        print(f"  Total time: {cached_time:.3f}s")

        collection.close()

    @pytest.mark.skip(reason="Benchmark test - run manually")
    def test_batch_search_performance_benchmark(self):
        """Benchmark batch search performance improvement."""
        collection = create_test_collection(
            collection_name="benchmark_batch",
            num_docs=5000,
            cache_config={"enabled": False},  # Disable cache to measure batch effect
        )

        num_queries = 50
        queries = [[random.random() for _ in range(128)] for _ in range(num_queries)]

        # Benchmark individual searches
        start_time = time.time()
        for query in queries:
            collection.search_by_vector("test_index", query, limit=10)
        individual_time = time.time() - start_time

        # Clear cache (though it's disabled)
        collection.invalidate_index_cache("test_index")

        # Benchmark batch search
        start_time = time.time()
        collection.batch_search_by_vector(
            index_name="test_index",
            dense_vectors=queries,
            limit=10,
            num_threads=4,
        )
        batch_time = time.time() - start_time

        print("\nBatch Search Performance:")
        print(f"  Number of queries: {num_queries}")
        print(f"  Individual search time: {individual_time:.3f}s")
        print(f"  Batch search time: {batch_time:.3f}s")
        print(f"  Speedup: {individual_time / batch_time:.2f}x")

        collection.close()


if __name__ == "__main__":
    # Run basic tests
    print("Running query cache tests...")
    test_cache = TestQueryCache()
    test_cache.test_cache_disabled()
    print("  ✓ test_cache_disabled")
    test_cache.test_cache_enabled()
    print("  ✓ test_cache_enabled")
    test_cache.test_cache_invalidation_on_upsert()
    print("  ✓ test_cache_invalidation_on_upsert")
    test_cache.test_cache_stats()
    print("  ✓ test_cache_stats")

    print("\nRunning batch search tests...")
    test_batch = TestBatchSearch()
    test_batch.test_batch_search_basic()
    print("  ✓ test_batch_search_basic")
    test_batch.test_batch_search_with_filters()
    print("  ✓ test_batch_search_with_filters")
    test_batch.test_batch_search_with_offset()
    print("  ✓ test_batch_search_with_offset")
    test_batch.test_batch_search_cache_interaction()
    print("  ✓ test_batch_search_cache_interaction")

    print("\nAll tests passed! ✓")
