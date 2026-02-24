# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Qdrant collection implementation for OpenViking.

Provides ICollection interface implementation using Qdrant vector database,
with support for hybrid search (dense + sparse vectors) and RRF fusion.
"""

import hashlib
import random
import uuid
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Direction,
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchText,
    MatchValue,
    OrderBy,
    PointStruct,
    Prefetch,
    Range,
    SparseVector,
    SparseVectorParams,
    TextIndexParams,
    TextIndexType,
    VectorParams,
)

from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
    UpsertDataResult,
)
from openviking.storage.vectordb.index.index import IIndex
from openviking.utils import get_logger

logger = get_logger(__name__)


# Distance metric mapping from OpenViking to Qdrant
DISTANCE_MAPPING = {
    "cosine": Distance.COSINE,
    "l2": Distance.EUCLID,
    "ip": Distance.DOT,
    "dot": Distance.DOT,
    "euclid": Distance.EUCLID,
}

# Unique namespace UUID for deterministic ID conversion (generated via uuid4)
OPENVIKING_NAMESPACE = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")


def _stable_sparse_index(key: str) -> int:
    """Convert a string sparse key to a stable integer index using MD5."""
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % (2**31)


def _stable_sparse_indices(sparse_vector: Dict[str, float]) -> List[int]:
    """Convert sparse vector keys to stable integer indices."""
    return [int(k) if k.isdigit() else _stable_sparse_index(k) for k in sparse_vector.keys()]


def string_to_qdrant_id(string_id: str) -> str:
    """Convert arbitrary string ID to a valid Qdrant UUID.

    Uses UUID5 (namespace-based) for deterministic conversion,
    so the same string always maps to the same UUID.

    If the input is already a valid UUID, it's returned as-is.
    """
    # Check if already a valid UUID
    try:
        uuid.UUID(string_id)
        return string_id
    except (ValueError, AttributeError):
        pass

    # Convert string to deterministic UUID5
    return str(uuid.uuid5(OPENVIKING_NAMESPACE, string_id))


def get_or_create_qdrant_collection(
    client: QdrantClient,
    collection_name: str,
    meta_data: Optional[Dict[str, Any]] = None,
    vector_dim: int = 0,
    distance_metric: str = "cosine",
    vectorizer_adapter: Optional[Any] = None,
) -> Collection:
    """Create or retrieve a Qdrant Collection.

    Args:
        client: QdrantClient instance
        collection_name: Name of the collection
        meta_data: Collection metadata configuration
        vector_dim: Dimension of dense vectors
        distance_metric: Distance metric for similarity search
        vectorizer_adapter: Optional vectorizer for multimodal search

    Returns:
        Collection: Collection instance wrapping QdrantCollection
    """
    collection = QdrantCollection(
        client=client,
        collection_name=collection_name,
        meta_data=meta_data or {},
        vector_dim=vector_dim,
        distance_metric=distance_metric,
        vectorizer_adapter=vectorizer_adapter,
    )
    return Collection(collection)


class QdrantCollection(ICollection):
    """
    Qdrant implementation of ICollection interface.

    Supports:
    - Dense vector search
    - Sparse vector search
    - Hybrid search with RRF fusion
    - Scalar filtering
    - CRUD operations
    """

    # Reserved field names
    DENSE_VECTOR_NAME = "dense"
    SPARSE_VECTOR_NAME = "sparse"

    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        meta_data: Optional[Dict[str, Any]] = None,
        vector_dim: int = 0,
        distance_metric: str = "cosine",
        vectorizer_adapter: Optional[Any] = None,
    ):
        """Initialize Qdrant collection.

        Args:
            client: QdrantClient instance
            collection_name: Name of the collection
            meta_data: Collection metadata (OpenViking schema format)
            vector_dim: Dimension of dense vectors
            distance_metric: Distance metric for similarity search
            vectorizer_adapter: Optional vectorizer for multimodal search
        """
        super().__init__()
        self.client = client
        self.collection_name = collection_name
        self.meta_data = meta_data or {}
        self.vector_dim = vector_dim
        self.distance = DISTANCE_MAPPING.get(distance_metric.lower(), Distance.COSINE)
        self.vectorizer_adapter = vectorizer_adapter

        # Extract vector dimension from meta_data if not provided
        if self.vector_dim == 0 and self.meta_data.get("Fields"):
            for field in self.meta_data["Fields"]:
                if field.get("FieldType") == "vector":
                    self.vector_dim = field.get("Dim", 0)
                    break

        # Check if collection exists, create if not
        self._ensure_collection()
        self._created_indexes = {"default"}  # Track created index names; "default" always exists

    def _ensure_collection(self):
        """Ensure the collection exists in Qdrant."""
        try:
            collections = self.client.get_collections().collections
            exists = any(c.name == self.collection_name for c in collections)

            if not exists and self.vector_dim > 0:
                logger.info(
                    f"Creating Qdrant collection '{self.collection_name}' "
                    f"with dim={self.vector_dim}, distance={self.distance}"
                )
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={
                        self.DENSE_VECTOR_NAME: VectorParams(
                            size=self.vector_dim,
                            distance=self.distance,
                        )
                    },
                    sparse_vectors_config={
                        self.SPARSE_VECTOR_NAME: SparseVectorParams()
                    },
                )
        except Exception as e:
            logger.error(f"Error ensuring collection: {e}")
            raise

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        """Update collection metadata."""
        if fields:
            self.meta_data.update(fields)
        if description:
            self.meta_data["Description"] = description

    def get_meta_data(self) -> Dict[str, Any]:
        """Get collection metadata."""
        try:
            info = self.client.get_collection(self.collection_name)
            return {
                **self.meta_data,
                "CollectionName": self.collection_name,
                "PointsCount": info.points_count,
                # vectors_count may not exist in all Qdrant versions
                "VectorsCount": getattr(info, 'vectors_count', info.points_count),
                "Status": info.status.name if info.status else "unknown",
            }
        except Exception as e:
            logger.error(f"Error getting collection metadata: {e}")
            return self.meta_data

    def close(self):
        """Close the collection (no-op for Qdrant, client manages connection)."""
        pass

    def drop(self):
        """Drop the collection."""
        try:
            self.client.delete_collection(self.collection_name)
            logger.info(f"Dropped Qdrant collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Error dropping collection: {e}")
            raise

    # =========================================================================
    # Index Operations (Qdrant manages indexes automatically)
    # =========================================================================

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        """Create index — auto-creates payload indexes and text indexes for text-like fields."""
        scalar_fields = meta_data.get("ScalarIndex", [])
        # Fields that should get full-text index (for keyword search support)
        text_field_names = {"content", "abstract", "text", "title", "description", "name"}

        for field in scalar_fields:
            try:
                if field.lower() in text_field_names:
                    # Create full-text index for text-like fields
                    self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field,
                        field_schema=TextIndexParams(
                            type=TextIndexType.TEXT,
                            lowercase=True,
                        ),
                    )
                    logger.info(f"Created text index for field: {field}")
                else:
                    self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name=field,
                        field_schema="keyword",
                    )
            except Exception as e:
                logger.warning(f"Error creating payload index for '{field}': {e}")
        self._created_indexes.add(index_name)
        return None  # Qdrant doesn't expose index objects

    def has_index(self, index_name: str) -> bool:
        """Check if index has been created for this collection."""
        return index_name in self._created_indexes

    def get_index(self, index_name: str) -> Optional[IIndex]:
        """Get index (returns None, Qdrant manages indexes internally)."""
        return None

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        """Update index (no-op for Qdrant)."""
        pass

    def get_index_meta_data(self, index_name: str) -> Dict[str, Any]:
        """Get index metadata."""
        return {"IndexName": index_name, "backend": "qdrant"}

    def list_indexes(self) -> List[str]:
        """List all created indexes."""
        return list(self._created_indexes)

    def drop_index(self, index_name: str):
        """Remove index from tracking."""
        self._created_indexes.discard(index_name)

    # =========================================================================
    # Search Operations
    # =========================================================================

    @staticmethod
    def _payload_selector(output_fields: Optional[List[str]] = None):
        """Convert output_fields to Qdrant with_payload parameter."""
        if output_fields:
            # Always include _original_id for ID mapping
            fields = list(output_fields)
            if QdrantCollection.ORIGINAL_ID_FIELD not in fields:
                fields.append(QdrantCollection.ORIGINAL_ID_FIELD)
            return fields
        return True

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
        """Perform vector similarity search with optional hybrid mode.

        Supports:
        - Dense vector only
        - Sparse vector only
        - Hybrid (dense + sparse) with RRF fusion
        """
        try:
            qdrant_filter = self._convert_filter(filters) if filters else None
            with_payload = self._payload_selector(output_fields)

            # Hybrid search: both dense and sparse vectors
            if dense_vector and sparse_vector:
                return self._hybrid_search(
                    dense_vector=dense_vector,
                    sparse_vector=sparse_vector,
                    limit=limit,
                    offset=offset,
                    qdrant_filter=qdrant_filter,
                    with_payload=with_payload,
                )

            # Dense vector only search
            if dense_vector:
                results = self.client.query_points(
                    collection_name=self.collection_name,
                    query=dense_vector,
                    using=self.DENSE_VECTOR_NAME,
                    limit=limit,
                    offset=offset,
                    query_filter=qdrant_filter,
                    with_payload=with_payload,
                )
                return self._convert_query_results(results)

            # Sparse vector only search
            if sparse_vector:
                indices = _stable_sparse_indices(sparse_vector)
                values = list(sparse_vector.values())

                results = self.client.query_points(
                    collection_name=self.collection_name,
                    query=SparseVector(indices=indices, values=values),
                    using=self.SPARSE_VECTOR_NAME,
                    limit=limit,
                    offset=offset,
                    query_filter=qdrant_filter,
                    with_payload=with_payload,
                )
                return self._convert_query_results(results)

            # No vector provided, return empty
            return SearchResult(data=[])

        except Exception as e:
            logger.error(f"Error in search_by_vector: {e}")
            return SearchResult(data=[])

    def _hybrid_search(
        self,
        dense_vector: List[float],
        sparse_vector: Dict[str, float],
        limit: int,
        offset: int,
        qdrant_filter: Optional[Filter],
        with_payload=True,
    ) -> SearchResult:
        """Perform hybrid search with RRF fusion."""
        try:
            # Convert sparse vector
            indices = _stable_sparse_indices(sparse_vector)
            values = list(sparse_vector.values())

            # Prefetch more results for better fusion
            prefetch_limit = max(limit * 3, 20)

            results = self.client.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    Prefetch(
                        query=dense_vector,
                        using=self.DENSE_VECTOR_NAME,
                        limit=prefetch_limit,
                        filter=qdrant_filter,
                    ),
                    Prefetch(
                        query=SparseVector(indices=indices, values=values),
                        using=self.SPARSE_VECTOR_NAME,
                        limit=prefetch_limit,
                        filter=qdrant_filter,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                offset=offset,
                with_payload=with_payload,
            )

            return self._convert_query_results(results)

        except Exception as e:
            logger.error(f"Error in hybrid search: {e}")
            # Fallback to dense-only search
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=dense_vector,
                using=self.DENSE_VECTOR_NAME,
                limit=limit,
                offset=offset,
                query_filter=qdrant_filter,
                with_payload=with_payload,
            )
            return self._convert_query_results(results)

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
        """Search by keywords using Qdrant full-text index.

        Requires a TextIndex on the target field (e.g., "abstract", "content").
        Uses MatchText filter for keyword matching.
        """
        try:
            # Build search text from keywords or query
            search_text = query or ""
            if keywords:
                search_text = " ".join(keywords)

            if not search_text:
                return SearchResult(data=[])

            # Determine which text fields have full-text indexes
            text_fields = self._get_text_indexed_fields()
            if not text_fields:
                # Fallback: try common field names
                text_fields = ["abstract", "content", "text"]

            # Build full-text filter conditions
            text_conditions = []
            for field_name in text_fields:
                text_conditions.append(
                    FieldCondition(
                        key=field_name,
                        match=MatchText(text=search_text),
                    )
                )

            # Combine text conditions: if multiple fields, use "should" (OR)
            if len(text_conditions) == 1:
                text_filter = Filter(must=text_conditions)
            else:
                text_filter = Filter(should=text_conditions)

            # Merge with user-provided filters
            base_filter = self._convert_filter(filters) if filters else None
            if base_filter:
                final_filter = Filter(must=[text_filter, base_filter])
            else:
                final_filter = text_filter

            # Scroll with filter
            results, _ = self.client.scroll(
                collection_name=self.collection_name,
                limit=limit,
                scroll_filter=final_filter,
                with_payload=True,
            )

            return self._convert_scroll_results(results)

        except Exception as e:
            logger.error(f"Error in search_by_keywords: {e}")
            return SearchResult(data=[])

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        """Search for similar items using an existing document's ID."""
        try:
            original_id = str(id)
            qdrant_id = string_to_qdrant_id(original_id)

            # Fetch the point to get its vector
            points = self.client.retrieve(
                collection_name=self.collection_name,
                ids=[qdrant_id],
                with_vectors=True,
            )

            if not points:
                return SearchResult(data=[])

            point = points[0]
            dense_vector = None

            # Extract dense vector
            if hasattr(point, 'vector') and point.vector:
                if isinstance(point.vector, dict):
                    dense_vector = point.vector.get(self.DENSE_VECTOR_NAME)
                else:
                    dense_vector = point.vector

            if dense_vector:
                result = self.search_by_vector(
                    index_name=index_name,
                    dense_vector=dense_vector,
                    limit=limit + 1,  # +1 to account for self-exclusion
                    offset=offset,
                    filters=filters,
                    output_fields=output_fields,
                )
                # Remove the query document from results
                result.data = [item for item in result.data if item.id != original_id][:limit]
                return result

            return SearchResult(data=[])

        except Exception as e:
            logger.error(f"Error in search_by_id: {e}")
            return SearchResult(data=[])

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str] = None,
        image: Optional[Any] = None,
        video: Optional[Any] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        """Multimodal search via vectorizer delegation (same pattern as LocalCollection)."""
        if not self.vectorizer_adapter:
            raise ValueError("vectorizer is not initialized")

        if not text and not image and not video:
            raise ValueError("At least one of text, image, or video must be provided")

        dense_vector, sparse_vector = self.vectorizer_adapter.vectorize_one(
            text=text, image=image, video=video
        )
        return self.search_by_vector(
            index_name, dense_vector, limit, offset, filters, sparse_vector, output_fields
        )

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        """Retrieve random documents using a random vector query."""
        if self.vector_dim > 0:
            random_vector = [random.uniform(-1, 1) for _ in range(self.vector_dim)]
            return self.search_by_vector(
                index_name, random_vector, limit, offset, filters, None, output_fields
            )

        # Fallback to scroll if vector dimension unknown
        try:
            qdrant_filter = self._convert_filter(filters) if filters else None
            with_payload = self._payload_selector(output_fields)

            results, _ = self.client.scroll(
                collection_name=self.collection_name,
                limit=limit,
                offset=offset,
                scroll_filter=qdrant_filter,
                with_payload=with_payload,
            )

            return self._convert_scroll_results(results)

        except Exception as e:
            logger.error(f"Error in search_by_random: {e}")
            return SearchResult(data=[])

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
        """Retrieve documents sorted by a scalar field using native order_by."""
        try:
            qdrant_filter = self._convert_filter(filters) if filters else None
            direction = Direction.DESC if order == "desc" else Direction.ASC

            results, _ = self.client.scroll(
                collection_name=self.collection_name,
                limit=limit,
                scroll_filter=qdrant_filter,
                with_payload=True,
                order_by=OrderBy(key=field, direction=direction),
            )

            data = []
            for point in results:
                payload = point.payload or {}
                original_id = payload.pop(self.ORIGINAL_ID_FIELD, None) or str(point.id)
                field_value = payload.get(field, 0)
                payload["id"] = original_id
                data.append(SearchItemResult(
                    id=original_id,
                    fields=payload,
                    score=float(field_value) if isinstance(field_value, (int, float)) else 0.0,
                ))

            return SearchResult(data=data)

        except Exception as e:
            logger.error(f"Error in search_by_scalar: {e}")
            return SearchResult(data=[])

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    # Reserved payload field for storing original string ID
    ORIGINAL_ID_FIELD = "_original_id"

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0) -> UpsertDataResult:
        """Insert or update data points."""
        try:
            points = []
            ids = []

            for original_data in data_list:
                # Make a copy to avoid modifying original
                data = dict(original_data)

                # Get or generate original ID
                original_id = data.get("id")
                if not original_id:
                    original_id = str(uuid.uuid4())

                # Convert to valid Qdrant UUID
                qdrant_id = string_to_qdrant_id(original_id)

                # Return original IDs to caller
                ids.append(original_id)

                # Extract vectors
                vectors = {}
                dense_vector = data.pop("vector", None) or data.pop("dense_vector", None)
                sparse_vector = data.pop("sparse_vector", None)

                if dense_vector:
                    vectors[self.DENSE_VECTOR_NAME] = dense_vector

                if sparse_vector:
                    if isinstance(sparse_vector, dict):
                        indices = _stable_sparse_indices(sparse_vector)
                        values = list(sparse_vector.values())
                        vectors[self.SPARSE_VECTOR_NAME] = SparseVector(
                            indices=indices, values=values
                        )
                    elif isinstance(sparse_vector, SparseVector):
                        vectors[self.SPARSE_VECTOR_NAME] = sparse_vector

                # Remaining fields become payload, store original ID for retrieval
                payload = {k: v for k, v in data.items() if k != "id" and v is not None}
                payload[self.ORIGINAL_ID_FIELD] = original_id

                # Skip records without vectors - Qdrant requires valid vectors
                if not vectors:
                    logger.warning(f"Skipping record {original_id}: no vector data")
                    continue

                # Create point with converted UUID
                point = PointStruct(
                    id=qdrant_id,
                    vector=vectors,
                    payload=payload,
                )
                points.append(point)

            # Upsert to Qdrant (skip if no valid points)
            if points:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=points,
                )
            else:
                logger.warning("No valid points to upsert - all records skipped due to missing vectors")

            return UpsertDataResult(ids=ids)

        except Exception as e:
            logger.error(f"Error upserting data: {e}")
            raise

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        """Fetch data by primary keys."""
        try:
            # Convert string IDs to Qdrant UUIDs
            id_mapping = {string_to_qdrant_id(str(pk)): str(pk) for pk in primary_keys}
            qdrant_ids = list(id_mapping.keys())

            points = self.client.retrieve(
                collection_name=self.collection_name,
                ids=qdrant_ids,
                with_payload=True,
            )

            items = []
            found_original_ids = set()

            for point in points:
                payload = point.payload or {}
                # Get original ID from payload or mapping
                original_id = payload.pop(self.ORIGINAL_ID_FIELD, None) or id_mapping.get(str(point.id), str(point.id))
                payload["id"] = original_id
                items.append(DataItem(id=original_id, fields=payload))
                found_original_ids.add(original_id)

            # Find IDs that don't exist (using original IDs)
            ids_not_exist = [str(pk) for pk in primary_keys if str(pk) not in found_original_ids]

            return FetchDataInCollectionResult(items=items, ids_not_exist=ids_not_exist)

        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            return FetchDataInCollectionResult(items=[], ids_not_exist=[str(pk) for pk in primary_keys])

    def delete_data(self, primary_keys: List[Any]):
        """Delete data by primary keys."""
        try:
            # Convert string IDs to Qdrant UUIDs
            qdrant_ids = [string_to_qdrant_id(str(pk)) for pk in primary_keys]

            self.client.delete(
                collection_name=self.collection_name,
                points_selector=qdrant_ids,
            )
        except Exception as e:
            logger.error(f"Error deleting data: {e}")
            raise

    def delete_all_data(self):
        """Delete all data in the collection without dropping it."""
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(
                    filter=Filter()  # Empty filter matches all points
                ),
            )
            logger.info(f"Deleted all data from collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Error deleting all data: {e}")
            raise

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        """Aggregate data (limited support - mainly count)."""
        try:
            qdrant_filter = self._convert_filter(filters) if filters else None

            if op == "count":
                if field:
                    # Group by field — paginated scroll to cover all records
                    counts = {}
                    offset = None
                    while True:
                        results, next_offset = self.client.scroll(
                            collection_name=self.collection_name,
                            limit=1000,
                            offset=offset,
                            scroll_filter=qdrant_filter,
                            with_payload=True,
                        )
                        if not results:
                            break

                        for point in results:
                            payload = point.payload or {}
                            value = str(payload.get(field, "_null_"))
                            counts[value] = counts.get(value, 0) + 1

                        if next_offset is None:
                            break
                        offset = next_offset

                    # Apply conditions if provided
                    if cond:
                        filtered_counts = {}
                        for k, v in counts.items():
                            if "gt" in cond and v <= cond["gt"]:
                                continue
                            if "lt" in cond and v >= cond["lt"]:
                                continue
                            if "gte" in cond and v < cond["gte"]:
                                continue
                            if "lte" in cond and v > cond["lte"]:
                                continue
                            filtered_counts[k] = v
                        counts = filtered_counts

                    return AggregateResult(agg=counts, op=op, field=field)
                else:
                    # Total count — use native exact count
                    count = self.client.count(
                        collection_name=self.collection_name,
                        count_filter=qdrant_filter,
                        exact=True,
                    )
                    return AggregateResult(agg={"_total": count.count}, op=op, field=None)

            return AggregateResult(agg={}, op=op, field=field)

        except Exception as e:
            logger.error(f"Error in aggregate_data: {e}")
            return AggregateResult(agg={"_total": 0}, op=op, field=field)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_text_indexed_fields(self) -> List[str]:
        """Get field names that have full-text indexes."""
        try:
            info = self.client.get_collection(self.collection_name)
            text_fields = []
            if info.payload_schema:
                for field_name, schema in info.payload_schema.items():
                    # Check if field has text index params
                    if hasattr(schema, 'params') and schema.params:
                        params = schema.params
                        # TextIndexParams has tokenizer attribute
                        if hasattr(params, 'tokenizer') and params.tokenizer is not None:
                            text_fields.append(field_name)
                    # Check data_type for text
                    if hasattr(schema, 'data_type') and schema.data_type:
                        if "text" in str(schema.data_type).lower():
                            if field_name not in text_fields:
                                text_fields.append(field_name)
            return text_fields
        except Exception:
            return []

    def _convert_filter(self, filter_dict: Dict[str, Any]) -> Optional[Filter]:
        """Convert OpenViking filter format to Qdrant Filter."""
        if not filter_dict:
            return None

        # If already a Qdrant Filter, return as-is
        if isinstance(filter_dict, Filter):
            return filter_dict

        try:
            # OpenViking filter format: {"op": "must", "field": "name", "conds": ["value"]}
            # or nested: {"op": "and", "conds": [...]}

            op = filter_dict.get("op", "must")
            field = filter_dict.get("field")
            conds = filter_dict.get("conds", [])

            if field:
                # Simple field filter
                conditions = []

                if isinstance(conds, dict):
                    # Range filter: {"gt": 10, "lt": 100}
                    range_params = {}
                    for key in ("gt", "gte", "lt", "lte"):
                        if key in conds:
                            range_params[key] = conds[key]

                    if range_params:
                        conditions.append(FieldCondition(
                            key=field,
                            range=Range(**range_params),
                        ))
                elif isinstance(conds, list):
                    # Filter out any non-scalar values (like Filter objects)
                    scalar_conds = [c for c in conds if isinstance(c, (str, int, float, bool))]
                    if len(scalar_conds) == 1:
                        conditions.append(FieldCondition(
                            key=field,
                            match=MatchValue(value=scalar_conds[0]),
                        ))
                    elif len(scalar_conds) > 1:
                        conditions.append(FieldCondition(
                            key=field,
                            match=MatchAny(any=scalar_conds),
                        ))
                elif isinstance(conds, (str, int, float, bool)):
                    conditions.append(FieldCondition(
                        key=field,
                        match=MatchValue(value=conds),
                    ))

                if op == "must":
                    return Filter(must=conditions)
                elif op == "should":
                    return Filter(should=conditions)
                elif op == "must_not":
                    return Filter(must_not=conditions)

            elif op in ["and", "or"]:
                # Nested conditions - collect all FieldConditions from nested filters
                all_must = []
                all_should = []
                all_must_not = []
                for cond in conds:
                    if isinstance(cond, Filter):
                        if cond.must:
                            all_must.extend(cond.must)
                        if cond.should:
                            all_should.extend(cond.should)
                        if cond.must_not:
                            all_must_not.extend(cond.must_not)
                        continue

                    nested = self._convert_filter(cond)
                    if nested:
                        if nested.must:
                            all_must.extend(nested.must)
                        if nested.should:
                            all_should.extend(nested.should)
                        if nested.must_not:
                            if op == "and":
                                all_must_not.extend(nested.must_not)
                            else:
                                logger.warning(
                                    "must_not inside 'or' filter is not fully supported, "
                                    "conditions will be applied at top level"
                                )
                                all_must_not.extend(nested.must_not)

                all_conditions = all_must or all_should
                if all_conditions or all_must_not:
                    if op == "and":
                        return Filter(
                            must=all_conditions if all_conditions else None,
                            must_not=all_must_not if all_must_not else None,
                        )
                    else:
                        return Filter(
                            should=all_conditions if all_conditions else None,
                            must_not=all_must_not if all_must_not else None,
                        )

            return None

        except Exception as e:
            logger.warning(f"Error converting filter: {e}")
            return None

    def _convert_search_results(self, results) -> SearchResult:
        """Convert Qdrant search results to OpenViking SearchResult."""
        data = []
        for point in results:
            payload = point.payload or {}
            # Use original ID from payload if available
            original_id = payload.pop(self.ORIGINAL_ID_FIELD, None) or str(point.id)
            payload["id"] = original_id
            data.append(SearchItemResult(
                id=original_id,
                fields=payload,
                score=point.score,
            ))
        return SearchResult(data=data)

    def _convert_query_results(self, results) -> SearchResult:
        """Convert Qdrant query_points results to OpenViking SearchResult."""
        data = []
        for point in results.points:
            payload = point.payload or {}
            # Use original ID from payload if available
            original_id = payload.pop(self.ORIGINAL_ID_FIELD, None) or str(point.id)
            payload["id"] = original_id
            data.append(SearchItemResult(
                id=original_id,
                fields=payload,
                score=point.score if hasattr(point, 'score') else 0.0,
            ))
        return SearchResult(data=data)

    def _convert_scroll_results(self, results) -> SearchResult:
        """Convert Qdrant scroll results to OpenViking SearchResult."""
        data = []
        for point in results:
            payload = point.payload or {}
            # Use original ID from payload if available
            original_id = payload.pop(self.ORIGINAL_ID_FIELD, None) or str(point.id)
            payload["id"] = original_id
            data.append(SearchItemResult(
                id=original_id,
                fields=payload,
                score=0.0,  # Scroll doesn't have scores
            ))
        return SearchResult(data=data)
