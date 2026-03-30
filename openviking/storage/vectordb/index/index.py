# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Union

from openviking.storage.vectordb.store.data import DeltaRecord


class IIndex(ABC):
    """Interface for index implementations.

    This abstract base class defines the contract that all index implementations must follow.
    An index provides vector similarity search capabilities along with optional scalar field
    filtering and aggregation operations.

    Index implementations can be:
    - Volatile (in-memory): Fast but non-persistent, lost on process termination
    - Persistent (disk-based): Durable storage with versioning support
    - Remote (service-based): Distributed index accessed via network

    Key Responsibilities:
    - Vector similarity search (dense and sparse vectors)
    - Data ingestion and deletion with incremental updates
    - Scalar field indexing and filtering
    - Aggregation operations (count, group by, etc.)
    - Metadata management and versioning
    """

    def __init__(
        self,
        index_path_or_json: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the index.

        Args:
            index_path_or_json: Either a file system path to a persisted index
                or a JSON configuration string for creating a new index. None for default initialization.
            meta: Index metadata including vector dimensions,
                distance metrics, scalar field definitions, etc.
        """
        pass

    @abstractmethod
    def upsert_data(self, delta_list: List[DeltaRecord]):
        """Insert or update data records in the index.

        Processes a batch of data changes (upserts) and applies them to the index.
        For existing records (matched by label/primary key), updates the vector and fields.
        For new records, inserts them into the index.

        Args:
            delta_list: List of delta records containing:
                - label: Unique identifier for the record
                - vector: Dense embedding vector
                - sparse_raw_terms: Optional sparse vector terms
                - sparse_values: Optional sparse vector weights
                - fields: JSON-encoded scalar field data
                - old_fields: Previous field values (for update tracking)

        Raises:
            NotImplementedError: If not implemented by subclass.

        Note:
            This operation should be atomic per record to maintain index consistency.
        """
        raise NotImplementedError

    @abstractmethod
    def delete_data(self, delta_list: List[DeltaRecord]):
        """Delete data records from the index.

        Removes records from the index based on their labels. Deleted records are
        no longer searchable and their storage is eventually reclaimed.

        Args:
            delta_list: List of delta records containing:
                - label: Unique identifier of the record to delete
                - old_fields: Previous field values (for consistency checking)

        Raises:
            NotImplementedError: If not implemented by subclass.

        Note:
            Depending on implementation, deleted data may be marked for deletion
            and physically removed during index rebuild or compaction.
        """
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query_vector: Optional[List[float]],
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ) -> Tuple[List[int], List[float]]:
        """Perform vector similarity search with optional filtering.

        Searches the index for vectors most similar to the query vector using the
        configured distance metric (e.g., cosine, L2, inner product). Supports both
        dense and sparse vector search, as well as scalar field filtering.

        Args:
            query_vector: Dense query vector for similarity matching.
                Should have the same dimensionality as indexed vectors.
            limit: Maximum number of results to return. Defaults to 10.
            filters: Query DSL for filtering results by scalar fields.
                Supports operators like eq, ne, gt, lt, in, range, etc.
            sparse_raw_terms: Term tokens for sparse vector search.
                Must correspond 1-to-1 with sparse_values.
            sparse_values: Weights for each term in sparse_raw_terms.
                Used for hybrid dense-sparse search (e.g., BM25 + vector).

        Returns:
            A tuple containing:
                - List of labels (record identifiers) sorted by similarity
                - List of similarity scores corresponding to each label

        Raises:
            NotImplementedError: If not implemented by subclass.

        Note:
            When both dense and sparse vectors are provided, implementations should
            perform hybrid search combining both signals.
        """
        raise NotImplementedError

    @abstractmethod
    def aggregate(
        self,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Perform aggregation operations on the index data.

        Computes aggregate statistics over index records, optionally grouped by
        a field and filtered by conditions.

        Args:
            filters: Aggregation query containing:
                - sorter.op: Aggregation operation (currently only "count" supported)
                - sorter.field: Field name for grouping (None for total count)
                - filter: Pre-aggregation filter conditions
                - sorter.gt/lt/gte/lte: Post-aggregation filter thresholds

        Returns:
            Aggregation results in the format:
                - For total count: {"__total_count__": count}
                - For grouped count: {field_value1: count1, field_value2: count2, ...}

        Raises:
            NotImplementedError: If not implemented by subclass.

        Example:
            # Total count
            aggregate({"sorter": {"op": "count"}})
            # Returns: {"__total_count__": 1000}

            # Count by category
            aggregate({"sorter": {"op": "count", "field": "category"}})
            # Returns: {"electronics": 450, "books": 550}

            # Filtered count
            aggregate({"sorter": {"op": "count", "field": "status"}, "filter": {"price": {"gt": 100}}})
        """
        raise NotImplementedError

    @abstractmethod
    def update(
        self, scalar_index: Optional[Union[List[str], Dict[str, Any]]], description: Optional[str]
    ):
        """Update index metadata and scalar field configuration.

        Modifies the index configuration without requiring a full rebuild.
        Can update the list of scalar fields to index and the index description.

        Args:
            scalar_index: List of field names to build scalar
                indexes on for faster filtering. None means no changes.
            description: Human-readable description of the index.
                None means no changes.

        Raises:
            NotImplementedError: If not implemented by subclass.

        Note:
            Adding new scalar indexes may trigger background index building.
        """
        raise NotImplementedError

    @abstractmethod
    def get_meta_data(self):
        """Retrieve the complete metadata of the index.

        Returns comprehensive information about the index configuration,
        schema, statistics, and operational parameters.

        Returns:
            Index metadata containing:
                - VectorIndex: Vector configuration (dimensions, metric, normalization)
                - ScalarIndex: Scalar field index configuration
                - Description: Human-readable description
                - UpdateTimeStamp: Last modification timestamp
                - Statistics: Data counts, index size, etc.

        Raises:
            NotImplementedError: If not implemented by subclass.
        """
        raise NotImplementedError

    @abstractmethod
    def close(self):
        """Close the index and release resources.

        Flushes any pending writes, closes file handles, releases memory,
        and shuts down background threads. After closing, the index should
        not be used for any operations.

        Raises:
            NotImplementedError: If not implemented by subclass.

        Note:
            For persistent indexes, this may trigger a final persistence
            operation to ensure data durability.
        """
        raise NotImplementedError

    @abstractmethod
    def drop(self):
        """Permanently delete the index and all its data.

        Removes the index structure and all associated data files from storage.
        This operation is irreversible and frees up disk space.

        Raises:
            NotImplementedError: If not implemented by subclass.

        Warning:
            This operation cannot be undone. Ensure proper backups exist
            before calling this method.
        """
        raise NotImplementedError

    def get_newest_version(self) -> Union[int, str, Any]:
        """Get the latest version identifier of the index.

        For persistent indexes with versioning support, returns the timestamp
        or version number of the most recent index snapshot. For volatile
        indexes, may return 0 or a runtime timestamp.

        Returns:
            Version identifier (typically a nanosecond timestamp).
            Returns 0 if versioning is not supported.
        """
        return 0

    def need_rebuild(self) -> bool:
        """Determine if the index needs to be rebuilt.

        Checks if the index has accumulated enough deleted records or fragmentation
        to warrant a full rebuild for space reclamation and performance optimization.

        Subclasses should implement the specific logic for this check based on
        their data structure characteristics.

        Returns:
            True if rebuild is needed, False otherwise.

        Note:
            Rebuilding compacts the index, removes tombstones, and can significantly
            improve search performance and reduce memory/disk usage.
        """
        return True


class Index:
    """
    A wrapper class that encapsulates an IIndex implementation, providing a type-safe interface
    for index-specific operations including data upsert/delete, search, configuration updates,
    and resource management.
    """

    def __init__(self, index: Optional[IIndex]):
        """
        Initialize the Index wrapper with an IIndex-compliant instance.

        Args:
            index: An instance of a class implementing the IIndex interface.
                Must adhere to the IIndex contract for all underlying operations.
                Can be None initially, but must be set before invoking operations.
        """
        self.__index: Optional[IIndex] = index

    def upsert_data(self, delta_list: List[DeltaRecord]):
        """
        Insert new data into the index or update existing data (based on primary key/unique identifier).

        Args:
            delta_list: List of data documents to upsert. Each document
                should contain required fields (e.g., primary key, vector data, scalar fields)
                as defined by the index schema.

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")
        self.__index.upsert_data(delta_list)

    def delete_data(self, delta_list: List[DeltaRecord]):
        """
        Delete specific data entries from the index using identifier information in the delta list.

        Args:
            delta_list: List of documents containing identifiers (e.g., primary keys)
                of the entries to delete. Documents only need to include sufficient fields to uniquely
                identify the records to be removed.

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")
        self.__index.delete_data(delta_list)

    def search(
        self,
        query_vector: Optional[List[float]] = None,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        sparse_raw_terms: Optional[List[str]] = None,
        sparse_values: Optional[List[float]] = None,
    ) -> Tuple[List[int], List[float]]:
        """
        Perform a similarity search on the index using dense vector, sparse vector, and/or filtered criteria.

        Args:
            query_vector: Dense vector for similarity matching (required for dense vector indexes).
                Defaults to None.
            limit: Maximum number of matching results to return. Defaults to 10.
            filters: Optional query filters to refine results (e.g., range conditions on scalar fields,
                exact matches). Defaults to None (no filters).
            sparse_raw_terms: List of terms for sparse vector matching (corresponds to `sparse_values`).
                Defaults to None.
            sparse_values: List of weights corresponding to `sparse_raw_terms` for sparse vector similarity.
                Must have the same length as `sparse_raw_terms` if provided. Defaults to None.

        Returns:
            A tuple containing:
                - List of labels (record identifiers) sorted by similarity
                - List of similarity scores corresponding to each label

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")

        # Handle mutable default arguments
        if filters is None:
            filters = {}
        if sparse_raw_terms is None:
            sparse_raw_terms = []
        if sparse_values is None:
            sparse_values = []

        return self.__index.search(query_vector, limit, filters, sparse_raw_terms, sparse_values)

    def update(
        self,
        scalar_index: Optional[Union[List[str], Dict[str, Any]]],
        description: Optional[str],
    ):
        """
        Update the index's scalar configuration and/or descriptive metadata.

        Args:
            scalar_index: Updated configuration for scalar fields (e.g., field mappings,
                indexing parameters for non-vector data). Defaults to None (no scalar configuration changes).
            description: New descriptive text for the index. Defaults to None (no description update).

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")
        self.__index.update(scalar_index, description)

    def get_meta_data(self) -> Dict[str, Any]:
        """
        Retrieve the complete metadata and configuration of the index.

        Returns:
            A dictionary containing index metadata such as index type, schema definition,
            creation time, performance statistics, and configuration parameters.

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")
        return self.__index.get_meta_data()

    def drop(self):
        """
        Permanently delete the index and free associated resources.
        Irreversible operation that removes the index structure (data may be preserved in the parent collection
        depending on implementation). Sets the underlying IIndex reference to None after deletion.

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            return
        self.__index.drop()
        self.__index = None

    def close(self):
        """
        Close the index connection and release allocated resources (e.g., memory, network connections).
        Should be called explicitly when the index is no longer needed to ensure proper cleanup.
        Sets the underlying IIndex reference to None after closing.
        """
        if self.__index is None:
            return
        self.__index.close()
        self.__index = None

    def get_newest_version(self) -> Union[int, str, Any]:
        """
        Retrieve the latest version identifier of the index.

        Returns:
            Implementation-specific version identifier (e.g., integer version number,
            timestamp string, or version object) representing the most recent state of the index.

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")
        return self.__index.get_newest_version()

    def need_rebuild(self) -> bool:
        """Determine if the index needs to be rebuilt.

        Subclasses should implement the specific logic for this check.

        Returns:
            True if rebuild is needed, False otherwise.

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")
        return self.__index.need_rebuild()

    def aggregate(
        self,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Perform aggregation operations on the index.

        Args:
            filters: Aggregation configuration containing:
                - op: Aggregation operation, currently only supports "count"
                - field: Field name for grouping, None means return total count
                - filters: Filter conditions before aggregation
                - cond: Conditions after aggregation, e.g., {"gt": 10}
                - order: Sort direction "asc" or "desc" (reserved for future use)

        Returns:
            Dictionary containing aggregation results

        Raises:
            RuntimeError: If the underlying index is not initialized.
        """
        if self.__index is None:
            raise RuntimeError("Index is not initialized")
        return self.__index.aggregate(filters)
