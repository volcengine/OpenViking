# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingDB storage backend for OpenViking."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from openviking.core.namespace import canonicalize_uri, visible_roots
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, FilterExpr, In, Or, PathScope, RawDSL
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import UpdateResult
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
    "search_tags",
]

LOOKUP_OUTPUT_FIELDS = [
    "uri",
    "level",
    "active_count",
]

FETCH_BY_URI_OUTPUT_FIELDS = [
    "id",
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
    "search_tags",
    "abstract",
    "account_id",
    "owner_user_id",
]

URI_REWRITE_OUTPUT_FIELDS = [
    "id",
    "uri",
    "level",
    "name",
    "description",
    "tags",
    "abstract",
    "content",
    "account_id",
]

VIKINGDB_CONTENT_MAX_SIZE = 1024 * 1024


class _AsyncVectorAdapter:
    """Thread-offloaded facade for sync vector adapters."""

    def __init__(self, adapter: Any):
        self._adapter = adapter

    async def call(self, method_name: str, /, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(getattr(self._adapter, method_name), *args, **kwargs)

    async def run(self, func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(func, *args, **kwargs)

    async def collection_meta(self) -> Dict[str, Any]:
        return await asyncio.to_thread(lambda: self._adapter.get_collection().get_meta_data() or {})

    async def update_collection_description(self, description: str) -> None:
        await asyncio.to_thread(
            lambda: self._adapter.get_collection().update(description=description)
        )


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
        self._async_adapter = _AsyncVectorAdapter(self._adapter)
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
        result = {k: v for k, v in filtered.items() if v is not None}

        # Ensure text fields required by the schema are present (even if empty).
        # VikingDB requires all schema-defined fields in upsert data.
        try:
            coll = self._get_collection()
            meta = self._get_meta_data(coll)
            for field in meta.get("Fields", []):
                if field.get("FieldType") == "text" and field.get("FieldName") not in result:
                    result[field["FieldName"]] = ""
        except Exception:
            pass

        content = result.get("content")
        if isinstance(content, (str, bytes)):
            result["content"] = content[:VIKINGDB_CONTENT_MAX_SIZE]

        return result

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "not found" in message or "does not exist" in message

    async def _refresh_meta_data_async(self) -> None:
        self._meta_data_cache = await self._async_adapter.collection_meta()

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

            created = await self._async_adapter.call(
                "create_collection",
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
            await self._refresh_meta_data_async()
            logger.info("Created collection: %s", name)
            return True
        except Exception as e:
            logger.error("Error creating collection %s: %s", name, e)
            return False

    async def drop_collection(self) -> bool:
        try:
            dropped = await self._async_adapter.call("drop_collection")
            if dropped:
                self._collection_config = {}
                self._meta_data_cache = {}
            return dropped
        except Exception as e:
            logger.error("Error dropping collection: %s", e)
            return False

    async def collection_exists(self) -> bool:
        return await self._async_adapter.call("collection_exists")

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

    async def get_collection_meta(self) -> Optional[Dict[str, Any]]:
        if not await self.collection_exists():
            return None
        return await self._async_adapter.collection_meta()

    async def update_collection_description(self, description: str) -> bool:
        if not await self.collection_exists():
            return False
        await self._async_adapter.update_collection_description(description)
        await self._refresh_meta_data_async()
        return True

    # =========================================================================
    # Data Operations (with tenant enforcement)
    # =========================================================================

    async def upsert(self, data: Dict[str, Any], partial_update: bool = False) -> str:
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

        if partial_update:
            try:
                existing_records = await self._async_adapter.call("get", [payload["id"]])
                if self._bound_account_id:
                    existing_records = [
                        record
                        for record in existing_records
                        if record.get("account_id") == self._bound_account_id
                    ]
            except Exception as e:
                logger.error("Error reading existing record before partial update: %s", e)
                return ""

            if existing_records:
                existing = dict(existing_records[0])
                existing.update({k: v for k, v in payload.items() if v is not None})
                payload = existing

        payload = await self._async_adapter.run(self._prepare_upsert_payload, payload)
        ids = await self._async_adapter.call("upsert", payload)
        return ids[0] if ids else ""

    async def update(self, data: Dict[str, Any]) -> UpdateResult:
        """Strict update path. The target record must already exist."""
        try:
            payload = dict(data)
            logger.debug(
                f"[_SingleAccountBackend.update] Input data.account_id={payload.get('account_id')}, bound_account_id={self._bound_account_id}"
            )

            if self._bound_account_id and not payload.get("account_id"):
                payload["account_id"] = self._bound_account_id

            if not payload.get("id"):
                raise ValueError("id is required for update")

            context_type = payload.get("context_type")
            if context_type and context_type not in VikingVectorIndexBackend.ALLOWED_CONTEXT_TYPES:
                allowed = sorted(VikingVectorIndexBackend.ALLOWED_CONTEXT_TYPES)
                raise ValueError(f"Invalid context_type: {context_type}. Must be one of {allowed}")

            payload = await self._async_adapter.run(self._prepare_upsert_payload, payload)
            ids = await self._async_adapter.call("update_data", [payload])
            normalized_ids = [str(item) for item in (ids or []) if item is not None]
            return UpdateResult(
                ok=bool(normalized_ids),
                ids=normalized_ids,
                updated_count=len(normalized_ids),
                error_code=None if normalized_ids else "UPDATE_FAILED",
                error_message=None
                if normalized_ids
                else "update completed without any updated ids",
            )
        except ValueError as e:
            message = str(e)
            error_code = "NOT_FOUND" if "not found" in message.lower() else "INVALID_ARGUMENT"
            return UpdateResult(
                ok=False,
                ids=[],
                updated_count=0,
                error_code=error_code,
                error_message=message,
            )
        except Exception as e:
            logger.error("Error updating record: %s", e)
            return UpdateResult(
                ok=False,
                ids=[],
                updated_count=0,
                error_code="UPDATE_FAILED",
                error_message=str(e),
            )

    async def get(self, ids: List[str]) -> List[Dict[str, Any]]:
        try:
            records = await self._async_adapter.call("get", ids)
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

            return await self._async_adapter.call("delete", ids=ids)
        except Exception as e:
            logger.error("Error deleting records: %s", e)
            return 0

    async def delete_by_filter(self, filter: FilterExpr) -> int:
        """Root-only: 直接通过 filter 删除"""
        try:
            return await self._async_adapter.call("delete", filter=filter)
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

            return await self._async_adapter.call(
                "query",
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
            logger.error("Error querying collection: %s", e, exc_info=True)
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

            return await self._async_adapter.call("count", filter=filter)
        except Exception as e:
            logger.error("Error counting records: %s", e)
            return 0

    async def clear(self) -> bool:
        try:
            if self._bound_account_id:
                return await self.delete_by_filter(Eq("account_id", self._bound_account_id)) > 0
            return await self._async_adapter.call("clear")
        except Exception as e:
            logger.error("Error clearing collection: %s", e)
            return False

    async def optimize(self) -> bool:
        logger.info("Optimization requested")
        return True

    async def search_by_keywords(
        self,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        output_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            if self._bound_account_id:
                account_filter = Eq("account_id", self._bound_account_id)
                if filter:
                    if isinstance(filter, dict):
                        filter = RawDSL(filter)
                    filter = And([account_filter, filter])
                else:
                    filter = account_filter

            return await asyncio.to_thread(
                self._adapter.search_by_keywords,
                keywords=keywords,
                query=query,
                limit=limit,
                offset=offset,
                filter=filter,
                output_fields=output_fields,
            )
        except Exception as e:
            logger.error("Error searching by keywords: %s", e)
            raise

    async def close(self) -> None:
        try:
            await self._async_adapter.call("close")
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
        self._backend_type = config.backend  # expose for engine resolution
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

    async def get_collection_meta(
        self,
        *,
        ctx: Optional[RequestContext] = None,
    ) -> Optional[Dict[str, Any]]:
        if ctx:
            backend = self._get_backend_for_context(ctx)
        else:
            backend = self._get_default_backend()
        return await backend.get_collection_meta()

    async def update_collection_description(self, description: str) -> bool:
        return await self._get_default_backend().update_collection_description(description)

    # =========================================================================
    # 公开数据操作 API（强制要求 ctx）
    # =========================================================================

    async def upsert(
        self, data: Dict[str, Any], *, ctx: RequestContext, partial_update: bool = False
    ) -> str:
        """Main write entrypoint.

        With the default ``partial_update=False``, this preserves the legacy
        full-record upsert behavior. When ``partial_update=True``, the backend
        first reads the current record and preserves unspecified existing
        fields before issuing the final upsert.
        """
        logger.debug(
            f"[VikingVectorIndexBackend.upsert] Called with ctx.account_id={ctx.account_id}, partial_update={partial_update}, data={data}"
        )
        backend = self._get_backend_for_context(ctx)
        logger.debug(
            f"[VikingVectorIndexBackend.upsert] Using backend for account_id={ctx.account_id}"
        )
        result = await backend.upsert(data, partial_update=partial_update)
        logger.debug(
            f"[VikingVectorIndexBackend.upsert] Completed with partial_update={partial_update}, result={result}"
        )
        return result

    async def update(self, data: Dict[str, Any], *, ctx: RequestContext) -> UpdateResult:
        """Strict update path. The target record must already exist."""
        logger.debug(
            f"[VikingVectorIndexBackend.update] Called with ctx.account_id={ctx.account_id}, data={data}"
        )
        backend = self._get_backend_for_context(ctx)
        logger.debug(
            f"[VikingVectorIndexBackend.update] Using backend for account_id={ctx.account_id}"
        )
        result = await backend.update(data)
        logger.debug(f"[VikingVectorIndexBackend.update] Completed, result={result}")
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

    async def update_search_tags(
        self,
        uri: str,
        tags: List[str],
        *,
        mode: str,
        levels: Optional[List[int]] = None,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        """Update search tags for the exact indexed record or directory summary records."""
        if mode not in {"replace", "append"}:
            raise ValueError(f"unsupported tag mode: {mode}")

        from openviking.utils.tags import merge_search_tags

        canonical_uri = canonicalize_uri(uri, ctx)
        if levels is None:
            record = await self.fetch_by_uri(canonical_uri, ctx=ctx)
            if not record or not record.get("id"):
                return []

            full_records = await self.get([str(record["id"])], ctx=ctx)
            if not full_records:
                logger.warning(
                    "update_search_tags failed to fetch full exact record uri=%s account_id=%s id=%s",
                    canonical_uri,
                    ctx.account_id,
                    record.get("id"),
                )
                return []

            updated_record = dict(full_records[0])
            try:
                if mode == "append":
                    updated_record["search_tags"] = merge_search_tags(
                        updated_record.get("search_tags"), tags
                    )
                else:
                    updated_record["search_tags"] = list(tags)
            except Exception as exc:
                logger.warning(
                    "update_search_tags failed to merge exact record tags uri=%s "
                    "account_id=%s existing_tags=%s incoming_tags=%s error=%s",
                    canonical_uri,
                    ctx.account_id,
                    updated_record.get("search_tags"),
                    tags,
                    exc,
                )
                return []

            if await self.upsert(updated_record, ctx=ctx):
                return [updated_record]
            return []

        records = await self.filter(
            filter=And([Eq("uri", canonical_uri), In("level", levels)]),
            limit=max(len(levels), 2),
            output_fields=FETCH_BY_URI_OUTPUT_FIELDS,
            ctx=ctx,
        )
        if not records:
            return []

        record_ids = [str(record["id"]) for record in records if record.get("id")]
        if not record_ids:
            return []
        full_records = await self.get(record_ids, ctx=ctx)
        full_records_by_id = {
            str(record["id"]): record for record in full_records if record.get("id") is not None
        }

        updated_records: List[Dict[str, Any]] = []
        for record in records:
            if not record or not record.get("id"):
                continue
            full_record = full_records_by_id.get(str(record["id"]))
            if not full_record:
                logger.warning(
                    "update_search_tags failed to fetch full leveled record uri=%s account_id=%s level=%s id=%s",
                    canonical_uri,
                    ctx.account_id,
                    record.get("level"),
                    record.get("id"),
                )
                continue
            updated_record = dict(full_record)
            try:
                if mode == "append":
                    updated_record["search_tags"] = merge_search_tags(
                        updated_record.get("search_tags"), tags
                    )
                else:
                    updated_record["search_tags"] = list(tags)
            except Exception as exc:
                logger.warning(
                    "update_search_tags failed to merge leveled record tags uri=%s "
                    "account_id=%s level=%s existing_tags=%s incoming_tags=%s error=%s",
                    canonical_uri,
                    ctx.account_id,
                    updated_record.get("level"),
                    updated_record.get("search_tags"),
                    tags,
                    exc,
                )
                return []
            if await self.upsert(updated_record, ctx=ctx):
                updated_records.append(updated_record)
        return updated_records

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

    async def search_by_keywords(
        self,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        output_fields: Optional[List[str]] = None,
        *,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        if ctx:
            backend = self._get_backend_for_context(ctx)
        else:
            backend = self._get_default_backend()
        return await backend.search_by_keywords(
            keywords=keywords,
            query=query,
            limit=limit,
            offset=offset,
            filter=filter,
            output_fields=output_fields,
        )

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
        level: Optional[List[int]] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        scope_filter = self._build_scope_filter(
            ctx=ctx,
            context_type=context_type,
            target_directories=target_directories,
            extra_filter=extra_filter,
            level=level,
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
        # TODO：Better Alternative to Current Temporary Fix

        # If parent_uri is already under the requested target_directories,
        # adding a redundant scope prefix filter can slow down the backend.
        # Keep tenant/context filters but skip target_directories in that case.
        effective_target_directories = target_directories
        if target_directories:
            parent_norm = parent_uri.rstrip("/")
            for target_dir in target_directories:
                if not target_dir:
                    continue
                target_norm = target_dir.rstrip("/")
                if parent_norm == target_norm or parent_norm.startswith(target_norm + "/"):
                    effective_target_directories = None
                    break

        merged_filter = self._merge_filters(
            PathScope("uri", parent_uri, depth=1),
            self._build_scope_filter(
                ctx=ctx,
                context_type=context_type,
                target_directories=effective_target_directories,
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

    async def get_context_by_uri(
        self,
        uri: str,
        owner_space: Optional[str] = None,
        level: Optional[int] = None,
        limit: int = 1,
        *,
        ctx: RequestContext,
    ) -> List[Dict[str, Any]]:
        conds: List[FilterExpr] = [
            PathScope("uri", canonicalize_uri(uri, ctx), depth=0),
            Eq("account_id", ctx.account_id),
        ]
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
            canonical_uri = canonicalize_uri(uri, ctx)
            conds: List[FilterExpr] = [
                Eq("account_id", ctx.account_id),
                Or([Eq("uri", canonical_uri), In("uri", [f"{canonical_uri}/"])]),
            ]

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

        canonical_uri = canonicalize_uri(uri, ctx)
        canonical_new_uri = canonicalize_uri(new_uri, ctx)
        conds: List[FilterExpr] = [Eq("uri", canonical_uri), Eq("account_id", ctx.account_id)]
        if levels:
            conds.append(In("level", levels))

        records = await self.filter(
            filter=And(conds),
            limit=100,
            output_fields=URI_REWRITE_OUTPUT_FIELDS,
            ctx=ctx,
        )
        if not records:
            return False
        record_ids = [str(record["id"]) for record in records if record.get("id")]
        if not record_ids:
            logger.warning(
                "update_uri_mapping found records without ids: uri=%s new_uri=%s account_id=%s",
                canonical_uri,
                canonical_new_uri,
                ctx.account_id,
            )
            return False
        full_records = await self.get(record_ids, ctx=ctx)
        if not full_records:
            logger.warning(
                "update_uri_mapping failed to fetch full records: uri=%s new_uri=%s account_id=%s ids=%s",
                canonical_uri,
                canonical_new_uri,
                ctx.account_id,
                record_ids,
            )
            return False

        def _seed_uri_for_id(uri: str, level: int) -> str:
            if level == 0:
                return uri if uri.endswith("/.abstract.md") else f"{uri}/.abstract.md"
            if level == 1:
                return uri if uri.endswith("/.overview.md") else f"{uri}/.overview.md"
            return uri

        success = False
        ids_to_delete: List[str] = []
        for record in full_records:
            if "id" not in record:
                continue
            raw_level = record.get("level", 2)
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                level = 2

            seed_uri = _seed_uri_for_id(canonical_new_uri, level)
            id_seed = f"{ctx.account_id}:{seed_uri}"
            new_id = hashlib.md5(id_seed.encode("utf-8")).hexdigest()

            updated = {
                **record,
                "id": new_id,
                "uri": canonical_new_uri,
            }
            vector = updated.get("vector")
            if not vector:
                logger.warning(
                    "update_uri_mapping skipped record without dense vector: old_uri=%s new_uri=%s level=%s account_id=%s id=%s",
                    canonical_uri,
                    canonical_new_uri,
                    level,
                    ctx.account_id,
                    record.get("id"),
                )
                continue
            result = await self.upsert(updated, ctx=ctx)
            if result:
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
                result = await self.upsert(record | {"active_count": current + 1}, ctx=ctx)
                if result:
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
        level: Optional[List[int]] = None,
    ) -> Optional[FilterExpr]:
        filters: List[FilterExpr] = []
        if context_type:
            filters.append(Eq("context_type", context_type))

        tenant_filter = self._tenant_filter(ctx, context_type=context_type)
        if tenant_filter:
            filters.append(tenant_filter)

        if target_directories:
            uri_conds = [
                PathScope("uri", canonicalize_uri(target_dir, ctx), depth=-1)
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

        if level:
            filters.append(In("level", level))

        return self._merge_filters(*filters)

    @staticmethod
    def _tenant_filter(
        ctx: RequestContext, context_type: Optional[str] = None
    ) -> Optional[FilterExpr]:
        if ctx.role == Role.ROOT:
            return None

        account_filter = Eq("account_id", ctx.account_id)
        path_filter = Or([PathScope("uri", root, depth=-1) for root in visible_roots(ctx)])
        if context_type:
            return And([account_filter, path_filter])
        return And([account_filter, path_filter])

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
