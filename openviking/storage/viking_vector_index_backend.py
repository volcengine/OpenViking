# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
VikingDB storage backend for OpenViking.

Supports both in-memory and local persistent storage modes.
"""

import uuid
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.vector_store import FilterExpr, create_driver
from openviking.storage.vector_store.expr import And, Eq, In, Or, RawDSL
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import FetchDataInCollectionResult
from openviking.storage.vectordb.utils.logging_init import init_cpp_logging
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class VikingVectorIndexBackend:
    """
    VikingDB storage backend implementation.

    Features:
    - Vector similarity search with BruteForce indexing
    - Scalar filtering with support for multiple operators
    - Support for local persistent storage, HTTP service, and Volcengine VikingDB
    - Auto-managed indexes per collection

    VikingDBManager is derived by VikingVectorIndexBackend.
    """

    # Default index name
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
        if config is None:
            raise ValueError("VectorDB backend config is required")

        init_cpp_logging()

        self.vector_dim = config.dimension
        self.distance_metric = config.distance_metric
        self.sparse_weight = config.sparse_weight
        self._collection_name = config.name or "context"

        # Backend selection is delegated to static driver registry.
        self._driver = create_driver(config)
        self._mode = self._driver.mode

        logger.info(
            "VikingDB backend initialized via driver '%s' (mode=%s)",
            type(self._driver).__name__,
            self._mode,
        )

        self._collection_configs: Dict[str, Dict[str, Any]] = {}
        # Cache meta_data at collection level to avoid repeated remote calls
        self._meta_data_cache: Dict[str, Dict[str, Any]] = {}

    def _compile_filter(self, filter_expr: Optional[FilterExpr | Dict[str, Any]]) -> Dict[str, Any]:
        """Compile AST filters via driver; allow raw DSL passthrough."""
        if filter_expr is None:
            return {}
        if isinstance(filter_expr, dict):
            return filter_expr
        if isinstance(filter_expr, RawDSL):
            return filter_expr.payload
        return self._driver.compile_expr(filter_expr)

    def _get_collection(self, name: str) -> Collection:
        """Get collection object or raise error if not found."""
        if not self._driver.has_collection(name):
            raise CollectionNotFoundError(f"Collection '{name}' does not exist")
        return self._driver.get_collection(name)

    def _get_meta_data(self, collection_name: str, coll: Collection) -> Dict[str, Any]:
        """Get meta_data with collection-level caching to avoid repeated remote calls."""
        if collection_name not in self._meta_data_cache:
            self._meta_data_cache[collection_name] = coll.get_meta_data()
        return self._meta_data_cache[collection_name]

    def _update_meta_data_cache(self, collection_name: str, coll: Collection):
        """Update the cached meta_data after modifications."""
        meta_data = coll.get_meta_data()
        self._meta_data_cache[collection_name] = meta_data

    @property
    def collection_name(self) -> str:
        """Return bound collection name for this store instance."""
        return self._collection_name

    def _resolve_collection_name(self, collection_name: Optional[str] = None) -> str:
        """Resolve collection name with bound default."""
        return collection_name or self._collection_name

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
            if self._driver.has_collection(name):
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

            # Create collection via backend-specific collection driver
            collection = self._driver.create_collection(name, collection_meta)

            scalar_index_fields = self._driver.sanitize_scalar_index_fields(
                scalar_index_fields=scalar_index_fields,
                fields_meta=collection_meta.get("Fields", []),
            )

            # Create default index for the collection
            use_sparse = self.sparse_weight > 0.0
            index_meta = self._driver.build_default_index_meta(
                index_name=self.DEFAULT_INDEX_NAME,
                distance=distance,
                use_sparse=use_sparse,
                sparse_weight=self.sparse_weight,
                scalar_index_fields=scalar_index_fields,
            )

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
            self._collection_name = name

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
            if not self._driver.has_collection(name):
                logger.warning(f"Collection '{name}' does not exist")
                return False

            self._driver.drop_collection(name)
            self._collection_configs.pop(name, None)
            # Clear cached meta_data when dropping collection
            self._meta_data_cache.pop(name, None)

            logger.info(f"Dropped collection: {name}")
            return True
        except Exception as e:
            logger.error(f"Error dropping collection '{name}': {e}")
            return False

    async def collection_exists(self, name: Optional[str] = None) -> bool:
        """Check if a collection exists."""
        return self._driver.has_collection(self._resolve_collection_name(name))

    async def list_collections(self) -> List[str]:
        """List all collection names."""
        return self._driver.list_collections()

    async def get_collection_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get collection metadata and statistics."""
        try:
            if not self._driver.has_collection(name):
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

    async def collection_exists_bound(self) -> bool:
        """Check whether the bound collection exists."""
        return await self.collection_exists(self._collection_name)

    # =========================================================================
    # CRUD Operations - Single Record
    # =========================================================================

    async def insert(self, data: Dict[str, Any]) -> str:
        """Insert a single record into the bound collection."""
        coll = self._get_collection(self._collection_name)

        # Ensure ID exists
        record_id = data.get("id")
        if not record_id:
            record_id = str(uuid.uuid4())
            data = {**data, "id": record_id}

        # Validate context_type for context collection
        context_type = data.get("context_type")
        if context_type not in ["resource", "skill", "memory"]:
            logger.warning(
                f"Invalid context_type: {context_type}. "
                f"Must be one of ['resource', 'skill', 'memory'], Ignore"
            )
            return ""

        fields = self._get_meta_data(self._collection_name, coll).get("Fields", [])
        fields_dict = {item["FieldName"]: item for item in fields}
        new_data = {}
        for key in data:
            if key in fields_dict and data[key] is not None:
                new_data[key] = data[key]

        try:
            coll.upsert_data([new_data])
            return record_id
        except Exception as e:
            logger.error(f"Error inserting record: {e}")
            raise

    async def update(self, id: str, data: Dict[str, Any]) -> bool:
        """Update a record by ID in the bound collection."""
        coll = self._get_collection(self._collection_name)

        try:
            # Fetch existing record
            existing = await self.get([id])
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

    async def upsert(self, data: Dict[str, Any]) -> str:
        """Insert or update a record in the bound collection."""
        coll = self._get_collection(self._collection_name)

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

    async def delete(self, ids: List[str]) -> int:
        """Delete records by IDs from the bound collection."""
        coll = self._get_collection(self._collection_name)

        try:
            coll.delete_data(ids)
            return len(ids)
        except Exception as e:
            logger.error(f"Error deleting records: {e}")
            return 0

    async def get(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Get records by IDs from the bound collection."""
        coll = self._get_collection(self._collection_name)

        try:
            result = coll.fetch_data(ids)

            if isinstance(result, FetchDataInCollectionResult):
                records = []
                for item in result.items:
                    record = dict(item.fields) if item.fields else {}
                    record["id"] = item.id
                    self._driver.normalize_record_for_read(record)
                    records.append(record)
                return records
            elif isinstance(result, dict):
                records = []
                if "fetch" in result:
                    for item in result.get("fetch", []):
                        record = dict(item.get("fields", {})) if item.get("fields") else {}
                        record["id"] = item.get("id")
                        if record["id"]:
                            self._driver.normalize_record_for_read(record)
                            records.append(record)
                return records
            else:
                logger.warning(f"Unexpected return type from fetch_data: {type(result)}")
                return []
        except Exception as e:
            logger.error(f"Error getting records: {e}")
            return []

    async def fetch_by_uri(self, uri: str) -> Optional[Dict[str, Any]]:
        """Fetch a record by URI."""
        coll = self._get_collection(self._collection_name)
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
                self._driver.normalize_record_for_read(record)
                records.append(record)
            if len(records) > 1:
                raise ValueError(f"Duplicate records found for URI: {uri}")
            if len(records) == 0:
                raise ValueError(f"Record not found for URI: {uri}")
            return records[0]
        except Exception as e:
            logger.error(f"Error fetching record by URI '{uri}': {e}")
            return None

    async def exists(self, id: str) -> bool:
        """Check if a record exists."""
        try:
            results = await self.get([id])
            return len(results) > 0
        except Exception:
            return False

    # =========================================================================
    # CRUD Operations - Batch
    # =========================================================================

    async def batch_insert(self, data: List[Dict[str, Any]]) -> List[str]:
        """Batch insert multiple records into the bound collection."""
        coll = self._get_collection(self._collection_name)

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

    async def batch_upsert(self, data: List[Dict[str, Any]]) -> List[str]:
        """Batch insert or update multiple records in the bound collection."""
        coll = self._get_collection(self._collection_name)

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

    async def batch_delete(self, filter: Dict[str, Any] | FilterExpr) -> int:
        """Delete records matching filter conditions."""
        try:
            # First, find matching records
            matching_records = await self.filter(filter, limit=10000)

            if not matching_records:
                return 0

            # Extract IDs and delete
            ids = [record["id"] for record in matching_records if "id" in record]
            return await self.delete(ids)
        except Exception as e:
            logger.error(f"Error batch deleting records: {e}")
            return 0

    async def remove_by_uri(self, uri: str) -> int:
        """Remove resource(s) by URI."""
        try:
            target_records = await self.filter(
                {"op": "must", "field": "uri", "conds": [uri]},
                limit=10,
            )

            if not target_records:
                return 0

            total_deleted = 0

            # If any record indicates this URI is a directory node, remove descendants first.
            if any(r.get("level") in [0, 1] for r in target_records):
                descendant_count = await self._remove_descendants(parent_uri=uri)
                total_deleted += descendant_count

            ids = [r.get("id") for r in target_records if r.get("id")]
            if ids:
                total_deleted += await self.delete(ids)

            logger.info(f"Removed {total_deleted} record(s) for URI: {uri}")
            return total_deleted

        except Exception as e:
            logger.error(f"Error removing URI '{uri}': {e}")
            return 0

    async def _remove_descendants(self, parent_uri: str) -> int:
        """Recursively remove all descendants of a parent URI."""
        total_deleted = 0

        # Find direct children
        children = await self.filter(
            {"op": "must", "field": "parent_uri", "conds": [parent_uri]},
            limit=10000,
        )

        for child in children:
            child_uri = child.get("uri")
            level = child.get("level", 2)

            # Recursively delete if child is also an intermediate directory
            if level in [0, 1] and child_uri:
                descendant_count = await self._remove_descendants(parent_uri=child_uri)
                total_deleted += descendant_count

            # Delete the child
            if "id" in child:
                await self.delete([child["id"]])
                total_deleted += 1

        return total_deleted

    # =========================================================================
    # Search Operations
    # =========================================================================

    async def search(
        self,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        with_vector: bool = False,
    ) -> List[Dict[str, Any]]:
        """Hybrid search: vector similarity (dense/sparse/hybrid) + scalar filtering.

        Args:
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
        coll = self._get_collection(self._collection_name)

        try:
            vectordb_filter = self._compile_filter(filter)

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
                    self._driver.normalize_record_for_read(record)

                    if not with_vector:
                        if "vector" in record:
                            record.pop("vector")
                        if "sparse_vector" in record:
                            record.pop("sparse_vector")

                    records.append(record)

                return records
            else:
                # Pure filtering without vector search
                return await self.filter(
                    filter or {},
                    limit=limit,
                    offset=offset,
                    output_fields=output_fields,
                )

        except Exception as e:
            logger.error(f"Error searching collection '{self._collection_name}': {e}")
            import traceback

            traceback.print_exc()
            return []

    async def filter(
        self,
        filter: Dict[str, Any] | FilterExpr,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        """Pure scalar filtering without vector search."""
        coll = self._get_collection(self._collection_name)

        try:
            vectordb_filter = self._compile_filter(filter)

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
                self._driver.normalize_record_for_read(record)
                records.append(record)

            return records

        except Exception as e:
            logger.error(f"Error filtering collection '{self._collection_name}': {e}")
            import traceback

            traceback.print_exc()
            return []

    # =========================================================================
    # Semantic Context Operations (Tenant-Aware)
    # =========================================================================

    async def search_in_tenant(
        self,
        ctx: RequestContext,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter: Optional[FilterExpr | Dict[str, Any]] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        scope_filter = self._build_scope_filter(
            ctx=ctx,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter=extra_filter,
        )
        return await self.search(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=scope_filter,
            limit=limit,
            offset=offset,
        )

    async def search_global_roots_in_tenant(
        self,
        ctx: RequestContext,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter: Optional[FilterExpr | Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        if not query_vector:
            return []

        merged_filter = self._merge_filters(
            self._build_scope_filter(
                ctx=ctx,
                context_type=context_type,
                target_directories=target_directories,
                extra_filter=extra_filter,
            ),
            In("level", [0, 1]),
        )
        return await self.search(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=merged_filter,
            limit=limit,
        )

    async def search_children_in_tenant(
        self,
        ctx: RequestContext,
        parent_uri: str,
        query_vector: Optional[List[float]],
        sparse_query_vector: Optional[Dict[str, float]] = None,
        context_type: Optional[str] = None,
        target_directories: Optional[List[str]] = None,
        extra_filter: Optional[FilterExpr | Dict[str, Any]] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        merged_filter = self._merge_filters(
            Eq("parent_uri", parent_uri),
            self._build_scope_filter(
                ctx=ctx,
                context_type=context_type,
                target_directories=target_directories,
                extra_filter=extra_filter,
            ),
        )
        return await self.search(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=merged_filter,
            limit=limit,
        )

    async def search_similar_memories(
        self,
        account_id: str,
        owner_space: Optional[str],
        category_uri_prefix: str,
        query_vector: List[float],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        conds: List[FilterExpr] = [
            Eq("context_type", "memory"),
            Eq("level", 2),
            Eq("account_id", account_id),
        ]
        if owner_space:
            conds.append(Eq("owner_space", owner_space))
        if category_uri_prefix:
            conds.append(In("uri", [category_uri_prefix]))

        return await self.search(
            query_vector=query_vector,
            filter=And(conds),
            limit=limit,
        )

    async def get_context_by_uri(
        self,
        account_id: str,
        uri: str,
        owner_space: Optional[str] = None,
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        conds: List[FilterExpr] = [
            Eq("uri", uri),
            Eq("account_id", account_id),
        ]
        if owner_space:
            conds.append(Eq("owner_space", owner_space))
        return await self.filter(
            filter=And(conds),
            limit=limit,
        )

    async def delete_account_data(self, account_id: str) -> int:
        return await self.batch_delete(Eq("account_id", account_id))

    async def delete_uris(self, ctx: RequestContext, uris: List[str]) -> None:
        for uri in uris:
            conds: List[FilterExpr] = [
                Eq("account_id", ctx.account_id),
                Or([Eq("uri", uri), In("uri", [f"{uri}/"])]),
            ]
            if ctx.role == Role.USER and uri.startswith(("viking://user/", "viking://agent/")):
                owner_space = (
                    ctx.user.user_space_name()
                    if uri.startswith("viking://user/")
                    else ctx.user.agent_space_name()
                )
                conds.append(Eq("owner_space", owner_space))
            await self.batch_delete(And(conds))

    async def update_uri_mapping(
        self,
        ctx: RequestContext,
        uri: str,
        new_uri: str,
        new_parent_uri: str,
    ) -> bool:
        records = await self.filter(
            filter=And([Eq("uri", uri), Eq("account_id", ctx.account_id)]),
            limit=1,
        )
        if not records or "id" not in records[0]:
            return False
        return await self.update(records[0]["id"], {"uri": new_uri, "parent_uri": new_parent_uri})

    async def increment_active_count(self, ctx: RequestContext, uris: List[str]) -> int:
        updated = 0
        for uri in uris:
            records = await self.get_context_by_uri(account_id=ctx.account_id, uri=uri, limit=1)
            if not records:
                continue
            record = records[0]
            record_id = record.get("id")
            if not record_id:
                continue
            current = int(record.get("active_count", 0) or 0)
            if await self.update(record_id, {"active_count": current + 1}):
                updated += 1
        return updated

    def _build_scope_filter(
        self,
        ctx: RequestContext,
        context_type: Optional[str],
        target_directories: Optional[List[str]],
        extra_filter: Optional[FilterExpr | Dict[str, Any]],
    ) -> Optional[FilterExpr]:
        filters: List[FilterExpr] = []
        if context_type:
            filters.append(Eq("context_type", context_type))

        tenant_filter = self._tenant_filter(ctx, context_type=context_type)
        if tenant_filter:
            filters.append(tenant_filter)

        if target_directories:
            uri_conds = [In("uri", [target_dir]) for target_dir in target_directories if target_dir]
            if uri_conds:
                filters.append(Or(uri_conds))

        if extra_filter:
            if isinstance(extra_filter, dict):
                filters.append(RawDSL(extra_filter))
            else:
                filters.append(extra_filter)

        return self._merge_filters(*filters)

    @staticmethod
    def _tenant_filter(
        ctx: RequestContext, context_type: Optional[str] = None
    ) -> Optional[FilterExpr]:
        if ctx.role == Role.ROOT:
            return None

        owner_spaces = [ctx.user.user_space_name(), ctx.user.agent_space_name()]
        if context_type == "resource":
            owner_spaces.append("")
        return And([Eq("account_id", ctx.account_id), In("owner_space", owner_spaces)])

    @staticmethod
    def _merge_filters(*filters: Optional[FilterExpr]) -> Optional[FilterExpr]:
        non_empty = [f for f in filters if f]
        if not non_empty:
            return None
        if len(non_empty) == 1:
            return non_empty[0]
        return And(non_empty)

    async def scroll(
        self,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Scroll through large result sets efficiently."""
        # vectordb doesn't natively support scroll, so we simulate it
        offset = int(cursor) if cursor else 0

        records = await self.filter(
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

    async def count(
        self,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
    ) -> int:
        """Count records matching filter."""
        try:
            coll = self._get_collection(self._collection_name)
            result = coll.aggregate_data(
                index_name=self.DEFAULT_INDEX_NAME,
                op="count",
                filters=self._compile_filter(filter),
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

    async def drop_index(self, field: str) -> bool:
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

    async def clear(self) -> bool:
        """Clear all data in a collection."""
        coll = self._get_collection(self._collection_name)

        try:
            coll.delete_all_data()
            logger.info(f"Cleared all data in collection: {self._collection_name}")
            return True
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")
            return False

    async def optimize(self) -> bool:
        """Optimize collection for better performance."""
        try:
            # vectordb handles optimization internally via index rebuilding
            logger.info("Optimization requested for collection: %s", self._collection_name)
            return True
        except Exception as e:
            logger.error(f"Error optimizing collection: {e}")
            return False

    async def close(self) -> None:
        """Close storage connection and release resources."""
        try:
            self._driver.close()

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
            # Simple check: verify we can access collections metadata.
            self._driver.list_collections()
            return True
        except Exception:
            return False

    async def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            collections = self._driver.list_collections()

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
