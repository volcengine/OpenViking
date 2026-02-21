# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VikingDB storage backend for OpenViking.

Implements the VikingDBInterface using the custom vectordb implementation.
Supports both in-memory and local persistent storage modes.
"""

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import FetchDataInCollectionResult
from openviking.storage.vectordb.utils.logging_init import init_cpp_logging
from openviking.storage.vikingdb_interface import CollectionNotFoundError, VikingDBInterface
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class VikingVectorIndexBackend(VikingDBInterface):
    """
    VikingDB storage backend implementation.

    Features:
    - Vector similarity search with BruteForce indexing
    - Scalar filtering with support for multiple operators
    - Support for local persistent storage, HTTP service, and Volcengine VikingDB
    - Auto-managed indexes per collection

    VikingDBManager is derived by VikingVectorIndexBackend.
    """

    # Default project and index names
    DEFAULT_PROJECT_NAME = "vectordb"
    DEFAULT_INDEX_NAME = "default"

    def __init__(
        self,
        config: Optional[VectorDBBackendConfig],
    ):
        """
        Initialize VikingDB backend.

        Args:
            config: Configuration object for VectorDB backend.

        Examples:
            # 1. Local persistent storage
            config = VectorDBBackendConfig(
                backend="local",
                path="./data/vectordb"
            )
            backend = VikingVectorIndexBackend(config=config)

            # 2. Remote HTTP service
            config = VectorDBBackendConfig(
                backend="http",
                url="http://localhost:5000"
            )
            backend = VikingVectorIndexBackend(config=config)

            # 3. Volcengine VikingDB
            from openviking_cli.utils.config.storage_config import VolcengineConfig
            config = VectorDBBackendConfig(
                backend="volcengine",
                volcengine=VolcengineConfig(
                    ak="your-ak",
                    sk="your-sk",
                    region="cn-beijing"
                )
            )
            backend = VikingVectorIndexBackend(config=config)
        """
        init_cpp_logging()

        self.vector_dim = config.dimension
        self.distance_metric = config.distance_metric
        self.sparse_weight = config.sparse_weight

        if config.backend == "volcengine":
            if not (
                config.volcengine
                and config.volcengine.ak
                and config.volcengine.sk
                and config.volcengine.region
            ):
                raise ValueError("Volcengine backend requires AK, SK, and Region configuration")

            # Volcengine VikingDB mode
            self._mode = config.backend
            # Convert lowercase keys to uppercase for consistency with volcengine_collection
            volc_config = {
                "AK": config.volcengine.ak,
                "SK": config.volcengine.sk,
                "Region": config.volcengine.region,
            }

            from openviking.storage.vectordb.project.volcengine_project import (
                get_or_create_volcengine_project,
            )

            self.project = get_or_create_volcengine_project(
                project_name=self.DEFAULT_PROJECT_NAME, config=volc_config
            )
            logger.info(
                f"VectorDB backend initialized in Volcengine mode: region={volc_config['Region']}"
            )
        elif config.backend == "vikingdb":
            if not config.vikingdb.host:
                raise ValueError("VikingDB backend requires a valid host")
            # VikingDB private deployment mode
            self._mode = config.backend
            viking_config = {
                "Host": config.vikingdb.host,
                "Headers": config.vikingdb.headers,
            }

            from openviking.storage.vectordb.project.vikingdb_project import (
                get_or_create_vikingdb_project,
            )

            self.project = get_or_create_vikingdb_project(
                project_name=self.DEFAULT_PROJECT_NAME, config=viking_config
            )
            logger.info(f"VikingDB backend initialized in private mode: {config.vikingdb.host}")
        elif config.backend == "http":
            if not config.url:
                raise ValueError("HTTP backend requires a valid URL")
            # Remote mode: parse URL and create HTTP project
            self._mode = config.backend
            self.host, self.port = self._parse_url(config.url)

            from openviking.storage.vectordb.project.http_project import get_or_create_http_project

            self.project = get_or_create_http_project(
                host=self.host, port=self.port, project_name=self.DEFAULT_PROJECT_NAME
            )
            logger.info(f"VikingDB backend initialized in remote mode: {config.url}")
        elif config.backend == "local":
            # Local persistent mode
            self._mode = config.backend
            from openviking.storage.vectordb.project.local_project import (
                get_or_create_local_project,
            )

            project_path = Path(config.path) / self.DEFAULT_PROJECT_NAME if config.path else ""
            self.project = get_or_create_local_project(path=str(project_path))
            logger.info(f"VikingDB backend initialized with local storage: {project_path}")
        else:
            raise ValueError(f"Unsupported VikingDB backend type: {config.type}")

        self._collection_configs: Dict[str, Dict[str, Any]] = {}
        # Cache meta_data at collection level to avoid repeated remote calls
        self._meta_data_cache: Dict[str, Dict[str, Any]] = {}

    def _parse_url(self, url: str) -> Tuple[str, int]:
        """
        Parse VikingVectorIndex service URL to extract host and port.

        Args:
            url: Service URL (e.g., "http://localhost:5000" or "localhost:5000")

        Returns:
            Tuple of (host, port)
        """
        from urllib.parse import urlparse

        # Add scheme if not present
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"

        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5000

        return host, port

    def _get_collection(self, name: str) -> Collection:
        """Get collection object or raise error if not found."""
        if not self.project.has_collection(name):
            raise CollectionNotFoundError(f"Collection '{name}' does not exist")
        return self.project.get_collection(name)

    def _get_meta_data(self, collection_name: str, coll: Collection) -> Dict[str, Any]:
        """Get meta_data with collection-level caching to avoid repeated remote calls."""
        if collection_name not in self._meta_data_cache:
            self._meta_data_cache[collection_name] = coll.get_meta_data()
        return self._meta_data_cache[collection_name]

    def _update_meta_data_cache(self, collection_name: str, coll: Collection):
        """Update the cached meta_data after modifications."""
        meta_data = coll.get_meta_data()
        self._meta_data_cache[collection_name] = meta_data

    # =========================================================================
    # Collection/Table Management
    # =========================================================================

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        """
        Create a new collection.

        Args:
            name: Collection name
            schema: VikingVectorIndex collection metadata in the format:
                {
                    "CollectionName": "name",
                    "Description": "description",
                    "Fields": [
                        {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                        {"FieldName": "vector", "FieldType": "vector", "Dim": 128},
                        ...
                    ]
                }

        Returns:
            True if created successfully, False if already exists
        """
        try:
            if self.project.has_collection(name):
                logger.debug(f"Collection '{name}' already exists")
                return False

            collection_meta = schema.copy()

            scalar_index_fields = []
            if "ScalarIndex" in collection_meta:
                scalar_index_fields = collection_meta.pop("ScalarIndex")

            # Ensure CollectionName is set
            if "CollectionName" not in collection_meta:
                collection_meta["CollectionName"] = name

            # Extract distance metric and vector_dim for config tracking
            distance = self.distance_metric
            vector_dim = self.vector_dim
            for field in collection_meta.get("Fields", []):
                if field.get("FieldType") == "vector":
                    vector_dim = field.get("Dim", self.vector_dim)
                    break

            logger.info(f"Creating collection mode={self._mode} with meta: {collection_meta}")

            # Create collection using vectordb project
            collection = self.project.create_collection(name, collection_meta)

            # Filter date_time fields for volcengine and vikingdb backends
            if self._mode in ["volcengine", "vikingdb"]:
                date_time_fields = {
                    field.get("FieldName")
                    for field in collection_meta.get("Fields", [])
                    if field.get("FieldType") == "date_time"
                }
                scalar_index_fields = [
                    field for field in scalar_index_fields if field not in date_time_fields
                ]

            # Create default index for the collection
            use_sparse = self.sparse_weight > 0.0
            index_type = "flat_hybrid" if use_sparse else "flat"
            if self._mode in ["volcengine", "vikingdb"]:
                index_type = "hnsw_hybrid" if use_sparse else "hnsw"

            index_meta = {
                "IndexName": self.DEFAULT_INDEX_NAME,
                "VectorIndex": {
                    "IndexType": index_type,
                    "Distance": distance,
                    "Quant": "int8",
                },
                "ScalarIndex": scalar_index_fields,
            }
            if use_sparse:
                index_meta["VectorIndex"]["EnableSparse"] = True
                index_meta["VectorIndex"]["SearchWithSparseLogitAlpha"] = self.sparse_weight

            logger.info(f"Creating index with meta: {index_meta}")
            collection.create_index(self.DEFAULT_INDEX_NAME, index_meta)

            # Update cached meta_data after creating index
            self._update_meta_data_cache(name, collection)

            # Store collection config
            self._collection_configs[name] = {
                "vector_dim": vector_dim,
                "distance": distance,
                "schema": schema,
            }

            logger.info(f"Created VikingDB collection: {name} (dim={vector_dim})")
            return True

        except Exception as e:
            logger.error(f"Error creating collection '{name}': {e}")
            import traceback

            traceback.print_exc()
            return False

    async def drop_collection(self, name: str) -> bool:
        """Drop a collection."""
        try:
            if not self.project.has_collection(name):
                logger.warning(f"Collection '{name}' does not exist")
                return False

            self.project.drop_collection(name)
            self._collection_configs.pop(name, None)
            # Clear cached meta_data when dropping collection
            self._meta_data_cache.pop(name, None)

            logger.info(f"Dropped collection: {name}")
            return True
        except Exception as e:
            logger.error(f"Error dropping collection '{name}': {e}")
            return False

    async def collection_exists(self, name: str) -> bool:
        """Check if a collection exists."""
        return self.project.has_collection(name)

    async def list_collections(self) -> List[str]:
        """List all collection names."""
        return self.project.list_collections()

    async def get_collection_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get collection metadata and statistics."""
        try:
            if not self.project.has_collection(name):
                return None

            config = self._collection_configs.get(name, {})

            return {
                "name": name,
                "vector_dim": config.get("vector_dim", self.vector_dim),
                "count": 0,  # vectordb doesn't easily expose count
                "status": "active",
            }
        except Exception as e:
            logger.error(f"Error getting collection info for '{name}': {e}")
            return None

    # =========================================================================
    # CRUD Operations - Single Record
    # =========================================================================

    async def insert(self, collection: str, data: Dict[str, Any]) -> str:
        """Insert a single record."""
        coll = self._get_collection(collection)

        # Ensure ID exists
        record_id = data.get("id")
        if not record_id:
            record_id = str(uuid.uuid4())
            data = {**data, "id": record_id}

        # Validate context_type for context collection
        if collection == "context":
            context_type = data.get("context_type")
            if context_type not in ["resource", "skill", "memory"]:
                logger.warning(
                    f"Invalid context_type: {context_type}. "
                    f"Must be one of ['resource', 'skill', 'memory'], Ignore"
                )
                return ""

        fields = self._get_meta_data(collection, coll).get("Fields", [])
        fields_dict = {item["FieldName"]: item for item in fields}
        new_data = {}
        for k in data:
            if k in fields_dict and data[k] is not None:
                new_data[k] = data[k]

        try:
            coll.upsert_data([new_data])
            return record_id
        except Exception as e:
            logger.error(f"Error inserting record: {e}")
            raise

    async def update(self, collection: str, id: str, data: Dict[str, Any]) -> bool:
        """Update a record by ID."""
        coll = self._get_collection(collection)

        try:
            # Fetch existing record
            existing = await self.get(collection, [id])
            if not existing:
                return False

            # Merge data with existing record
            updated_data = {**existing[0], **data}
            updated_data["id"] = id

            # Upsert the updated record
            coll.upsert_data([updated_data])
            return True
        except Exception as e:
            logger.error(f"Error updating record '{id}': {e}")
            return False

    async def upsert(self, collection: str, data: Dict[str, Any]) -> str:
        """Insert or update a record."""
        coll = self._get_collection(collection)

        record_id = data.get("id")
        if not record_id:
            record_id = str(uuid.uuid4())
            data = {**data, "id": record_id}

        try:
            coll.upsert_data([data])
            return record_id
        except Exception as e:
            logger.error(f"Error upserting record: {e}")
            raise

    async def delete(self, collection: str, ids: List[str]) -> int:
        """Delete records by IDs."""
        coll = self._get_collection(collection)

        try:
            coll.delete_data(ids)
            return len(ids)
        except Exception as e:
            logger.error(f"Error deleting records: {e}")
            return 0

    async def get(self, collection: str, ids: List[str]) -> List[Dict[str, Any]]:
        """Get records by IDs."""
        coll = self._get_collection(collection)

        try:
            result = coll.fetch_data(ids)

            if isinstance(result, FetchDataInCollectionResult):
                records = []
                for item in result.items:
                    record = dict(item.fields) if item.fields else {}
                    record["id"] = item.id
                    records.append(record)
                return records
            elif isinstance(result, dict):
                records = []
                if "fetch" in result:
                    for item in result.get("fetch", []):
                        record = dict(item.get("fields", {})) if item.get("fields") else {}
                        record["id"] = item.get("id")
                        if record["id"]:
                            records.append(record)
                return records
            else:
                logger.warning(f"Unexpected return type from fetch_data: {type(result)}")
                return []
        except Exception as e:
            logger.error(f"Error getting records: {e}")
            return []

    async def fetch_by_uri(self, collection: str, uri: str) -> Optional[Dict[str, Any]]:
        """Fetch a record by URI."""
        coll = self._get_collection(collection)
        try:
            result = coll.search_by_random(
                index_name=self.DEFAULT_INDEX_NAME,
                limit=10,
                filters={"op": "must", "field": "uri", "conds": [uri]},
            )
            records = []
            for item in result.data:
                record = dict(item.fields) if item.fields else {}
                record["id"] = item.id
                records.append(record)
            if len(records) > 1:
                raise ValueError(f"Duplicate records found for URI: {uri}")
            if len(records) == 0:
                raise ValueError(f"Record not found for URI: {uri}")
            return records[0]
        except Exception as e:
            logger.error(f"Error fetching record by URI '{uri}': {e}")
            return None

    async def exists(self, collection: str, id: str) -> bool:
        """Check if a record exists."""
        try:
            results = await self.get(collection, [id])
            return len(results) > 0
        except Exception:
            return False

    # =========================================================================
    # CRUD Operations - Batch
    # =========================================================================

    async def batch_insert(self, collection: str, data: List[Dict[str, Any]]) -> List[str]:
        """Batch insert multiple records."""
        coll = self._get_collection(collection)

        # Ensure all records have IDs
        ids = []
        records_with_ids = []
        for record in data:
            if "id" not in record:
                record_id = str(uuid.uuid4())
                records_with_ids.append({**record, "id": record_id})
                ids.append(record_id)
            else:
                records_with_ids.append(record)
                ids.append(record["id"])

        try:
            coll.upsert_data(records_with_ids)
            return ids
        except Exception as e:
            logger.error(f"Error batch inserting records: {e}")
            raise

    async def batch_upsert(self, collection: str, data: List[Dict[str, Any]]) -> List[str]:
        """Batch insert or update multiple records."""
        coll = self._get_collection(collection)

        ids = []
        records_with_ids = []
        for record in data:
            if "id" not in record:
                record_id = str(uuid.uuid4())
                records_with_ids.append({**record, "id": record_id})
                ids.append(record_id)
            else:
                records_with_ids.append(record)
                ids.append(record["id"])

        try:
            coll.upsert_data(records_with_ids)
            return ids
        except Exception as e:
            logger.error(f"Error batch upserting records: {e}")
            raise

    async def batch_delete(self, collection: str, filter: Dict[str, Any]) -> int:
        """Delete records matching filter conditions."""
        try:
            # First, find matching records
            matching_records = await self.filter(collection, filter, limit=10000)

            if not matching_records:
                return 0

            # Extract IDs and delete
            ids = [record["id"] for record in matching_records if "id" in record]
            return await self.delete(collection, ids)
        except Exception as e:
            logger.error(f"Error batch deleting records: {e}")
            return 0

    async def remove_by_uri(self, collection: str, uri: str) -> int:
        """Remove resource(s) by URI."""
        try:
            target_records = await self.filter(
                collection=collection,
                filter={"op": "must", "field": "uri", "conds": [uri]},
                limit=10,
            )

            if not target_records:
                return 0

            total_deleted = 0

            # If any record indicates this URI is a directory node, remove descendants first.
            if any(not r.get("is_leaf", False) for r in target_records):
                descendant_count = await self._remove_descendants(collection, uri)
                total_deleted += descendant_count

            ids = [r.get("id") for r in target_records if r.get("id")]
            if ids:
                total_deleted += await self.delete(collection, ids)

            logger.info(f"Removed {total_deleted} record(s) for URI: {uri}")
            return total_deleted

        except Exception as e:
            logger.error(f"Error removing URI '{uri}': {e}")
            return 0

    async def _remove_descendants(self, collection: str, parent_uri: str) -> int:
        """Recursively remove all descendants of a parent URI."""
        total_deleted = 0

        # Find direct children
        children = await self.filter(
            collection=collection,
            filter={"op": "must", "field": "parent_uri", "conds": [parent_uri]},
            limit=10000,
        )

        for child in children:
            child_uri = child.get("uri")
            is_leaf = child.get("is_leaf", False)

            # Recursively delete if child is also an intermediate directory
            if not is_leaf and child_uri:
                descendant_count = await self._remove_descendants(collection, child_uri)
                total_deleted += descendant_count

            # Delete the child
            if "id" in child:
                await self.delete(collection, [child["id"]])
                total_deleted += 1

        return total_deleted

    # =========================================================================
    # Search Operations
    # =========================================================================

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
        """Hybrid search: vector similarity (dense/sparse/hybrid) + scalar filtering.

        Args:
            collection: Collection name, by default it should be "context"
            query_vector: Dense query vector (optional)
            sparse_query_vector: Sparse query vector as {term: weight} dict (optional)
            filter: Scalar filter conditions
            limit: Maximum number of results
            offset: Offset for pagination
            output_fields: Fields to return
            with_vector: Whether to include vector field in results

        Returns:
            List of matching records with scores
        """
        coll = self._get_collection(collection)

        try:
            # Filter is already in vectordb DSL format
            vectordb_filter = filter if filter else {}

            if query_vector or sparse_query_vector:
                # Vector search (dense, sparse, or hybrid) with optional filtering
                result = coll.search_by_vector(
                    index_name=self.DEFAULT_INDEX_NAME,
                    dense_vector=query_vector,
                    sparse_vector=sparse_query_vector,
                    limit=limit,
                    offset=offset,
                    filters=vectordb_filter,
                    output_fields=output_fields,
                )

                # Convert results
                records = []
                for item in result.data:
                    record = dict(item.fields) if item.fields else {}
                    record["id"] = item.id
                    record["_score"] = item.score if item.score is not None else 0.0

                    if not with_vector:
                        if "vector" in record:
                            record.pop("vector")
                        if "sparse_vector" in record:
                            record.pop("sparse_vector")

                    records.append(record)

                return records
            else:
                # Pure filtering without vector search
                return await self.filter(collection, filter or {}, limit, offset, output_fields)

        except Exception as e:
            logger.error(f"Error searching collection '{collection}': {e}")
            import traceback

            traceback.print_exc()
            return []

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
        """Pure scalar filtering without vector search."""
        coll = self._get_collection(collection)

        try:
            # Filter is already in vectordb DSL format
            vectordb_filter = filter if filter else {}

            if order_by:
                # Use search_by_scalar for sorting
                result = coll.search_by_scalar(
                    index_name=self.DEFAULT_INDEX_NAME,
                    field=order_by,
                    order="desc" if order_desc else "asc",
                    limit=limit,
                    offset=offset,
                    filters=vectordb_filter,
                    output_fields=output_fields,
                )
            else:
                # Use search_by_random for pure filtering
                result = coll.search_by_random(
                    index_name=self.DEFAULT_INDEX_NAME,
                    limit=limit,
                    offset=offset,
                    filters=vectordb_filter,
                    output_fields=output_fields,
                )

            # Convert results
            records = []
            for item in result.data:
                record = dict(item.fields) if item.fields else {}
                record["id"] = item.id
                records.append(record)

            return records

        except Exception as e:
            logger.error(f"Error filtering collection '{collection}': {e}")
            import traceback

            traceback.print_exc()
            return []

    async def scroll(
        self,
        collection: str,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Scroll through large result sets efficiently."""
        # vectordb doesn't natively support scroll, so we simulate it
        offset = int(cursor) if cursor else 0

        records = await self.filter(
            collection=collection,
            filter=filter or {},
            limit=limit,
            offset=offset,
            output_fields=output_fields,
        )

        # Return next cursor if we got a full batch
        next_cursor = str(offset + limit) if len(records) == limit else None

        return records, next_cursor

    # =========================================================================
    # Aggregation Operations
    # =========================================================================

    async def count(self, collection: str, filter: Optional[Dict[str, Any]] = None) -> int:
        """Count records matching filter."""
        try:
            coll = self._get_collection(collection)
            result = coll.aggregate_data(
                index_name=self.DEFAULT_INDEX_NAME, op="count", filters=filter
            )
            return result.agg.get("_total", 0)
        except Exception as e:
            logger.error(f"Error counting records: {e}")
            return 0

    # =========================================================================
    # Index Operations
    # =========================================================================

    async def create_index(
        self,
        collection: str,
        field: str,
        index_type: str,
        **kwargs,
    ) -> bool:
        """Create an index on a field."""
        try:
            # vectordb manages indexes at collection level
            # Indexes are already created with the collection
            logger.info(f"Index creation requested for field '{field}' (managed by vectordb)")
            return True
        except Exception as e:
            logger.error(f"Error creating index on '{field}': {e}")
            return False

    async def drop_index(self, collection: str, field: str) -> bool:
        """Drop an index on a field."""
        try:
            # vectordb manages indexes internally
            logger.info(f"Index drop requested for field '{field}' (managed by vectordb)")
            return True
        except Exception as e:
            logger.error(f"Error dropping index on '{field}': {e}")
            return False

    # =========================================================================
    # Lifecycle Operations
    # =========================================================================

    async def clear(self, collection: str) -> bool:
        """Clear all data in a collection."""
        coll = self._get_collection(collection)

        try:
            coll.delete_all_data()
            logger.info(f"Cleared all data in collection: {collection}")
            return True
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            return False

    async def optimize(self, collection: str) -> bool:
        """Optimize collection for better performance."""
        try:
            # vectordb handles optimization internally via index rebuilding
            logger.info(f"Optimization requested for collection: {collection}")
            return True
        except Exception as e:
            logger.error(f"Error optimizing collection: {e}")
            return False

    async def close(self) -> None:
        """Close storage connection and release resources."""
        try:
            if self.project:
                self.project.close()

            self._collection_configs.clear()
            logger.info("VikingDB backend closed")
        except Exception as e:
            logger.error(f"Error closing VikingDB backend: {e}")

    # =========================================================================
    # Health & Status
    # =========================================================================

    async def health_check(self) -> bool:
        """Check if storage backend is healthy and accessible."""
        try:
            # Simple check: verify we can access the project
            self.project.list_collections()
            return True
        except Exception:
            return False

    async def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            collections = self.project.list_collections()

            # Count total records across all collections using aggregate_data
            total_records = 0
            for collection_name in collections:
                try:
                    coll = self._get_collection(collection_name)
                    result = coll.aggregate_data(
                        index_name=self.DEFAULT_INDEX_NAME, op="count", filters=None
                    )
                    total_records += result.agg.get("_total", 0)
                except Exception as e:
                    logger.warning(f"Error counting records in collection '{collection_name}': {e}")
                    continue

            return {
                "collections": len(collections),
                "total_records": total_records,
                "backend": "vikingdb",
                "mode": self._mode,
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {
                "collections": 0,
                "total_records": 0,
                "backend": "vikingdb",
                "error": str(e),
            }

    @property
    def mode(self) -> str:
        """Return the current storage mode."""
        return self._mode
