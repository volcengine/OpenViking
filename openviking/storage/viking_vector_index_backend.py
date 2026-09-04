# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VikingDB storage backend for OpenViking."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional

from openviking.core.namespace import canonical_user_root, resolve_uri, uri_parts, visible_roots
from openviking.server.identity import RequestContext, Role
from openviking.storage.acl import (
    ACL_CONTEXT_FIELDS,
    AclAction,
    AclManager,
    acl_grant_tokens,
    acl_principals,
)
from openviking.storage.expr import And, Contains, Eq, FilterExpr, In, Or, PathScope, RawDSL
from openviking.storage.vector_migration import (
    rewrite_vector_record,
    uri_in_transfer_scope,
)
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import UpdateResult
from openviking.storage.vectordb.utils.logging_init import init_cpp_logging
from openviking.storage.vectordb_adapters import create_collection_adapter
from openviking.utils.tags import merge_search_tags
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.exceptions import ConflictError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import DEFAULT_INDEX_NAME, VectorDBBackendConfig
from openviking_cli.utils.uri import VikingURI

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

VIKINGDB_CONTENT_MAX_SIZE = 1024 * 1024


@dataclass(frozen=True)
class UpsertOptions:
    partial_update: bool = False
    search_tag_mode: str = "replace"


@dataclass
class VectorTransferResult:
    """Counts produced by one strict online vector URI transfer."""

    scanned: int = 0
    written: int = 0
    deleted: int = 0
    restored: int = 0
    batches: int = 0


class VectorTransferRollbackError(RuntimeError):
    """Raised when a vector transfer and its compensation both fail."""

    def __init__(self, message: str, *, phase: str, residual_count: int):
        super().__init__(message)
        self.phase = phase
        self.residual_count = residual_count


def normalize_upsert_options(
    options: UpsertOptions | Mapping[str, Any] | None = None,
) -> UpsertOptions:
    if options is None:
        return UpsertOptions()
    if isinstance(options, UpsertOptions):
        return options
    return UpsertOptions(
        partial_update=bool(options.get("partial_update", False)),
        search_tag_mode=str(options.get("search_tag_mode", "replace")),
    )


async def _wait_for_task_completion_despite_cancellation(
    task: asyncio.Task[Any],
) -> Optional[asyncio.CancelledError]:
    """Wait for an offloaded lifecycle operation without leaking its state."""

    pending_cancellation: Optional[asyncio.CancelledError] = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            # A child that cancels itself did not complete the lifecycle
            # operation and must still be reported by ``task.result()``.
            if not task.cancelled() and pending_cancellation is None:
                pending_cancellation = exc
        except BaseException:
            # Read the task's exception below so the caller can apply the
            # lifecycle-specific exception ordering.
            if not task.done():
                raise
    return pending_cancellation


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

        # ``content`` (full text) is only meaningful for VikingDB-backed backends,
        # which use it for server-side full-text grep. Every other backend leaves
        # ``USE_CONTENT_FIELD=False`` and gets ``content`` dropped here, so its large
        # payload can't blow past the local engine's per-field byte limit. A new backend
        # that doesn't need ``content`` requires no extra code.
        if self._adapter.USE_CONTENT_FIELD:
            content = result.get("content")
            if isinstance(content, (str, bytes)):
                result["content"] = content[:VIKINGDB_CONTENT_MAX_SIZE]
        else:
            result.pop("content", None)

        return result

    def _prepare_upsert_payloads(self, data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Prepare a batch in one worker-thread handoff."""
        return [self._prepare_upsert_payload(data) for data in data_list]

    def _bind_upsert_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Copy a record, enforce its bound account, and apply write defaults."""
        payload = dict(data)
        if self._bound_account_id:
            account_id = payload.get("account_id")
            if account_id and account_id != self._bound_account_id:
                raise PermissionError(
                    "record account_id does not match the request context account_id"
                )
            payload["account_id"] = self._bound_account_id

        context_type = payload.get("context_type")
        if context_type and context_type not in VikingVectorIndexBackend.ALLOWED_CONTEXT_TYPES:
            raise ValueError(
                f"Invalid context_type: {context_type}. "
                f"Must be one of {sorted(VikingVectorIndexBackend.ALLOWED_CONTEXT_TYPES)}"
            )

        if not payload.get("id"):
            payload["id"] = str(uuid.uuid4())
        return payload

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

    async def upsert(
        self,
        data: Dict[str, Any],
        options: UpsertOptions | Mapping[str, Any] | None = None,
    ) -> str:
        options = normalize_upsert_options(options)
        try:
            payload = self._bind_upsert_payload(data)
        except (PermissionError, ValueError) as exc:
            logger.warning("Rejecting upsert: %s", exc)
            return ""

        if options.partial_update:
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
                raise

            if existing_records:
                existing = dict(existing_records[0])
                if options.search_tag_mode == "append" and payload.get("search_tags") is not None:
                    payload["search_tags"] = merge_search_tags(
                        existing.get("search_tags"),
                        payload.get("search_tags"),
                    )
                existing.update({k: v for k, v in payload.items() if v is not None})
                payload = existing

        payload = await self._async_adapter.run(self._prepare_upsert_payload, payload)
        ids = await self._async_adapter.call("upsert", payload)
        return ids[0] if ids else ""

    async def upsert_many(self, data_list: List[Dict[str, Any]]) -> List[str]:
        """Bulk full-record upsert through one adapter call.

        The batch is validated before the adapter is invoked. Partial-update
        semantics intentionally remain on :meth:`upsert`, where each existing
        record is read and merged independently. Returned IDs preserve input
        order; invalid batches raise before the adapter is invoked.
        """
        if not data_list:
            return []

        payloads: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, data in enumerate(data_list):
            try:
                payload = self._bind_upsert_payload(data)
            except PermissionError as exc:
                raise PermissionError(f"record at index {index}: {exc}") from exc
            except ValueError as exc:
                raise ValueError(f"record at index {index}: {exc}") from exc
            record_id = str(payload["id"])
            if record_id in seen_ids:
                raise ValueError(f"duplicate record id at index {index}")
            seen_ids.add(record_id)
            payloads.append(payload)

        payloads = await self._async_adapter.run(self._prepare_upsert_payloads, payloads)
        ids = await self._async_adapter.call("upsert", payloads)
        normalized_ids = [str(item) for item in (ids or []) if item is not None]
        expected_ids = [str(payload["id"]) for payload in payloads]
        if normalized_ids != expected_ids:
            raise RuntimeError(
                "bulk upsert adapter returned IDs that do not match the input count and order "
                f"(expected {len(expected_ids)}, got {len(normalized_ids)})"
            )
        return normalized_ids

    async def begin_bulk_ingest(self) -> None:
        await self._async_adapter.call("begin_bulk_ingest")

    async def end_bulk_ingest(self) -> None:
        await self._async_adapter.call("end_bulk_ingest")

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

    def _with_account_filter(
        self, filter: Optional[Dict[str, Any] | FilterExpr]
    ) -> Optional[FilterExpr]:
        if not self._bound_account_id:
            if isinstance(filter, dict):
                return RawDSL(filter)
            return filter
        account_filter = Eq("account_id", self._bound_account_id)
        if not filter:
            return account_filter
        if isinstance(filter, dict):
            filter = RawDSL(filter)
        return And([account_filter, filter])

    async def get_strict(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch records without converting backend errors to misses."""
        records = await self._async_adapter.call("get", ids)
        if self._bound_account_id:
            records = [r for r in records if r.get("account_id") == self._bound_account_id]
        return records

    async def strict_get(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Transaction alias for strict record reads."""
        return await self.get_strict(ids)

    async def strict_delete(self, ids: List[str]) -> int:
        """Delete transaction records and propagate every backend failure."""
        if self._bound_account_id:
            records = await self.strict_get(ids)
            valid_ids = [str(record["id"]) for record in records if record.get("id")]
            ids = valid_ids
        if not ids:
            return 0
        return int(await self._async_adapter.call("delete", ids=ids) or 0)

    async def strict_query(
        self,
        *,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        """Query transaction records without fail-open exception handling."""
        return await self._async_adapter.call(
            "query",
            query_vector=None,
            sparse_query_vector=None,
            filter=self._with_account_filter(filter),
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            order_by=order_by,
            order_desc=order_desc,
        )

    async def strict_scroll(
        self,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Return a stable URI-ordered page for a transactional scan."""
        offset = int(cursor) if cursor else 0
        records = await self.strict_query(
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
            # The local engine's scalar sorter does not return records for
            # path/string fields. ``updated_at`` is an indexed date-time field
            # on every context collection and provides stable offset pages.
            order_by="updated_at",
            order_desc=False,
        )
        next_cursor = str(offset + len(records)) if len(records) == limit else None
        return records, next_cursor

    async def strict_count(self, filter: Optional[Dict[str, Any] | FilterExpr] = None) -> int:
        """Count transaction records without converting backend errors to zero."""
        return int(
            await self._async_adapter.call("count", filter=self._with_account_filter(filter)) or 0
        )

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
            raise

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

            ids = [str(r["id"]) for r in target_records if r.get("id")]
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
        """Scroll records without converting backend failures into an empty page."""
        if isinstance(filter, dict):
            filter = RawDSL(filter)
        if self._bound_account_id:
            account_filter = Eq("account_id", self._bound_account_id)
            filter = And([account_filter, filter]) if filter else account_filter

        offset = int(cursor) if cursor else 0
        records = await self._async_adapter.call(
            "query",
            filter=filter,
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
            raise

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
        self.acl_manager: Optional[AclManager] = None

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

    @property
    def uses_content_field(self) -> bool:
        return self._shared_adapter.USE_CONTENT_FIELD

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
        self,
        data: Dict[str, Any],
        *,
        ctx: RequestContext,
        options: UpsertOptions | Mapping[str, Any] | None = None,
    ) -> str:
        """Main write entrypoint.

        With the default ``options.partial_update=False``, this preserves the legacy
        full-record upsert behavior. When ``options.partial_update=True``, the backend
        first reads the current record and preserves unspecified existing
        fields before issuing the final upsert.
        """
        options = normalize_upsert_options(options)
        logger.debug(
            "[VikingVectorIndexBackend.upsert] uri=%s partial_update=%s search_tag_mode=%s",
            data.get("uri", ""),
            options.partial_update,
            options.search_tag_mode,
        )
        data = {key: value for key, value in data.items() if key not in ACL_CONTEXT_FIELDS}
        data = (await self._materialize_acl_fields([data], ctx))[0]
        backend = self._get_backend_for_context(ctx)
        logger.debug(
            "[VikingVectorIndexBackend.upsert] Using backend for account_id=%s",
            ctx.account_id,
        )
        result = await backend.upsert(
            data,
            options=options,
        )
        logger.debug(
            "[VikingVectorIndexBackend.upsert] Completed with partial_update=%s, "
            "search_tag_mode=%s, result=%s",
            options.partial_update,
            options.search_tag_mode,
            result,
        )
        return result

    async def upsert_many(
        self, data_list: List[Dict[str, Any]], *, ctx: RequestContext
    ) -> List[str]:
        """Bulk full-record upsert.

        All records are validated for the bound account before one adapter
        invocation is made. Adapters may split that invocation into multiple
        data-plane requests, so this API does not guarantee transaction
        atomicity. Use :meth:`upsert` with
        ``options=UpsertOptions(partial_update=True)`` when omitted fields must
        be preserved from existing records.
        """
        logger.debug(
            "[VikingVectorIndexBackend.upsert_many] Called with ctx.account_id=%s, count=%s",
            ctx.account_id,
            len(data_list),
        )
        data_list = [
            {key: value for key, value in record.items() if key not in ACL_CONTEXT_FIELDS}
            for record in data_list
        ]
        data_list = await self._materialize_acl_fields(data_list, ctx)
        result = await self._upsert_many_raw(data_list, ctx=ctx)
        logger.debug(
            "[VikingVectorIndexBackend.upsert_many] Completed with count=%s, result_count=%s",
            len(data_list),
            len(result),
        )
        return result

    async def _upsert_many_raw(
        self, data_list: List[Dict[str, Any]], *, ctx: RequestContext
    ) -> List[str]:
        """Write records whose ACL fields have already been materialized."""
        return await self._get_backend_for_context(ctx).upsert_many(data_list)

    async def _materialize_acl_fields(
        self, records: List[Dict[str, Any]], ctx: RequestContext
    ) -> List[Dict[str, Any]]:
        if not self.acl_manager or not records:
            return records
        return await self.acl_manager.materialize_context_records(records, ctx)

    async def update(self, data: Dict[str, Any], *, ctx: RequestContext) -> UpdateResult:
        """Strict update path. The target record must already exist."""
        data = {key: value for key, value in data.items() if key not in ACL_CONTEXT_FIELDS}
        logger.debug(
            "[VikingVectorIndexBackend.update] uri=%s",
            data.get("uri", ""),
        )
        backend = self._get_backend_for_context(ctx)
        logger.debug(
            f"[VikingVectorIndexBackend.update] Using backend for account_id={ctx.account_id}"
        )
        result = await backend.update(data)
        logger.debug(f"[VikingVectorIndexBackend.update] Completed, result={result}")
        return result

    @asynccontextmanager
    async def bulk_ingest(self, *, ctx: RequestContext) -> AsyncIterator[None]:
        """Coalesce optional derived-index rebuilds across many write calls.

        The scope is a performance hint only. It does not make the enclosed
        writes transactional or atomic, and adapters that do not maintain a
        derived local index treat it as a no-op.
        """
        backend = self._get_backend_for_context(ctx)
        begin_task = asyncio.create_task(backend.begin_bulk_ingest())
        entry_cancellation = await _wait_for_task_completion_despite_cancellation(begin_task)
        # A failed or self-cancelled begin did not acquire the scope, so it
        # must not be balanced with an end call.
        begin_task.result()
        if entry_cancellation is not None:
            end_task = asyncio.create_task(backend.end_bulk_ingest())
            await _wait_for_task_completion_despite_cancellation(end_task)
            # Cleanup failures take priority because they mean the suspension
            # may still be live. Otherwise preserve the original cancellation.
            end_task.result()
            raise entry_cancellation
        try:
            yield
        finally:
            end_task = asyncio.create_task(backend.end_bulk_ingest())
            exit_cancellation = await _wait_for_task_completion_despite_cancellation(end_task)
            end_task.result()
            if exit_cancellation is not None:
                raise exit_cancellation

    async def get(self, ids: List[str], *, ctx: RequestContext) -> List[Dict[str, Any]]:
        backend = self._get_backend_for_context(ctx)
        return await backend.get(ids)

    async def get_strict(self, ids: List[str], *, ctx: RequestContext) -> List[Dict[str, Any]]:
        return await self._get_backend_for_context(ctx).get_strict(ids)

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

        if levels is None:
            record = await self.fetch_by_uri(uri, ctx=ctx)
            if not record or not record.get("id"):
                return []

            full_records = await self.get([str(record["id"])], ctx=ctx)
            if not full_records:
                logger.warning(
                    "update_search_tags failed to fetch full exact record uri=%s account_id=%s id=%s",
                    uri,
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
                    uri,
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
            filter=And([Eq("uri", uri), In("level", levels)]),
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
                    uri,
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
                    uri,
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

    async def _strict_transfer_page(
        self,
        ctx: RequestContext,
        filter: FilterExpr,
        *,
        limit: int,
        cursor: Optional[str],
        output_fields: List[str],
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        backend = self._get_backend_for_context(ctx)
        return await backend.strict_scroll(
            filter=filter,
            limit=limit,
            cursor=cursor,
            output_fields=output_fields,
        )

    async def _strict_transfer_count(self, ctx: RequestContext, filter: FilterExpr) -> int:
        backend = self._get_backend_for_context(ctx)
        return await backend.strict_count(filter=filter)

    async def _strict_transfer_get(
        self, ctx: RequestContext, ids: List[str]
    ) -> List[Dict[str, Any]]:
        backend = self._get_backend_for_context(ctx)
        return await backend.strict_get(ids)

    async def _strict_transfer_delete(self, ctx: RequestContext, ids: List[str]) -> int:
        backend = self._get_backend_for_context(ctx)
        return await backend.strict_delete(ids)

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
            filter = self._merge_filters(filter, self._tenant_filter(ctx))
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
            PathScope("uri", uri, depth=0),
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

    async def get_l2_abstracts_by_uris(
        self,
        uris: List[str],
        *,
        ctx: RequestContext,
    ) -> Dict[str, str]:
        """Strictly load existing L2 abstracts for a bounded URI set."""
        requested_by_canonical: Dict[str, str] = {}
        for uri in uris:
            requested_by_canonical.setdefault(resolve_uri(uri).uri, uri)
        canonical_uris = list(requested_by_canonical)
        if not canonical_uris:
            return {}

        abstracts: Dict[str, str] = {}
        chunk_size = 100
        for start in range(0, len(canonical_uris), chunk_size):
            chunk = canonical_uris[start : start + chunk_size]
            cursor: Optional[str] = None
            while True:
                records, cursor = await self._strict_transfer_page(
                    ctx,
                    And([In("uri", chunk), Eq("level", 2)]),
                    limit=100,
                    cursor=cursor,
                    output_fields=["uri", "abstract", "updated_at"],
                )
                for record in records:
                    uri = str(record.get("uri") or "")
                    abstract = str(record.get("abstract") or "").strip()
                    if uri and abstract and uri not in abstracts:
                        abstracts[uri] = abstract
                if cursor is None:
                    break
        return {
            requested_by_canonical[uri]: abstract
            for uri, abstract in abstracts.items()
            if uri in requested_by_canonical
        }

    async def delete_account_data(self, account_id: str, *, ctx: RequestContext) -> int:
        """删除指定 account 的所有数据（仅限，root 角色操作）"""
        self._check_root_role(ctx)
        root_backend = self._get_root_backend()
        return await root_backend.delete_by_filter(Eq("account_id", account_id))

    async def delete_user_data(
        self,
        account_id: str,
        user_id: str,
        *,
        ctx: RequestContext,
    ) -> int:
        """Delete every vector record owned by one user as ROOT."""
        self._check_root_role(ctx)
        root_backend = self._get_root_backend()
        return await root_backend.delete_by_filter(
            And([Eq("account_id", account_id), Eq("owner_user_id", user_id)])
        )

    async def delete_uris(self, ctx: RequestContext, uris: List[str]) -> None:
        for uri in uris:
            conds: List[FilterExpr] = [
                Eq("account_id", ctx.account_id),
                Or([Eq("uri", uri), In("uri", [f"{uri}/"])]),
            ]

            backend = self._get_backend_for_context(ctx)
            await backend.delete_by_filter(And(conds))

    def _uri_transfer_filter(self, ctx: RequestContext, uri: str, *, recursive: bool) -> FilterExpr:
        scopes: List[FilterExpr] = [Eq("uri", uri)]
        if recursive:
            scopes.append(PathScope("uri", uri, depth=-1))
        if self.mode == "volcengine":
            parent = VikingURI(uri).parent
            if parent is not None and parent.uri != "viking://":
                scopes.append(PathScope("uri", parent.uri, depth=1))
        else:
            scopes.append(Contains("uri", uri + "#"))
        return And([Eq("account_id", ctx.account_id), Or(scopes)])

    async def _scan_uri_transfer_scope(
        self,
        ctx: RequestContext,
        uri: str,
        *,
        recursive: bool,
        include_full_records: bool,
        batch_size: int = 100,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Scan one URI scope without a fixed total-record limit."""
        transfer_filter = self._uri_transfer_filter(ctx, uri, recursive=recursive)
        expected_count = await self._strict_transfer_count(ctx, transfer_filter)
        records: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        batches = 0
        seen_cursors: set[str] = set()
        seen_ids: set[str] = set()
        scanned_count = 0
        while True:
            page, next_cursor = await self._strict_transfer_page(
                ctx,
                transfer_filter,
                limit=batch_size,
                cursor=cursor,
                output_fields=["id", "uri"],
            )
            batches += 1
            if not page and scanned_count < expected_count:
                raise RuntimeError(
                    f"Vector scan ended after {scanned_count} of {expected_count} records under {uri}"
                )
            scanned_count += len(page)
            for record in page:
                record_id = record.get("id")
                if not record_id:
                    raise RuntimeError(f"Vector records without IDs found under {uri}")
                normalized_id = str(record_id)
                if normalized_id in seen_ids:
                    raise RuntimeError(
                        f"Vector scan returned duplicate vector record {normalized_id} under {uri}"
                    )
                seen_ids.add(normalized_id)
            scoped = [
                record
                for record in page
                if isinstance(record.get("uri"), str)
                and uri_in_transfer_scope(record["uri"], uri, recursive=recursive)
            ]
            if include_full_records and scoped:
                ids = [str(record["id"]) for record in scoped if record.get("id")]
                if len(ids) != len(scoped):
                    raise RuntimeError(f"Vector records without IDs found under {uri}")
                full_records = await self._strict_transfer_get(ctx, ids)
                by_id = {str(record["id"]): record for record in full_records if record.get("id")}
                if len(by_id) != len(ids):
                    raise RuntimeError(f"Failed to fetch complete vector records under {uri}")
                records.extend(by_id[record_id] for record_id in ids)
            else:
                records.extend(scoped)

            if scanned_count == expected_count:
                break
            if scanned_count > expected_count:
                raise RuntimeError(
                    f"Vector scan returned {scanned_count} records but count was {expected_count} "
                    f"under {uri}"
                )
            if next_cursor is None:
                raise RuntimeError(
                    f"Vector scan cursor ended after {scanned_count} of {expected_count} records "
                    f"under {uri}"
                )
            if next_cursor in seen_cursors:
                raise RuntimeError(f"Vector scroll cursor repeated under {uri}: {next_cursor}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return records, batches

    async def _delete_vector_transfer_ids(
        self,
        ctx: RequestContext,
        ids: List[str],
        *,
        batch_size: int = 100,
    ) -> int:
        deleted = 0
        for offset in range(0, len(ids), batch_size):
            batch = ids[offset : offset + batch_size]
            batch_deleted = await self._strict_transfer_delete(ctx, batch)
            residual = await self._strict_transfer_get(ctx, batch)
            if residual:
                raise RuntimeError(
                    f"Vector cleanup deleted {batch_deleted} of {len(batch)} records and left "
                    f"{len(residual)} records"
                )
            if batch_deleted != len(batch):
                logger.info(
                    "Vector cleanup removed %s of %s attempted IDs; remaining IDs were never written",
                    batch_deleted,
                    len(batch),
                )
            deleted += batch_deleted
        return deleted

    async def copy_uri_mapping(
        self,
        ctx: RequestContext,
        source_uri: str,
        target_uri: str,
        recursive: bool = False,
    ) -> VectorTransferResult:
        """Copy every vector record in a URI scope without regenerating embeddings."""
        source_uri = resolve_uri(source_uri).uri
        target_uri = resolve_uri(target_uri).uri
        target_records, _ = await self._scan_uri_transfer_scope(
            ctx,
            target_uri,
            recursive=recursive,
            include_full_records=False,
        )
        if target_records:
            raise ConflictError(
                f"copy target vector scope already exists: {target_uri}",
                resource=target_uri,
            )

        source_records, batches = await self._scan_uri_transfer_scope(
            ctx,
            source_uri,
            recursive=recursive,
            include_full_records=True,
        )
        result = VectorTransferResult(scanned=len(source_records), batches=batches)
        if not source_records:
            return result

        timestamp = get_current_timestamp()
        target_payloads = [
            rewrite_vector_record(
                record,
                source_uri=source_uri,
                target_uri=target_uri,
                ctx=ctx,
                mode="copy",
                timestamp=timestamp,
            )
            for record in source_records
        ]
        attempted_target_ids: List[str] = []
        try:
            for offset in range(0, len(target_payloads), 100):
                payload_batch = target_payloads[offset : offset + 100]
                attempted_target_ids.extend(str(payload["id"]) for payload in payload_batch)
                written_ids = await self.upsert_many(payload_batch, ctx=ctx)
                if len(written_ids) != len(payload_batch):
                    raise RuntimeError(
                        f"Vector copy wrote {len(written_ids)} of {len(payload_batch)} records"
                    )
                result.written += len(written_ids)
        except Exception as transfer_error:
            try:
                await self._delete_vector_transfer_ids(ctx, attempted_target_ids)
            except Exception as rollback_error:
                diagnostic_suffix = ""
                try:
                    residual_records, _ = await self._scan_uri_transfer_scope(
                        ctx,
                        target_uri,
                        recursive=recursive,
                        include_full_records=False,
                    )
                    residual_count = len(residual_records)
                except Exception as diagnostic_error:
                    residual_count = len(attempted_target_ids)
                    diagnostic_suffix = f"; residual scan failed: {diagnostic_error}"
                raise VectorTransferRollbackError(
                    f"Vector copy failed and target cleanup failed: {rollback_error}"
                    f"{diagnostic_suffix}",
                    phase="copy_target_cleanup",
                    residual_count=residual_count,
                ) from transfer_error
            raise
        return result

    async def update_uri_mapping(
        self,
        ctx: RequestContext,
        source_uri: str,
        target_uri: str,
        recursive: bool = False,
    ) -> VectorTransferResult:
        """Move every vector record in a URI scope with compensating rollback."""
        source_uri = resolve_uri(source_uri).uri
        target_uri = resolve_uri(target_uri).uri
        target_records, _ = await self._scan_uri_transfer_scope(
            ctx,
            target_uri,
            recursive=recursive,
            include_full_records=False,
        )
        if target_records:
            raise ConflictError(
                f"move target vector scope already exists: {target_uri}",
                resource=target_uri,
            )

        source_records, batches = await self._scan_uri_transfer_scope(
            ctx,
            source_uri,
            recursive=recursive,
            include_full_records=True,
        )
        result = VectorTransferResult(scanned=len(source_records), batches=batches)
        if not source_records:
            return result

        timestamp = get_current_timestamp()
        acl_enabled = self._acl_enabled(ctx)
        moved_acl_by_uri: Dict[str, Dict[str, Any]] = {}
        target_payloads: List[Dict[str, Any]] = []
        for record in source_records:
            payload = rewrite_vector_record(
                record,
                source_uri=source_uri,
                target_uri=target_uri,
                ctx=ctx,
                mode="move",
                timestamp=timestamp,
            )
            if acl_enabled:
                assert self.acl_manager is not None
                rewritten_uri = str(payload["uri"])
                acl_fields = moved_acl_by_uri.get(rewritten_uri)
                if acl_fields is None:
                    acl_fields = await self.acl_manager.materialize_moved_record(
                        record,
                        rewritten_uri,
                        ctx,
                    )
                    moved_acl_by_uri[rewritten_uri] = acl_fields
                payload.update(acl_fields)
            target_payloads.append(payload)
        target_ids = [str(payload["id"]) for payload in target_payloads]
        attempted_target_ids: List[str] = []
        try:
            for offset in range(0, len(target_payloads), 100):
                payload_batch = target_payloads[offset : offset + 100]
                attempted_target_ids.extend(str(payload["id"]) for payload in payload_batch)
                written_ids = (
                    await self._upsert_many_raw(payload_batch, ctx=ctx)
                    if acl_enabled
                    else await self.upsert_many(payload_batch, ctx=ctx)
                )
                if len(written_ids) != len(payload_batch):
                    raise RuntimeError(
                        f"Vector move wrote {len(written_ids)} of {len(payload_batch)} records"
                    )
                result.written += len(written_ids)
        except Exception as transfer_error:
            try:
                await self._delete_vector_transfer_ids(ctx, attempted_target_ids)
            except Exception as rollback_error:
                raise VectorTransferRollbackError(
                    f"Vector move prepare failed and target cleanup failed: {rollback_error}",
                    phase="move_target_cleanup",
                    residual_count=len(attempted_target_ids),
                ) from transfer_error
            raise

        source_ids = [str(record["id"]) for record in source_records]
        try:
            for offset in range(0, len(source_ids), 100):
                source_batch = source_ids[offset : offset + 100]
                deleted = await self._strict_transfer_delete(ctx, source_batch)
                if deleted != len(source_batch):
                    raise RuntimeError(
                        f"Vector move deleted {deleted} of {len(source_batch)} source records"
                    )
                result.deleted += deleted
        except Exception as transfer_error:
            rollback_errors: List[Exception] = []
            try:
                restored_ids = await self._upsert_many_raw(source_records, ctx=ctx)
                result.restored = len(restored_ids)
                if result.restored != len(source_records):
                    raise RuntimeError(
                        f"Vector move restored {result.restored} of {len(source_records)} records"
                    )
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
            try:
                await self._delete_vector_transfer_ids(ctx, target_ids)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
            if rollback_errors:
                raise VectorTransferRollbackError(
                    "Vector move source deletion failed and compensation was incomplete: "
                    + "; ".join(str(error) for error in rollback_errors),
                    phase="move_source_restore",
                    residual_count=len(source_records),
                ) from transfer_error
            raise
        return result

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

        targets = [target_dir for target_dir in target_directories or [] if target_dir]
        tenant_filter = self._tenant_filter(ctx)
        if (
            tenant_filter
            and not self._acl_enabled(ctx)
            and self._targets_within_visible_roots(ctx, targets)
        ):
            # The target scopes are already narrower than the tenant-visible
            # roots. Keep account isolation, but avoid recursively evaluating
            # the broader path scopes as an additional filter.
            tenant_filter = Eq("account_id", ctx.account_id)
        if tenant_filter:
            filters.append(tenant_filter)

        if targets:
            uri_conds = [PathScope("uri", target_dir, depth=-1) for target_dir in targets]
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
    def _targets_within_visible_roots(ctx: RequestContext, targets: List[str]) -> bool:
        if not targets:
            return False

        root_parts = [tuple(uri_parts(root)) for root in visible_roots(ctx)]
        return all(
            any(
                len(target_parts) >= len(root) and target_parts[: len(root)] == root
                for root in root_parts
            )
            for target_parts in (tuple(uri_parts(target)) for target in targets)
        )

    def _tenant_filter(self, ctx: RequestContext) -> Optional[FilterExpr]:
        if ctx.bypass_acl:
            return Eq("account_id", ctx.account_id)
        if ctx.role == Role.ROOT:
            return None

        account_filter = Eq("account_id", ctx.account_id)
        if not self._acl_enabled(ctx):
            return And(
                [
                    account_filter,
                    Or([PathScope("uri", root, depth=-1) for root in visible_roots(ctx)]),
                ]
            )

        legacy_filter = And(
            [
                RawDSL({"op": "must_not", "field": "acl_enabled", "conds": [True]}),
                Or([PathScope("uri", root, depth=-1) for root in visible_roots(ctx)]),
            ]
        )
        read_grants = acl_grant_tokens(acl_principals(ctx), AclAction.READ)
        shared_acl_filter = And(
            [
                PathScope("uri", "viking://resources", depth=-1),
                Or(
                    [
                        In("acl_direct_grants", read_grants),
                        In("acl_inherited_grants", read_grants),
                    ]
                ),
            ]
        )
        access_filters: List[FilterExpr] = [
            legacy_filter,
            shared_acl_filter,
            PathScope("uri", f"{canonical_user_root(ctx)}/resources", depth=-1),
        ]
        if ctx.role == Role.ADMIN:
            access_filters.append(PathScope("uri", "viking://resources", depth=-1))
        return And([account_filter, Or(access_filters)])

    def _acl_enabled(self, ctx: RequestContext) -> bool:
        return self.acl_manager is not None and self.acl_manager.is_enabled(ctx.account_id)

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
