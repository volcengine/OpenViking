# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingDB storage backend for OpenViking."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, FilterExpr, In, Or, PathScope, RawDSL
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.utils.logging_init import init_cpp_logging
from openviking.storage.vectordb_adapters import create_collection_adapter
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import DEFAULT_INDEX_NAME, VectorDBBackendConfig

logger = get_logger(__name__)

RETRIEVAL_OUTPUT_FIELDS = [
    "uri",
    "level",
    "context_type",
    "abstract",
    "active_count",
    "updated_at",
]

LOOKUP_OUTPUT_FIELDS = [
    "uri",
    "level",
    "active_count",
]

MEMORY_DEDUP_OUTPUT_FIELDS = [
    "uri",
    "abstract",
    "context_type",
    "created_at",
    "updated_at",
    "active_count",
    "level",
    "account_id",
    "owner_space",
]

FETCH_BY_URI_OUTPUT_FIELDS = [
    "uri",
    "type",
    "context_type",
    "created_at",
    "updated_at",
    "active_count",
    "level",
    "name",
    "description",
    "tags",
    "abstract",
    "account_id",
    "owner_space",
]

URI_REWRITE_OUTPUT_FIELDS = [
    "uri",
    "type",
    "context_type",
    "vector",
    "sparse_vector",
    "created_at",
    "updated_at",
    "active_count",
    "level",
    "name",
    "description",
    "tags",
    "abstract",
    "account_id",
    "owner_space",
]


class _SingleAccountBackend:
    """绑定单个 account 的后端实现（内部类）"""

    def __init__(
        self,
        config: VectorDBBackendConfig,
        bound_account_id: Optional[str],
        shared_adapter=None,
    ):
        """
        初始化单 account 后端。

        Args:
            config: VectorDB 配置
            bound_account_id: 绑定的 account_id，None 表示 root 特权模式
            shared_adapter: Optional pre-created adapter to share across backends.
                If provided, reuses the existing adapter (and its underlying
                PersistStore) instead of creating a new one. This avoids
                RocksDB LOCK contention when multiple account backends point
                to the same storage path.
        """
        self._bound_account_id = bound_account_id
        self._adapter = shared_adapter or create_collection_adapter(config)
        self._collection_config: Dict[str, Any] = {}
        self._meta_data_cache: Dict[str, Any] = {}
        self._mode = self._adapter.mode
        self._distance_metric = "cosine"
        self._sparse_weight = 0.0
        self._collection_name = "context"
        self._index_name = config.index_name or DEFAULT_INDEX_NAME

        logger.info(
            "_SingleAccountBackend initialized (bound_account_id=%s, mode=%s)",
            bound_account_id,
            self._mode,
        )

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

    def _prepare_upsert_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Drop runtime-only or stale legacy fields before writing back to the current schema."""
        payload = {k: v for k, v in data.items() if v is not None}
        filtered = self._filter_known_fields(payload)
        return {k: v for k, v in filtered.items() if v is not None}

    # =========================================================================
    # Collection Management
    # =========================================================================

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        try:
            collection_meta = dict(schema)
            vector_dim = None
            for field in collection_meta.get("Fields", []):
                if field.get("FieldType") == "vector":
                    vector_dim = field.get("Dim")
                    break

            created = self._adapter.create_collection(
                name=name,
                schema=collection_meta,
                distance=self._distance_metric,
                sparse_weight=self._sparse_weight,
                index_name=self._index_name,
            )
            if not created:
                return False

            self._collection_config = {
                "vector_dim": vector_dim,
                "distance": self._distance_metric,
                "schema": schema,
            }
            self._refresh_meta_data(self._get_collection())
            logger.info("Created collection: %s", name)
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
            logger.error("Error dropping collection: %s", e)
            return False

    async def collection_exists(self) -> bool:
        return self._adapter.collection_exists()

    async def get_collection_info(self) -> Optional[Dict[str, Any]]:
        if not await self.collection_exists():
            return None
        config = self._collection_config
        return {
            "name": self._collection_name,
            "vector_dim": config.get("vector_dim"),
            "count": await self.count(),
            "status": "active",
        }

    # =========================================================================
    # Data Operations (with tenant enforcement)
    # =========================================================================

    async def upsert(self, data: Dict[str, Any]) -> str:
        payload = dict(data)
        logger.debug(
            f"[_SingleAccountBackend.upsert] Input data.account_id={payload.get('account_id')}, bound_account_id={self._bound_account_id}"
        )

        if self._bound_account_id and not payload.get("account_id"):
            payload["account_id"] = self._bound_account_id
        logger.debug(
            f"[_SingleAccountBackend.upsert] Final payload.account_id={payload.get('account_id')}"
        )

        context_type = payload.get("context_type")
        if context_type and context_type not in VikingVectorIndexBackend.ALLOWED_CONTEXT_TYPES:
            logger.warning(
                "Invalid context_type: %s. Must be one of %s",
                context_type,
                sorted(VikingVectorIndexBackend.ALLOWED_CONTEXT_TYPES),
            )
            return ""

        if not payload.get("id"):
            payload["id"] = str(uuid.uuid4())

        payload = self._prepare_upsert_payload(payload)
        ids = self._adapter.upsert(payload)
        return ids[0] if ids else ""

    async def get(self, ids: List[str]) -> List[Dict[str, Any]]:
        try:
            records = self._adapter.get(ids)
            if self._bound_account_id:
                records = [r for r in records if r.get("account_id") == self._bound_account_id]
            return records
        except Exception as e:
            logger.error("Error getting records: %s", e)
            return []

    async def delete(self, ids: List[str]) -> int:
        try:
            if self._bound_account_id:
                records = await self.get(ids)
                valid_ids = [r["id"] for r in records if r.get("id")]
                if len(valid_ids) != len(ids):
                    logger.warning("Attempted to delete records outside bound account")
                ids = valid_ids

            return self._adapter.delete(ids=ids)
        except Exception as e:
            logger.error("Error deleting records: %s", e)
            return 0

    async def delete_by_filter(self, filter: FilterExpr) -> int:
        """Root-only: 直接通过 filter 删除"""
        try:
            return self._adapter.delete(filter=filter)
        except Exception as e:
            logger.error("Error deleting by filter: %s", e)
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
                output_fields=FETCH_BY_URI_OUTPUT_FIELDS,
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
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        try:
            logger.debug(
                f"[_SingleAccountBackend.query] Called with bound_account_id={self._bound_account_id}, filter={filter}"
            )
            if self._bound_account_id:
                account_filter = Eq("account_id", self._bound_account_id)
                if filter:
                    if isinstance(filter, dict):
                        filter = RawDSL(filter)
                    filter = And([account_filter, filter])
                else:
                    filter = account_filter
                logger.debug(
                    f"[_SingleAccountBackend.query] Applied account filter, final filter={filter}"
                )

            return self._adapter.query(
                query_vector=query_vector,
                sparse_query_vector=sparse_query_vector,
                filter=filter,
                limit=limit,
                offset=offset,
                output_fields=output_fields,
                order_by=order_by,
                order_desc=order_desc,
            )
        except Exception as e:
            logger.error("Error querying collection: %s", e)
            return []

    async def search(
        self,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return await self.query(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
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
                output_fields=LOOKUP_OUTPUT_FIELDS,
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
            PathScope("uri", parent_uri, depth=1),
            limit=100000,
            output_fields=LOOKUP_OUTPUT_FIELDS,
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
            if self._bound_account_id:
                account_filter = Eq("account_id", self._bound_account_id)
                if filter:
                    if isinstance(filter, dict):
                        filter = RawDSL(filter)
                    filter = And([account_filter, filter])
                else:
                    filter = account_filter

            return self._adapter.count(filter=filter)
        except Exception as e:
            logger.error("Error counting records: %s", e)
            return 0

    async def clear(self) -> bool:
        try:
            if self._bound_account_id:
                return await self.delete_by_filter(Eq("account_id", self._bound_account_id)) > 0
            return self._adapter.clear()
        except Exception as e:
            logger.error("Error clearing collection: %s", e)
            return False

    async def optimize(self) -> bool:
        logger.info("Optimization requested")
        return True

    async def close(self) -> None:
        try:
            self._adapter.close()
            self._collection_config = {}
            self._meta_data_cache = {}
            logger.info("_SingleAccountBackend closed")
        except Exception as e:
            logger.error("Error closing backend: %s", e)

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
                "bound_account_id": self._bound_account_id,
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
    def is_closing(self) -> bool:
        return False


class VikingVectorIndexBackend:
    """单例门面，管理 per-account 后端实例"""

    ALLOWED_CONTEXT_TYPES = {"resource", "skill", "memory"}

    def __init__(self, config: Optional[VectorDBBackendConfig]):
        if config is None:
            raise ValueError("VectorDB backend config is required")

        init_cpp_logging()

        self._config = config
        self.vector_dim = config.dimension
        self.distance_metric = config.distance_metric
        self.sparse_weight = config.sparse_weight
        self._collection_name = config.name or "context"
        self._index_name = config.index_name or DEFAULT_INDEX_NAME

        self._account_backends: Dict[str, _SingleAccountBackend] = {}
        self._root_backend: Optional[_SingleAccountBackend] = None
        # Share a single adapter (and its underlying PersistStore/RocksDB instance)
        # across all account backends to avoid LOCK contention.
        self._shared_adapter = create_collection_adapter(config)

        logger.info(
            "VikingVectorIndexBackend facade initialized",
        )

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def mode(self) -> str:
        return self._get_default_backend()._mode

    # =========================================================================
    # 内部辅助方法
    # =========================================================================

    def _get_default_backend(self) -> _SingleAccountBackend:
        """获取默认 backend（用于 collection 管理等操作）"""
        return self._get_backend_for_account("default")

    def _get_backend_for_account(self, account_id: str) -> _SingleAccountBackend:
        """获取指定 account 的 backend，懒创建"""
        if account_id not in self._account_backends:
            backend = _SingleAccountBackend(
                self._config, bound_account_id=account_id, shared_adapter=self._shared_adapter
            )
            backend._distance_metric = self.distance_metric
            backend._sparse_weight = self.sparse_weight
            backend._collection_name = self._collection_name
            backend._index_name = self._index_name
            self._account_backends[account_id] = backend
        return self._account_backends[account_id]

    def _get_backend_for_context(self, ctx: RequestContext) -> _SingleAccountBackend:
        """根据上下文获取 backend"""
        return self._get_backend_for_account(ctx.account_id)

    def _get_root_backend(self) -> _SingleAccountBackend:
        """获取 root 特权 backend"""
        if not self._root_backend:
            self._root_backend = _SingleAccountBackend(
                self._config, bound_account_id=None, shared_adapter=self._shared_adapter
            )
            self._root_backend._distance_metric = self.distance_metric
            self._root_backend._sparse_weight = self.sparse_weight
            self._root_backend._collection_name = self._collection_name
            self._root_backend._index_name = self._index_name
        return self._root_backend

    def _check_root_role(self, ctx: RequestContext) -> None:
        """校验是否为 root 角色"""
        if ctx.role != Role.ROOT:
            raise PermissionError(f"Root role required, got {ctx.role}")

    # =========================================================================
    # Collection Management（委托给默认 backend）
    # =========================================================================

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        return await self._get_default_backend().create_collection(name, schema)

    async def drop_collection(self) -> bool:
        return await self._get_default_backend().drop_collection()

    async def collection_exists(self) -> bool:
        return await self._get_default_backend().collection_exists()

    async def collection_exists_bound(self) -> bool:
        return await self.collection_exists()

    async def get_collection_info(self) -> Optional[Dict[str, Any]]:
        return await self._get_default_backend().get_collection_info()

    # =========================================================================
    # 公开数据操作 API（强制要求 ctx）
    # =========================================================================

    async def upsert(self, data: Dict[str, Any], *, ctx: RequestContext) -> str:
        logger.debug(
            f"[VikingVectorIndexBackend.upsert] Called with ctx.account_id={ctx.account_id}, data={data}"
        )
        backend = self._get_backend_for_context(ctx)
        logger.debug(
            f"[VikingVectorIndexBackend.upsert] Using backend for account_id={ctx.account_id}"
        )
        result = await backend.upsert(data)
        logger.debug(f"[VikingVectorIndexBackend.upsert] Completed, result={result}")
        return result

    async def get(self, ids: List[str], *, ctx: RequestContext) -> List[Dict[str, Any]]:
        backend = self._get_backend_for_context(ctx)
        return await backend.get(ids)

    async def delete(self, ids: List[str], *, ctx: RequestContext) -> int:
        backend = self._get_backend_for_context(ctx)
        return await backend.delete(ids)

    async def exists(self, id: str, *, ctx: RequestContext) -> bool:
        backend = self._get_backend_for_context(ctx)
        return await backend.exists(id)

    async def fetch_by_uri(self, uri: str, *, ctx: RequestContext) -> Optional[Dict[str, Any]]:
        backend = self._get_backend_for_context(ctx)
        return await backend.fetch_by_uri(uri)

    async def query(
        self,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
        *,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        backend = self._get_backend_for_context(ctx)
        return await backend.query(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            order_by=order_by,
            order_desc=order_desc,
        )

    async def search(
        self,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        *,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        return await self.query(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            ctx=ctx,
        )

    async def filter(
        self,
        filter: Dict[str, Any] | FilterExpr,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
        *,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        return await self.query(
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            order_by=order_by,
            order_desc=order_desc,
            ctx=ctx,
        )

    async def remove_by_uri(self, uri: str, *, ctx: RequestContext) -> int:
        backend = self._get_backend_for_context(ctx)
        return await backend.remove_by_uri(uri)

    async def scroll(
        self,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
        *,
        ctx: RequestContext,
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        backend = self._get_backend_for_context(ctx)
        return await backend.scroll(
            filter=filter,
            limit=limit,
            cursor=cursor,
            output_fields=output_fields,
        )

    async def count(
        self,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        *,
        ctx: Optional[RequestContext] = None,
    ) -> int:
        if ctx:
            backend = self._get_backend_for_context(ctx)
        else:
            backend = self._get_default_backend()
        return await backend.count(filter=filter)

    async def clear(self, *, ctx: Optional[RequestContext] = None) -> bool:
        if ctx:
            backend = self._get_backend_for_context(ctx)
        else:
            backend = self._get_default_backend()
        return await backend.clear()

    async def optimize(self) -> bool:
        return await self._get_default_backend().optimize()

    async def close(self) -> None:
        try:
            for backend in self._account_backends.values():
                await backend.close()
            if self._root_backend:
                await self._root_backend.close()
            self._account_backends.clear()
            self._root_backend = None
            logger.info("VikingVectorIndexBackend facade closed")
        except Exception as e:
            logger.error("Error closing facade: %s", e)

    async def health_check(self) -> bool:
        return await self._get_default_backend().health_check()

    async def get_stats(self) -> Dict[str, Any]:
        return await self._get_default_backend().get_stats()

    @property
    def is_closing(self) -> bool:
        return False

    @property
    def has_queue_manager(self) -> bool:
        return False

    async def enqueue_embedding_msg(self, _embedding_msg) -> bool:
        raise NotImplementedError("Queue management requires VikingDBManager")

    # =========================================================================
    # Tenant-Aware 方法（保持向后兼容）
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
            output_fields=RETRIEVAL_OUTPUT_FIELDS,
            ctx=ctx,
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
            In("level", [0, 1, 2]),  # TODO: smj fix this
        )
        return await self.search(
            query_vector=query_vector,
            sparse_query_vector=sparse_query_vector,
            filter=merged_filter,
            limit=limit,
            output_fields=RETRIEVAL_OUTPUT_FIELDS,
            ctx=ctx,
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
            PathScope("uri", parent_uri, depth=1),
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
            output_fields=RETRIEVAL_OUTPUT_FIELDS,
            ctx=ctx,
        )

    async def search_similar_memories(
        self,
        owner_space: Optional[str],
        category_uri_prefix: str,
        query_vector: List[float],
        limit: int = 5,
        *,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        conds: List[FilterExpr] = [
            Eq("context_type", "memory"),
            Eq("level", 2),
            Eq("account_id", ctx.account_id),
        ]
        if owner_space:
            conds.append(Eq("owner_space", owner_space))
        if category_uri_prefix:
            conds.append(In("uri", [category_uri_prefix]))

        backend = self._get_backend_for_context(ctx)
        return await backend.search(
            query_vector=query_vector,
            filter=And(conds),
            limit=limit,
            output_fields=MEMORY_DEDUP_OUTPUT_FIELDS,
        )

    async def get_context_by_uri(
        self,
        uri: str,
        owner_space: Optional[str] = None,
        level: Optional[int] = None,
        limit: int = 1,
        *,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        conds: List[FilterExpr] = [PathScope("uri", uri, depth=0), Eq("account_id", ctx.account_id)]
        if owner_space:
            conds.append(Eq("owner_space", owner_space))
        if level is not None:
            conds.append(Eq("level", level))

        backend = self._get_backend_for_context(ctx)
        return await backend.filter(
            filter=And(conds),
            limit=limit,
            output_fields=LOOKUP_OUTPUT_FIELDS,
        )

    async def delete_account_data(self, account_id: str, *, ctx: RequestContext) -> int:
        """删除指定 account 的所有数据（仅限，root 角色操作）"""
        self._check_root_role(ctx)
        root_backend = self._get_root_backend()
        return await root_backend.delete_by_filter(Eq("account_id", account_id))

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

            backend = self._get_backend_for_context(ctx)
            await backend.delete_by_filter(And(conds))

    async def update_uri_mapping(
        self,
        ctx: RequestContext,
        uri: str,
        new_uri: str,
        levels: Optional[List[int]] = None,
    ) -> bool:
        import hashlib

        conds: List[FilterExpr] = [Eq("uri", uri), Eq("account_id", ctx.account_id)]
        if levels:
            conds.append(In("level", levels))
        if ctx.role == Role.USER and uri.startswith(("viking://user/", "viking://agent/")):
            owner_space = (
                ctx.user.user_space_name()
                if uri.startswith("viking://user/")
                else ctx.user.agent_space_name()
            )
            conds.append(Eq("owner_space", owner_space))

        records = await self.filter(
            filter=And(conds),
            limit=100,
            output_fields=URI_REWRITE_OUTPUT_FIELDS,
            ctx=ctx,
        )
        if not records:
            return False

        def _seed_uri_for_id(uri: str, level: int) -> str:
            if level == 0:
                return uri if uri.endswith("/.abstract.md") else f"{uri}/.abstract.md"
            if level == 1:
                return uri if uri.endswith("/.overview.md") else f"{uri}/.overview.md"
            return uri

        success = False
        ids_to_delete: List[str] = []
        for record in records:
            if "id" not in record:
                continue
            raw_level = record.get("level", 2)
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                level = 2

            seed_uri = _seed_uri_for_id(new_uri, level)
            id_seed = f"{ctx.account_id}:{seed_uri}"
            new_id = hashlib.md5(id_seed.encode("utf-8")).hexdigest()

            updated = {
                **record,
                "id": new_id,
                "uri": new_uri,
            }
            if await self.upsert(updated, ctx=ctx):
                success = True
                old_id = record.get("id")
                if old_id and old_id != new_id:
                    ids_to_delete.append(old_id)

        if ids_to_delete:
            await self.delete(list(set(ids_to_delete)), ctx=ctx)

        return success

    async def increment_active_count(self, ctx: RequestContext, uris: List[str]) -> int:
        updated = 0
        for uri in uris:
            records = await self.get_context_by_uri(uri=uri, limit=100, ctx=ctx)
            if not records:
                continue
            record_ids = [r["id"] for r in records if r.get("id")]
            if not record_ids:
                continue
            # Re-fetch by ID to get full records including vectors
            full_records = await self.get(record_ids, ctx=ctx)
            uri_updated = False
            for record in full_records:
                current = int(record.get("active_count", 0) or 0)
                record["active_count"] = current + 1
                if await self.upsert(record, ctx=ctx):
                    uri_updated = True
            if uri_updated:
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
            uri_conds = [
                PathScope("uri", target_dir, depth=-1)
                for target_dir in target_directories
                if target_dir
            ]
            if uri_conds:
                filters.append(Or(uri_conds))

        if extra_filter:
            if isinstance(extra_filter, dict):
                filters.append(RawDSL(extra_filter))
            else:
                filters.append(extra_filter)

        merged = self._merge_filters(*filters)
        return merged

    @staticmethod
    def _tenant_filter(
        ctx: RequestContext, context_type: Optional[str] = None
    ) -> Optional[FilterExpr]:
        if ctx.role == Role.ROOT:
            return None

        user_spaces = [ctx.user.user_space_name(), ctx.user.agent_space_name()]
        resource_spaces = [*user_spaces, ""]
        account_filter = Eq("account_id", ctx.account_id)

        if context_type == "resource":
            return And([account_filter, In("owner_space", resource_spaces)])
        if context_type in {"memory", "skill"}:
            return And([account_filter, In("owner_space", user_spaces)])

        return And(
            [
                account_filter,
                Or(
                    [
                        And([Eq("context_type", "resource"), In("owner_space", resource_spaces)]),
                        And(
                            [
                                In("context_type", ["memory", "skill"]),
                                In("owner_space", user_spaces),
                            ]
                        ),
                    ]
                ),
            ]
        )

    @staticmethod
    def _merge_filters(*filters: Optional[FilterExpr]) -> Optional[FilterExpr]:
        non_empty = [
            f
            for f in filters
            if f
            and not (
                isinstance(f, RawDSL)
                and f.payload.get("op") == "and"
                and not f.payload.get("conds")
            )
        ]
        if not non_empty:
            return None
        if len(non_empty) == 1:
            return non_empty[0]
        return And(non_empty)
