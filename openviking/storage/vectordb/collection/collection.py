# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from openviking.storage.vectordb.collection.result import AggregateResult, SearchResult
from openviking.storage.vectordb.index.index import IIndex


class ICollection(ABC):
    def __init__(self):
        pass

    @abstractmethod
    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        raise NotImplementedError

    @abstractmethod
    def get_meta_data(self):
        raise NotImplementedError

    @abstractmethod
    def close(self):
        raise NotImplementedError

    @abstractmethod
    def drop(self):
        raise NotImplementedError

    @abstractmethod
    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        raise NotImplementedError

    @abstractmethod
    def has_index(self, index_name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_index(self, index_name: str) -> Optional[IIndex]:
        raise NotImplementedError

    @abstractmethod
    def search_by_vector(
        self,
        index_name: str,
        dense_vector: Optional[List[float]] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sparse_vector: Optional[Dict[str, float]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError

    @abstractmethod
    def search_by_keywords(
        self,
        index_name: str,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError

    @abstractmethod
    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError

    @abstractmethod
    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str],
        image: Optional[Any],
        video: Optional[Any],
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError

    @abstractmethod
    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError

    @abstractmethod
    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: Optional[str] = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError

    @abstractmethod
    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        raise NotImplementedError

    @abstractmethod
    def get_index_meta_data(self, index_name: str):
        raise NotImplementedError

    @abstractmethod
    def list_indexes(self):
        raise NotImplementedError

    @abstractmethod
    def drop_index(self, index_name: str):
        raise NotImplementedError

    @abstractmethod
    def upsert_data(self, data_list: List[Dict[str, Any]], ttl=0):
        raise NotImplementedError

    @abstractmethod
    def fetch_data(self, primary_keys: List[Any]):
        raise NotImplementedError

    @abstractmethod
    def delete_data(self, primary_keys: List[Any]):
        raise NotImplementedError

    @abstractmethod
    def delete_all_data(self):
        raise NotImplementedError

    @abstractmethod
    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        """Aggregate data on the specified index.

        Args:
            index_name: Name of the index to aggregate on
            op: Aggregation operation, currently only supports "count"
            field: Field name for grouping, None means return total count
            filters: Filter conditions before aggregation
            cond: Conditions after aggregation, e.g., {"gt": 10}

        Returns:
            AggregateResult: Object containing aggregation results
        """
        raise NotImplementedError


class Collection:
    """
    A wrapper class that encapsulates an ICollection implementation, providing a consistent interface
    for collection management, index operations, and data manipulation.
    """

    def __init__(self, collection: ICollection):
        """
        Initialize the Collection wrapper with an ICollection instance.

        Args:
            collection (ICollection): An instance of a class implementing the ICollection interface.
                Must conform to the ICollection contract for all underlying operations.

        Raises:
            AssertionError: If the provided `collection` is not an instance of ICollection.
        """
        assert isinstance(collection, ICollection), (
            "collection must be an instance of CollectionInterface"
        )
        self.__collection: Optional[ICollection] = collection

    def __del__(self):
        """
        Destructor method that cleans up the underlying ICollection instance.
        Closes the collection connection and sets the reference to None to free resources.
        """
        if self.__collection:
            self.__collection.close()
            self.__collection = None

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        """
        Update the collection's metadata fields and/or description.

        Args:
            fields (Optional[Dict[str, Any]]): Dictionary of key-value pairs representing
                metadata fields to update. Defaults to None.
            description (Optional[str]): New description for the collection. Defaults to None.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.update(fields, description)

    def drop(self):
        """
        Permanently delete the entire collection and all its associated data/indexes.
        Irreversible operation that removes the collection from the storage system.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.drop()

    def get_meta_data(self) -> Dict[str, Any]:
        """
        Retrieve the full metadata of the collection.

        Returns:
            Dict[str, Any]: A dictionary containing the collection's metadata (e.g., creation time,
                configuration settings, statistics).
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.get_meta_data()

    def get_meta(self) -> Dict[str, Any]:
        """
        Retrieve a simplified version of the collection's metadata.

        Returns:
            Dict[str, Any]: A condensed dictionary of key collection metadata properties.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.get_meta_data()

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> Any:
        """
        Create a new index for the collection with the specified configuration.

        Args:
            index_name (str): Unique name to identify the index. Must not conflict with existing indexes.
            meta_data (Dict[str, Any]): Index configuration metadata (e.g., index type, field mappings,
                distance metric for vector indexes).

        Returns:
            Any: Implementation-specific result (e.g., index ID, success confirmation).
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.create_index(index_name, meta_data)

    def has_index(self, index_name: str) -> bool:
        """
        Check if an index with the specified name exists in the collection.

        Args:
            index_name (str): Name of the index to check for existence.

        Returns:
            bool: True if the index exists; False otherwise.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.has_index(index_name)

    def get_index(self, index_name: str) -> Any:
        """
        Retrieve the index instance or its detailed configuration by name.

        Args:
            index_name (str): Name of the index to retrieve.

        Returns:
            Any: Implementation-specific index object or configuration details.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.get_index(index_name)

    def search_by_vector(
        self,
        index_name: str,
        dense_vector: Optional[List[float]] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sparse_vector: Optional[Dict[str, float]] = None,
        output_fields: Optional[List[str]] = None,
    ):
        """Perform vector similarity search on the specified index.

        Args:
            index_name (str): Name of the index to search against.
            dense_vector (Optional[List[float]]): Dense vector for similarity search. Defaults to None.
            limit (int): Maximum number of results to return. Defaults to 10.
            offset (int): Number of results to skip before returning. Defaults to 0.
            filters (Optional[Dict[str, Any]]): Query filters to narrow down results. Defaults to None.
            sparse_vector (Optional[Dict[str, float]]): Sparse vector represented as term-weight pairs.
                Defaults to None.
            output_fields (Optional[List[str]]): List of field names to include in results.
                If None, returns all fields. Defaults to None.

        Returns:
            SearchResult: Search results containing matching documents with scores and field values.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.search_by_vector(
            index_name, dense_vector, limit, offset, filters, sparse_vector, output_fields
        )

    def search_by_keywords(
        self,
        index_name: str,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ):
        """Search by keywords or query string using vectorization.

        Args:
            index_name (str): Name of the index to search against.
            keywords (Optional[List[str]]): List of keywords to search for. Defaults to None.
            query (Optional[str]): Query string to search for. Defaults to None.
            limit (int): Maximum number of results to return. Defaults to 10.
            offset (int): Number of results to skip before returning. Defaults to 0.
            filters (Optional[Dict[str, Any]]): Query filters to narrow down results. Defaults to None.
            output_fields (Optional[List[str]]): List of field names to include in results.
                If None, returns all fields. Defaults to None.

        Returns:
            SearchResult: Search results containing matching documents with scores and field values.

        Note:
            At least one of keywords or query must be provided. The input will be vectorized
            using the configured vectorizer before performing similarity search.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.search_by_keywords(
            index_name, keywords, query, limit, offset, filters, output_fields
        )

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ):
        """Search for similar items using an existing document's ID.

        Args:
            index_name (str): Name of the index to search against.
            id (Any): Primary key of the document to use as the query.
            limit (int): Maximum number of results to return. Defaults to 10.
            offset (int): Number of results to skip before returning. Defaults to 0.
            filters (Optional[Dict[str, Any]]): Query filters to narrow down results. Defaults to None.
            output_fields (Optional[List[str]]): List of field names to include in results.
                If None, returns all fields. Defaults to None.

        Returns:
            SearchResult: Search results containing similar documents with scores and field values.

        Note:
            This method retrieves the vector of the document identified by the given ID
            and uses it to find similar documents.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.search_by_id(index_name, id, limit, offset, filters, output_fields)

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str],
        image: Optional[Any],
        video: Optional[Any],
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ):
        """Search using multimodal inputs (text, image, and/or video).

        Args:
            index_name (str): Name of the index to search against.
            text (Optional[str]): Text query for multimodal search. Defaults to None.
            image (Optional[Any]): Image data for multimodal search. Defaults to None.
            video (Optional[Any]): Video data for multimodal search. Defaults to None.
            limit (int): Maximum number of results to return. Defaults to 10.
            offset (int): Number of results to skip before returning. Defaults to 0.
            filters (Optional[Dict[str, Any]]): Query filters to narrow down results. Defaults to None.
            output_fields (Optional[List[str]]): List of field names to include in results.
                If None, returns all fields. Defaults to None.

        Returns:
            SearchResult: Search results containing matching documents with scores and field values.

        Note:
            At least one of text, image, or video must be provided. A multimodal vectorizer
            must be configured to process these inputs.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.search_by_multimodal(
            index_name, text, image, video, limit, offset, filters, output_fields
        )

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ):
        """Retrieve random documents from the index.

        Args:
            index_name (str): Name of the index to search against.
            limit (int): Maximum number of results to return. Defaults to 10.
            offset (int): Number of results to skip before returning. Defaults to 0.
            filters (Optional[Dict[str, Any]]): Query filters to narrow down results. Defaults to None.
            output_fields (Optional[List[str]]): List of field names to include in results.
                If None, returns all fields. Defaults to None.

        Returns:
            SearchResult: Random documents from the index with field values (scores are not meaningful).

        Note:
            This method uses a random vector for similarity search, which approximates random sampling.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.search_by_random(index_name, limit, offset, filters, output_fields)

    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: Optional[str] = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ):
        """Retrieve documents sorted by a scalar field value.

        Args:
            index_name (str): Name of the index to search against.
            field (str): Field name to sort by. Must be a scalar field (numeric or string).
            order (Optional[str]): Sort order, either 'desc' (descending) or 'asc' (ascending).
                Defaults to 'desc'.
            limit (int): Maximum number of results to return. Defaults to 10.
            offset (int): Number of results to skip before returning. Defaults to 0.
            filters (Optional[Dict[str, Any]]): Query filters to narrow down results. Defaults to None.
            output_fields (Optional[List[str]]): List of field names to include in results.
                If None, returns all fields. Defaults to None.

        Returns:
            SearchResult: Documents sorted by the specified field. The score field contains
                the scalar field value.

        Note:
            This method performs a scalar field sort rather than vector similarity search.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.search_by_scalar(
            index_name, field, order, limit, offset, filters, output_fields
        )

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        """
        Update the configuration or description of an existing index.

        Args:
            index_name (str): Name of the index to update.
            scalar_index (Optional[Dict[str, Any]]): Updated configuration for scalar indexes.
                Defaults to None.
            description (Optional[str]): New description for the index. Defaults to None.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.update_index(index_name, scalar_index, description)

    def get_index_meta_data(self, index_name: str) -> Dict[str, Any]:
        """
        Retrieve the metadata of a specific index.

        Args:
            index_name (str): Name of the index to get metadata for.

        Returns:
            Dict[str, Any]: Dictionary containing the index's metadata (e.g., type, configuration, stats).
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.get_index_meta_data(index_name)

    def list_indexes(self) -> List[str]:
        """
        List the names of all indexes associated with the collection.

        Returns:
            List[str]: A list of index names existing in the collection.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.list_indexes()

    def drop_index(self, index_name: str):
        """
        Delete a specific index from the collection.
        Does not affect the underlying collection data, only the index structure.

        Args:
            index_name (str): Name of the index to delete.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.drop_index(index_name)

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: Optional[int] = 0) -> Any:
        """
        Insert new data into the collection or update existing data (based on primary key).

        Args:
            data_list (List[Dict[str, Any]]): List of data documents to upsert. Each document
                must contain required fields (including primary key for updates).
            ttl (Optional[int]): Time-to-live (in seconds) for the inserted/updated data.
                Data will be automatically deleted after TTL expires. Defaults to 0 (no expiration).

        Returns:
            Any: Implementation-specific result (e.g., number of documents upserted, success status).
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.upsert_data(data_list, ttl)

    def fetch_data(self, primary_keys: List[Any]) -> List[Dict[str, Any]]:
        """
        Retrieve data documents from the collection using their primary keys.

        Args:
            primary_keys (List[Any]): List of primary key values corresponding to the documents to fetch.

        Returns:
            List[Dict[str, Any]]: List of retrieved documents (in the same order as input primary keys).
                Missing keys will return empty entries or be omitted (implementation-dependent).
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.fetch_data(primary_keys)

    def delete_data(self, primary_keys: List[Any]):
        """
        Delete specific data documents from the collection using their primary keys.

        Args:
            primary_keys (List[Any]): List of primary key values corresponding to the documents to delete.
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.delete_data(primary_keys)

    def delete_all_data(self):
        """
        Delete all data documents from the collection.
        Preserves the collection structure and indexes (only removes data records).
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.delete_all_data()

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        """Aggregate data on the specified index.

        Args:
            index_name (str): Name of the index to aggregate on
            op (str): Aggregation operation, currently only supports "count"
            field (Optional[str]): Field name for grouping, None means return total count
            filters (Optional[Dict[str, Any]]): Filter conditions before aggregation
            cond (Optional[Dict[str, Any]]): Conditions after aggregation, e.g., {"gt": 10}

        Returns:
            AggregateResult: Object containing aggregation results
        """
        if self.__collection is None:
            raise RuntimeError("Collection is closed")
        return self.__collection.aggregate_data(index_name, op, field, filters, cond)

    def close(self):
        """
        Close the connection to the collection and release associated resources.
        Should be called explicitly when the collection is no longer needed (in addition to destructor).
        """
        if self.__collection:
            self.__collection.close()
            self.__collection = None
