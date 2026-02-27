# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""VikingDB storage backend for OpenViking."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, FilterExpr, In, Or, RawDSL
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.utils.logging_init import init_cpp_logging
from openviking.storage.vectordb_adapters import CollectionAdapter, create_collection_adapter
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class VikingVectorIndexBackend:
    """Single-collection vector backend with adapter-based backend specialization."""

    DEFAULT_INDEX_NAME = "default"
    ALLOWED_CONTEXT_TYPES = {"resource", "skill", "memory"}

    def __init__(self, config: Optional[VectorDBBackendConfig]):
        if config is None:
            raise ValueError("VectorDB backend config is required")

        init_cpp_logging()

        self.vector_dim = config.dimension
        self.distance_metric = config.distance_metric
        self.sparse_weight = config.sparse_weight
        self._collection_name = config.name or "context"

        self._adapter: CollectionAdapter = create_collection_adapter(config)
        self._mode = self._adapter.mode

        logger.info(
            "VikingDB backend initialized via adapter %s (mode=%s)",
            type(self._adapter).__name__,
            self._mode,
        )

        self._collection_config: Dict[str, Any] = {}
        self._meta_data_cache: Dict[str, Any] = {}

    @property
    def collection_name(self) -> str:
        return self._collection_name

    def _get_collection(self) -> Collection:
        return self._adapter.get_collection()

    def _get_meta_data(self, coll: Collection) -> Dict[str, Any]:
        if not self._meta_data_cache:
            self._meta_data_cache = coll.get_meta_data() or {}
        return self._meta_data_cache

    def _refresh_meta_data(self, coll: Collection) -> None:
        self._meta_data_cache = coll.get_meta_data() or {}

    def _filter_known_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            coll = self._get_collection()
            fields = self._get_meta_data(coll).get("Fields", [])
            allowed = {item.get("FieldName") for item in fields}
            return {k: v for k, v in data.items() if k in allowed and v is not None}
        except Exception:
            return data

    # =========================================================================
    # Collection Management (single collection)
    # =========================================================================

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        try:
            collection_meta = dict(schema)

            # Track vector dim from schema for info.
            vector_dim = self.vector_dim
            for field in collection_meta.get("Fields", []):
                if field.get("FieldType") == "vector":
                    vector_dim = field.get("Dim", self.vector_dim)
                    break

            created = self._adapter.create_collection(
                name=name,
                schema=collection_meta,
                distance=self.distance_metric,
                sparse_weight=self.sparse_weight,
                index_name=self.DEFAULT_INDEX_NAME,
            )
            if not created:
                return False

            self._collection_name = name
            self._collection_config = {
                "vector_dim": vector_dim,
                "distance": self.distance_metric,
                "schema": schema,
            }
            self._refresh_meta_data(self._get_collection())
            logger.info("Created VikingDB collection: %s (dim=%s)", name, vector_dim)
            return True
        except Exception as e:
            logger.error("Error creating collection %s: %s", name, e)
            return False

    async def drop_collection(self) -> bool:
        try:
            dropped = self._adapter.drop_collection()
            if dropped:
                self._collection_config = {}
                self._meta_data_cache = {}
            return dropped
        except Exception as e:
            logger.error("Error dropping collection %s: %s", self._collection_name, e)
            return False

    async def collection_exists(self) -> bool:
        return self._adapter.collection_exists()

    async def get_collection_info(self) -> Optional[Dict[str, Any]]:
        if not await self.collection_exists():
            return None
        config = self._collection_config
        return {
            "name": self._collection_name,
            "vector_dim": config.get("vector_dim", self.vector_dim),
            "count": await self.count(),
            "status": "active",
        }

    async def collection_exists_bound(self) -> bool:
        return await self.collection_exists()

    # =========================================================================
    # Data Operations
    # =========================================================================

    async def upsert(self, data: Dict[str, Any]) -> str:
        payload = dict(data)
        context_type = payload.get("context_type")
        if context_type and context_type not in self.ALLOWED_CONTEXT_TYPES:
            logger.warning(
                "Invalid context_type: %s. Must be one of %s",
                context_type,
                sorted(self.ALLOWED_CONTEXT_TYPES),
            )
            return ""

        if not payload.get("id"):
            payload["id"] = str(uuid.uuid4())

        payload = self._filter_known_fields(payload)
        ids = self._adapter.upsert(payload)
        return ids[0] if ids else ""

    async def get(self, ids: List[str]) -> List[Dict[str, Any]]:
        try:
            return self._adapter.get(ids)
        except Exception as e:
            logger.error("Error getting records: %s", e)
            return []

    async def delete(self, ids: List[str]) -> int:
        try:
            return self._adapter.delete(ids=ids)
        except Exception as e:
            logger.error("Error deleting records: %s", e)
            return 0

    async def exists(self, id: str) -> bool:
        try:
            return len(await self.get([id])) > 0
        except Exception:
            return False

    async def fetch_by_uri(self, uri: str) -> Optional[Dict[str, Any]]:
        try:
            records = await self.query(
                filter={"op": "must", "field": "uri", "conds": [uri]},
                limit=2,
            )
            if len(records) == 1:
                return records[0]
            return None
        except Exception as e:
            logger.error("Error fetching record by URI %s: %s", uri, e)
            return None

    async def query(
        self,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        with_vector: bool = False,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        try:
            return self._adapter.query(
                query_vector=query_vector,
                sparse_query_vector=sparse_query_vector,
                filter=filter,
                limit=limit,
                offset=offset,
                output_fields=output_fields,
                with_vector=with_vector,
                order_by=order_by,
                order_desc=order_desc,
            )
        except Exception as e:
            logger.error("Error querying collection %s: %s", self._collection_name, e)
            return []

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
        # Backward-compatible alias for internal call sites.
        return await self.query(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            with_vector=with_vector,
        )

    async def filter(
        self,
        filter: Dict[str, Any] | FilterExpr,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        return await self.query(
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            order_by=order_by,
            order_desc=order_desc,
        )

    async def remove_by_uri(self, uri: str) -> int:
        try:
            target_records = await self.filter(
                {"op": "must", "field": "uri", "conds": [uri]},
                limit=10,
            )
            if not target_records:
                return 0

            total_deleted = 0
            if any(r.get("level") in [0, 1] for r in target_records):
                total_deleted += await self._remove_descendants(parent_uri=uri)

            ids = [r.get("id") for r in target_records if r.get("id")]
            if ids:
                total_deleted += await self.delete(ids)
            return total_deleted
        except Exception as e:
            logger.error("Error removing URI %s: %s", uri, e)
            return 0

    async def _remove_descendants(self, parent_uri: str) -> int:
        total_deleted = 0
        children = await self.filter(
            {"op": "must", "field": "parent_uri", "conds": [parent_uri]},
            limit=100000,
        )
        for child in children:
            child_uri = child.get("uri")
            level = child.get("level", 2)
            if level in [0, 1] and child_uri:
                total_deleted += await self._remove_descendants(parent_uri=child_uri)
            child_id = child.get("id")
            if child_id:
                await self.delete([child_id])
                total_deleted += 1
        return total_deleted

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
        conds: List[FilterExpr] = [Eq("uri", uri), Eq("account_id", account_id)]
        if owner_space:
            conds.append(Eq("owner_space", owner_space))
        return await self.filter(filter=And(conds), limit=limit)

    async def delete_account_data(self, account_id: str) -> int:
        return self._adapter.delete(filter=Eq("account_id", account_id))

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
            self._adapter.delete(filter=And(conds))

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
        updated = {**records[0], "uri": new_uri, "parent_uri": new_parent_uri}
        return bool(await self.upsert(updated))

    async def increment_active_count(self, ctx: RequestContext, uris: List[str]) -> int:
        updated = 0
        for uri in uris:
            records = await self.get_context_by_uri(account_id=ctx.account_id, uri=uri, limit=1)
            if not records:
                continue
            record = records[0]
            current = int(record.get("active_count", 0) or 0)
            record["active_count"] = current + 1
            if await self.upsert(record):
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
        offset = int(cursor) if cursor else 0
        records = await self.filter(
            filter=filter or {},
            limit=limit,
            offset=offset,
            output_fields=output_fields,
        )
        next_cursor = str(offset + limit) if len(records) == limit else None
        return records, next_cursor

    async def count(self, filter: Optional[Dict[str, Any] | FilterExpr] = None) -> int:
        try:
            return self._adapter.count(filter=filter)
        except Exception as e:
            logger.error("Error counting records: %s", e)
            return 0

    async def clear(self) -> bool:
        try:
            return self._adapter.clear()
        except Exception as e:
            logger.error("Error clearing collection: %s", e)
            return False

    async def optimize(self) -> bool:
        logger.info("Optimization requested for collection: %s", self._collection_name)
        return True

    async def close(self) -> None:
        try:
            self._adapter.close()
            self._collection_config = {}
            self._meta_data_cache = {}
            logger.info("VikingDB backend closed")
        except Exception as e:
            logger.error("Error closing VikingDB backend: %s", e)

    async def health_check(self) -> bool:
        try:
            await self.collection_exists()
            return True
        except Exception:
            return False

    async def get_stats(self) -> Dict[str, Any]:
        try:
            exists = await self.collection_exists()
            total_records = await self.count() if exists else 0
            return {
                "collections": 1 if exists else 0,
                "total_records": total_records,
                "backend": "vikingdb",
                "mode": self._mode,
            }
        except Exception as e:
            logger.error("Error getting stats: %s", e)
            return {
                "collections": 0,
                "total_records": 0,
                "backend": "vikingdb",
                "error": str(e),
            }

    @property
    def mode(self) -> str:
        return self._mode
