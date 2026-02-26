# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Storage interface for OpenViking.

Defines the abstract storage interface inspired by vector database designs
(Milvus/Qdrant). All storage backends must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class VikingDBInterface(ABC):
    """
    Abstract vector indexing interface for OpenViking.

    This interface defines all vector indexing capabilities required by OpenViking.
    New vector indexing backends should implement this interface to ensure compatibility.

    Capabilities:
    - Collection management
    - CRUD operations (single and batch)
    - Vector similarity search
    - Scalar filtering
    - Index management
    - Lifecycle management
    """

    # =========================================================================
    # Collection Management
    # =========================================================================

    @abstractmethod
    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        """
        Create a new collection.`

        Args:
            name: Collection name (e.g., "memory", "resource", "skill")
            schema: Schema definition including:
                - vector_dim: int - Vector dimension (default: 2048)
                - distance: str - Distance metric ("cosine", "euclid", "dot")
                - fields: List[dict] - Field definitions with name, type, indexed

        Returns:
            True if created successfully, False if already exists

        Example:
            schema = {
                "vector_dim": 2048,
                "distance": "cosine",
                "fields": [
                    {"name": "uri", "type": "string", "indexed": True},
                    {"name": "abstract", "type": "text"},
                    {"name": "active_count", "type": "integer"},
                ]
            }
        """
        pass

    @abstractmethod
    async def drop_collection(self, name: str) -> bool:
        """
        Drop a collection/table.

        Args:
            name: Collection name

        Returns:
            True if dropped successfully, False otherwise
        """
        pass

    @abstractmethod
    async def collection_exists(self, name: str) -> bool:
        """
        Check if a collection exists.

        Args:
            name: Collection name

        Returns:
            True if exists, False otherwise
        """
        pass

    @abstractmethod
    async def list_collections(self) -> List[str]:
        """
        List all collection names.

        Returns:
            List of collection names
        """
        pass

    @abstractmethod
    async def get_collection_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get collection metadata and statistics.

        Args:
            name: Collection name

        Returns:
            Dictionary with collection info:
                - name: str
                - vector_dim: int
                - count: int
                - status: str
            Returns None if collection doesn't exist
        """
        pass

    # =========================================================================
    # CRUD Operations - Single Record
    # =========================================================================

    @abstractmethod
    async def insert(self, collection: str, data: Dict[str, Any]) -> str:
        """
        Insert a single record.

        Args:
            collection: Collection name
            data: Record data. Must include:
                - id: str (optional, auto-generated if not provided)
                - vector: List[float] (optional)
                - Other payload fields

        Returns:
            ID of the inserted record
        """
        pass

    @abstractmethod
    async def update(self, collection: str, id: str, data: Dict[str, Any]) -> bool:
        """
        Update a record by ID.

        Args:
            collection: Collection name
            id: Record ID
            data: Fields to update (can include vector)

        Returns:
            True if updated successfully, False if not found
        """
        pass

    @abstractmethod
    async def upsert(self, collection: str, data: Dict[str, Any]) -> str:
        """
        Insert or update a record.

        If record with same ID exists, update it. Otherwise insert new record.

        Args:
            collection: Collection name
            data: Record data with id field

        Returns:
            ID of the upserted record
        """
        pass

    @abstractmethod
    async def delete(self, collection: str, ids: List[str]) -> int:
        """
        Delete records by IDs.

        Args:
            collection: Collection name
            ids: List of record IDs to delete

        Returns:
            Number of records deleted
        """
        pass

    @abstractmethod
    async def get(self, collection: str, ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get records by IDs.

        Args:
            collection: Collection name
            ids: List of record IDs

        Returns:
            List of records (may be fewer than requested if some IDs not found)
        """
        pass

    @abstractmethod
    async def exists(self, collection: str, id: str) -> bool:
        """
        Check if a record exists.

        Args:
            collection: Collection name
            id: Record ID

        Returns:
            True if exists, False otherwise
        """
        pass

    # =========================================================================
    # CRUD Operations - Batch
    # =========================================================================

    @abstractmethod
    async def batch_insert(self, collection: str, data: List[Dict[str, Any]]) -> List[str]:
        """
        Batch insert multiple records.

        Args:
            collection: Collection name
            data: List of records

        Returns:
            List of IDs of inserted records
        """
        pass

    @abstractmethod
    async def batch_upsert(self, collection: str, data: List[Dict[str, Any]]) -> List[str]:
        """
        Batch insert or update multiple records.

        Args:
            collection: Collection name
            data: List of records with id fields

        Returns:
            List of IDs of upserted records
        """
        pass

    @abstractmethod
    async def batch_delete(self, collection: str, filter: Dict[str, Any]) -> int:
        """
        Delete records matching filter conditions.

        Args:
            collection: Collection name
            filter: Filter conditions

        Returns:
            Number of records deleted
        """
        pass

    @abstractmethod
    async def remove_by_uri(
        self,
        collection: str,
        uri: str,
    ) -> int:
        """
        Remove resource(s) by URI.

        If the URI points to a directory, removes all descendants first,
        then removes the directory itself.

        Args:
            collection: Collection name
            uri: URI to remove (e.g., "viking://resources/references/doc_name")

        Returns:
            Number of records removed

        Example:
            # Remove a single context
            await storage.remove_by_uri("resource", "viking://resources/ref/doc/section1")

            # Remove entire document tree (directory + all children)
            await storage.remove_by_uri("resource", "viking://resources/ref/doc_name")
        """
        pass

    # =========================================================================
    # Search Operations
    # =========================================================================

    @abstractmethod
    async def search(
        self,
        collection: str,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        with_vector: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search: vector similarity + scalar filtering + sparse vector matching.

        Args:
            collection: Collection name
            query_vector: Dense query vector for similarity search (optional)
            sparse_query_vector: Sparse query vector for term matching (optional, Dict[str, float])
            filter: Scalar filter conditions (optional)
            limit: Maximum number of results
            offset: Offset for pagination
            output_fields: Fields to return (None for all)
            with_vector: Include vector in results

        Returns:
            List of matching records. If query_vector provided, includes _score field.

        Notes:
            - If both query_vector and sparse_query_vector are provided, performs hybrid search
            - If only query_vector is provided, performs dense vector search
            - If only sparse_query_vector is provided, performs sparse search
            - If neither is provided, performs filter-only search

        Filter format (VikingVectorIndex DSL):
            {
                "op": "and" | "or",
                "conds": [
                    {"op": "must", "field": "name", "conds": [value]},
                    {"op": "range", "field": "age", "gte": 18, "lt": 65},
                    {"op": "must", "field": "uri", "conds": ["viking://"]},
                    {"op": "contains", "field": "desc", "substring": "hello"}
                ]
            }

        Example:
            # Dense search
            results = await storage.search(
                collection="context",
                query_vector=embedding,
                filter={
                    "op": "and",
                    "conds": [
                        {"op": "must", "field": "uri", "conds": ["viking://user"]},
                        {"op": "range", "field": "active_count", "gte": 1}
                    ]
                },
                limit=10
            )
        """
        pass

    @abstractmethod
    async def filter(
        self,
        collection: str,
        filter: Dict[str, Any],
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Pure scalar filtering without vector search.

        Args:
            collection: Collection name
            filter: Filter conditions
            limit: Maximum number of results
            offset: Offset for pagination
            output_fields: Fields to return
            order_by: Field to sort by (optional)
            order_desc: Sort descending if True

        Returns:
            List of matching records
        """
        pass

    @abstractmethod
    async def scroll(
        self,
        collection: str,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Scroll through large result sets efficiently.

        Args:
            collection: Collection name
            filter: Optional filter conditions
            limit: Batch size
            cursor: Cursor from previous scroll (None for first batch)
            output_fields: Fields to return

        Returns:
            Tuple of (records, next_cursor). next_cursor is None when exhausted.

        Example:
            cursor = None
            while True:
                records, cursor = await storage.scroll(
                    "memory", limit=100, cursor=cursor
                )
                process(records)
                if cursor is None:
                    break
        """
        pass

    # =========================================================================
    # Aggregation Operations
    # =========================================================================

    @abstractmethod
    async def count(self, collection: str, filter: Optional[Dict[str, Any]] = None) -> int:
        """
        Count records matching filter.

        Args:
            collection: Collection name
            filter: Optional filter conditions

        Returns:
            Number of matching records
        """
        pass

    # =========================================================================
    # Index Operations
    # =========================================================================

    @abstractmethod
    async def create_index(
        self,
        collection: str,
        field: str,
        index_type: str,
        **kwargs,
    ) -> bool:
        """
        Create an index on a field.

        Args:
            collection: Collection name
            field: Field name to index
            index_type: Index type:
                - "keyword": Exact match index
                - "text": Full-text search index
                - "integer": Numeric range index
                - "float": Numeric range index
                - "bool": Boolean index
            **kwargs: Additional index parameters

        Returns:
            True if created successfully
        """
        pass

    @abstractmethod
    async def drop_index(self, collection: str, field: str) -> bool:
        """
        Drop an index on a field.

        Args:
            collection: Collection name
            field: Field name

        Returns:
            True if dropped successfully
        """
        pass

    # =========================================================================
    # Lifecycle Operations
    # =========================================================================

    @abstractmethod
    async def clear(self, collection: str) -> bool:
        """
        Clear all data in a collection (keep schema).

        Args:
            collection: Collection name

        Returns:
            True if cleared successfully
        """
        pass

    @abstractmethod
    async def optimize(self, collection: str) -> bool:
        """
        Optimize collection for better performance.

        Triggers index optimization, compaction, etc.

        Args:
            collection: Collection name

        Returns:
            True if optimization completed
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close storage connection and release resources.

        Should be called when storage is no longer needed.
        """
        pass

    # =========================================================================
    # Health & Status
    # =========================================================================

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if storage backend is healthy and accessible.

        Returns:
            True if healthy, False otherwise
        """
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get storage statistics.

        Returns:
            Dictionary with stats:
                - collections: int - Number of collections
                - total_records: int - Total record count
                - storage_size: int - Storage size in bytes (if available)
                - backend: str - Backend type identifier
        """
        pass


# =============================================================================
# Exceptions
# =============================================================================


class VikingDBException(Exception):
    """Base exception for VikingDB operations."""

    pass


class StorageException(VikingDBException):
    """Legacy alias for VikingDBException for backward compatibility."""

    pass


class CollectionNotFoundError(StorageException):
    """Raised when a collection does not exist."""

    pass


class RecordNotFoundError(StorageException):
    """Raised when a record does not exist."""

    pass


class DuplicateKeyError(StorageException):
    """Raised when trying to insert a duplicate key."""

    pass


class ConnectionError(StorageException):
    """Raised when storage connection fails."""

    pass


class SchemaError(StorageException):
    """Raised when schema validation fails."""

    pass
