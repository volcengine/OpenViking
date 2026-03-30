# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Query result caching module for vector search optimization.

This module provides an LRU (Least Recently Used) cache implementation
for storing and retrieving vector search results, reducing redundant
computations for frequently repeated queries.
"""

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CacheEntry:
    """A single cache entry storing search results and metadata.

    Attributes:
        labels: List of result labels (record identifiers)
        scores: List of similarity scores
        created_at: Timestamp when the entry was created
        access_count: Number of times this entry has been accessed
    """

    labels: List[int]
    scores: List[float]
    created_at: float = field(default_factory=time.time)
    access_count: int = 0


class QueryCache:
    """Thread-safe LRU cache for vector search results.

    This cache stores search results keyed by a hash of the query parameters,
    including the query vector, filters, and other search parameters.

    Features:
    - Thread-safe operations using a reentrant lock
    - LRU eviction when capacity is reached
    - TTL-based expiration of stale entries
    - Cache statistics tracking (hits, misses, evictions)

    Attributes:
        max_size: Maximum number of entries in the cache
        ttl_seconds: Time-to-live for cache entries in seconds (0 = no TTL)
        enabled: Whether caching is enabled
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl_seconds: float = 300.0,
        enabled: bool = True,
    ):
        """Initialize the query cache.

        Args:
            max_size: Maximum number of entries to store. Defaults to 1000.
            ttl_seconds: Time-to-live for entries in seconds.
                        Set to 0 to disable TTL-based expiration. Defaults to 300 (5 minutes).
            enabled: Whether caching is enabled. Defaults to True.
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _compute_key(
        self,
        query_vector: Optional[List[float]],
        limit: int,
        filters: Optional[Dict[str, Any]],
        sparse_raw_terms: Optional[List[str]],
        sparse_values: Optional[List[float]],
    ) -> str:
        """Compute a cache key from query parameters.

        Args:
            query_vector: Dense query vector
            limit: Maximum number of results
            filters: Query filters
            sparse_raw_terms: Sparse vector terms
            sparse_values: Sparse vector values

        Returns:
            A unique string key for the query
        """
        # Convert query parameters to a hashable representation
        key_parts = []

        # Handle query vector - convert to tuple for hashing
        if query_vector is not None:
            # Round to 6 decimal places to handle floating point variations
            rounded_vector = tuple(round(v, 6) for v in query_vector)
            key_parts.append(("vector", rounded_vector))

        key_parts.append(("limit", limit))

        # Handle filters - convert to JSON string for consistent hashing
        if filters:
            filter_str = json.dumps(filters, sort_keys=True)
            key_parts.append(("filters", filter_str))

        # Handle sparse vector
        if sparse_raw_terms and sparse_values:
            sparse_tuple = tuple(
                zip(sparse_raw_terms, [round(v, 6) for v in sparse_values], strict=True)
            )
            key_parts.append(("sparse", sparse_tuple))

        # Create hash of the key parts
        key_str = str(key_parts)
        return hashlib.sha256(key_str.encode()).hexdigest()

    def get(
        self,
        query_vector: Optional[List[float]],
        limit: int,
        filters: Optional[Dict[str, Any]],
        sparse_raw_terms: Optional[List[str]],
        sparse_values: Optional[List[float]],
    ) -> Optional[Tuple[List[int], List[float]]]:
        """Retrieve cached search results if available.

        Args:
            query_vector: Dense query vector
            limit: Maximum number of results
            filters: Query filters
            sparse_raw_terms: Sparse vector terms
            sparse_values: Sparse vector values

        Returns:
            Tuple of (labels, scores) if found in cache, None otherwise
        """
        if not self.enabled:
            return None

        key = self._compute_key(query_vector, limit, filters, sparse_raw_terms, sparse_values)

        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            entry = self._cache[key]

            # Check TTL expiration
            if self.ttl_seconds > 0:
                age = time.time() - entry.created_at
                if age > self.ttl_seconds:
                    del self._cache[key]
                    self._misses += 1
                    return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.access_count += 1
            self._hits += 1

            return (entry.labels.copy(), entry.scores.copy())

    def put(
        self,
        query_vector: Optional[List[float]],
        limit: int,
        filters: Optional[Dict[str, Any]],
        sparse_raw_terms: Optional[List[str]],
        sparse_values: Optional[List[float]],
        labels: List[int],
        scores: List[float],
    ) -> None:
        """Store search results in the cache.

        Args:
            query_vector: Dense query vector
            limit: Maximum number of results
            filters: Query filters
            sparse_raw_terms: Sparse vector terms
            sparse_values: Sparse vector values
            labels: Result labels from search
            scores: Result scores from search
        """
        if not self.enabled:
            return

        key = self._compute_key(query_vector, limit, filters, sparse_raw_terms, sparse_values)

        with self._lock:
            # Remove if already exists (will be re-added at end)
            if key in self._cache:
                del self._cache[key]

            # Evict oldest entry if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
                self._evictions += 1

            # Add new entry
            self._cache[key] = CacheEntry(
                labels=labels.copy(),
                scores=scores.copy(),
            )

    def invalidate(self) -> None:
        """Clear all entries from the cache.

        Should be called when the underlying index is modified.
        """
        with self._lock:
            self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary containing cache statistics:
            - size: Current number of entries
            - max_size: Maximum capacity
            - hits: Number of cache hits
            - misses: Number of cache misses
            - evictions: Number of entries evicted
            - hit_rate: Cache hit rate (0-1)
        """
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0

            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_rate": hit_rate,
                "enabled": self.enabled,
                "ttl_seconds": self.ttl_seconds,
            }

    def resize(self, new_max_size: int) -> None:
        """Resize the cache capacity.

        Args:
            new_max_size: New maximum number of entries
        """
        with self._lock:
            self.max_size = new_max_size
            # Evict entries if new size is smaller
            while len(self._cache) > new_max_size:
                self._cache.popitem(last=False)
                self._evictions += 1

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable caching.

        Args:
            enabled: Whether to enable caching
        """
        with self._lock:
            self.enabled = enabled
            if not enabled:
                self._cache.clear()


class CacheManager:
    """Manages multiple query caches for different indexes.

    This class provides a central point for managing caches across
    multiple indexes in a collection.

    Attributes:
        default_max_size: Default maximum cache size for new caches
        default_ttl_seconds: Default TTL for new caches
        default_enabled: Default enabled state for new caches
    """

    def __init__(
        self,
        default_max_size: int = 1000,
        default_ttl_seconds: float = 300.0,
        default_enabled: bool = True,
    ):
        """Initialize the cache manager.

        Args:
            default_max_size: Default max size for new caches
            default_ttl_seconds: Default TTL for new caches
            default_enabled: Default enabled state for new caches
        """
        self.default_max_size = default_max_size
        self.default_ttl_seconds = default_ttl_seconds
        self.default_enabled = default_enabled
        self._caches: Dict[str, QueryCache] = {}
        self._lock = threading.RLock()

    def get_cache(self, index_name: str) -> QueryCache:
        """Get or create a cache for the specified index.

        Args:
            index_name: Name of the index

        Returns:
            QueryCache instance for the index
        """
        with self._lock:
            if index_name not in self._caches:
                self._caches[index_name] = QueryCache(
                    max_size=self.default_max_size,
                    ttl_seconds=self.default_ttl_seconds,
                    enabled=self.default_enabled,
                )
            return self._caches[index_name]

    def invalidate_index(self, index_name: str) -> None:
        """Invalidate cache for a specific index.

        Args:
            index_name: Name of the index to invalidate
        """
        with self._lock:
            if index_name in self._caches:
                self._caches[index_name].invalidate()

    def invalidate_all(self) -> None:
        """Invalidate all caches."""
        with self._lock:
            for cache in self._caches.values():
                cache.invalidate()

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all caches.

        Returns:
            Dictionary mapping index names to their cache statistics
        """
        with self._lock:
            return {name: cache.get_stats() for name, cache in self._caches.items()}

    def set_enabled_all(self, enabled: bool) -> None:
        """Enable or disable all caches.

        Args:
            enabled: Whether to enable caching
        """
        with self._lock:
            for cache in self._caches.values():
                cache.set_enabled(enabled)


# Global cache manager instance (can be configured per collection)
_global_cache_manager: Optional[CacheManager] = None
_global_cache_lock = threading.Lock()


def get_global_cache_manager() -> CacheManager:
    """Get the global cache manager instance.

    Returns:
        The global CacheManager instance, creating it if necessary
    """
    global _global_cache_manager
    with _global_cache_lock:
        if _global_cache_manager is None:
            _global_cache_manager = CacheManager()
        return _global_cache_manager


def set_global_cache_manager(manager: CacheManager) -> None:
    """Set the global cache manager instance.

    Args:
        manager: The CacheManager instance to use globally
    """
    global _global_cache_manager
    with _global_cache_lock:
        _global_cache_manager = manager
