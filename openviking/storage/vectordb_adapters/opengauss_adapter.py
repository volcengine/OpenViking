# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""openGauss vector database adapter using psycopg2.

openGauss (DataVec) provides native vector types and HNSW / IVFFlat / DISKANN
indexes. No separate pgvector extension is required on openGauss >= 6.0.3.

Driver: any psycopg2-compatible driver.
  Install: pip install "openviking[opengauss]"  (installs psycopg2-binary)
  The openGauss-connector-python-psycopg2 package exposes the same `psycopg2`
  module interface and works as a drop-in alternative.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openviking.storage.expr import FilterExpr, Or
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb_adapters.base import (
    VIKINGDB_STRING_FIELD_BYTE_LIMIT,
    VIKINGDB_TEXT_FIELD_BYTE_LIMIT,
    CollectionAdapter,
)
from openviking_cli.exceptions import UnimplementedError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import (
    OpenGaussConfig,
    VectorDBBackendConfig,
)

from .opengauss.catalog import (
    _create_collection_table,
    _ensure_meta_table,
    _load_collection_meta,
    _validate_distributed_environment,
)
from .opengauss.collection import (
    OpenGaussCollection,
)
from .opengauss.connection import (
    _create_connection_pool,
    _PooledConnectionProxy,
)
from .opengauss.sql import (
    _EMPTY_OR_FILTER,
    _META_TABLE,
    _VECTOR_OPS,
    _build_where_clause,
    _date_time_to_epoch_ms,
    _index_access_method,
    _index_quantization,
    _quote_identifier,
    _validate_identifier,
)

logger = get_logger(__name__)


def _save_collection_meta(conn, name: str, meta: Dict[str, Any], distributed: bool = False):
    """Merge collection metadata under a row lock."""
    _ensure_meta_table(conn, distributed=distributed)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f'SELECT meta_json FROM "{_META_TABLE}" WHERE table_name = %s FOR UPDATE',
            (name,),
        )
        row = cursor.fetchone()
        if row is None:
            merged = dict(meta)
            cursor.execute(
                f'INSERT INTO "{_META_TABLE}" (table_name, meta_json) VALUES (%s, %s)',
                (name, json.dumps(merged)),
            )
        else:
            merged = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
            merged.update(meta)
            cursor.execute(
                f'UPDATE "{_META_TABLE}" SET meta_json = %s WHERE table_name = %s',
                (json.dumps(merged), name),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("Collection metadata write did not affect exactly one row")
        conn.commit()
        return merged
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


class OpenGaussCollectionAdapter(CollectionAdapter):
    """OpenViking CollectionAdapter backed by openGauss DataVec.

    Supports two deployment modes controlled by ``opengauss.mode``:

    * **standalone** (default): connects to a single-node openGauss instance.
    * **distributed**: connects to the CN node of an openGauss cluster with
      spq_plugin_v2. Collection and metadata tables use verified distributed
      catalog entries; metadata uses reference tables only when the CN supports them.

    Index knobs follow the cuVS-style config shape:
    ``index_type`` + ``build_params`` + ``search_params``.
    """

    mode = "opengauss"

    def __init__(
        self,
        collection_name: str,
        config: OpenGaussConfig,
        index_name: str = "default",
        distance_metric: str = "cosine",
    ):
        super().__init__(
            collection_name=_validate_identifier(collection_name, kind="openGauss collection name"),
            index_name=index_name,
        )
        validated_config = config.model_copy(deep=True)
        validated_config.validate_capabilities(
            distributed=config.is_distributed, distance=distance_metric
        )
        self._host = validated_config.host
        self._port = validated_config.port
        self._user = validated_config.user
        self._password = validated_config.password
        self._db_name = validated_config.db_name
        self._distributed = validated_config.is_distributed
        self._shard_count = validated_config.shard_count
        self._distance_metric = (distance_metric or "cosine").lower()
        self._index_type = validated_config.index_type
        self._build_params = dict(validated_config.build_params)
        self._parallel_workers = validated_config.parallel_workers
        self._search_params = dict(validated_config.search_params)
        self._maintenance_work_mem_mb = validated_config.maintenance_work_mem_mb
        self._connection_pool_min_size = validated_config.connection_pool_min_size
        self._connection_pool_max_size = validated_config.connection_pool_max_size
        self._retrieval_config = {
            "candidate_limit": validated_config.candidate_limit,
        }
        self._sparse_weight = 0.0
        self._pool = None
        self._conn = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self):
        self._pool = _create_connection_pool(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db_name=self._db_name,
            min_size=self._connection_pool_min_size,
            max_size=self._connection_pool_max_size,
        )
        self._conn = _PooledConnectionProxy(self._pool)
        try:
            if self._distributed:
                _validate_distributed_environment(self._conn)
            _ensure_meta_table(self._conn, distributed=self._distributed)
        except Exception:
            self._conn.close()
            self._conn = None
            self._pool = None
            raise
        logger.info(
            "opengauss_adapter: connected to %s:%s db=%s (distributed=%s)",
            self._host,
            self._port,
            self._db_name,
            self._distributed,
        )

    # ------------------------------------------------------------------
    # CollectionAdapter: required overrides
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: VectorDBBackendConfig) -> "OpenGaussCollectionAdapter":
        if config.opengauss is None:
            raise ValueError("VectorDB opengauss backend requires 'opengauss' config")
        adapter = cls(
            collection_name=config.name or "context",
            config=config.opengauss,
            index_name=config.index_name or "default",
            distance_metric=config.distance_metric,
        )
        adapter._sparse_weight = config.sparse_weight
        return adapter

    def probe_capabilities(self) -> Dict[str, Any]:
        from .opengauss.capabilities import probe_fulltext_capabilities

        return probe_fulltext_capabilities(self._conn)

    USE_CONTENT_FIELD = True
    _STRING_FIELD_BYTE_LIMIT = VIKINGDB_STRING_FIELD_BYTE_LIMIT
    _TEXT_FIELD_BYTE_LIMIT = VIKINGDB_TEXT_FIELD_BYTE_LIMIT

    def query(self, *, advance: Optional[Dict[str, Any]] = None, **kwargs):
        from .opengauss.retrieval import _parse_time_decay, _validate_search_parameters

        limit, offset = kwargs.get("limit", 10), kwargs.get("offset", 0)
        _validate_search_parameters(limit, offset, kwargs.get("output_fields"))
        _parse_time_decay(advance)
        if advance is not None:
            if self._distributed:
                raise UnimplementedError("SPQ advanced ranking has not been certified")
            if limit and not (kwargs.get("query_vector") or kwargs.get("sparse_query_vector")):
                raise ValueError("openGauss advanced ranking requires a vector query")
        if limit == 0:
            self.get_collection().search_by_vector(
                index_name=self._index_name,
                dense_vector=kwargs.get("query_vector"),
                sparse_vector=kwargs.get("sparse_query_vector"),
                limit=limit,
                offset=offset,
                filters=self._compile_filter(kwargs.get("filter")),
                output_fields=kwargs.get("output_fields"),
                advance=advance,
            )
            return []
        return super().query(advance=advance, **kwargs)

    def _configure_retrieval(self, collection: OpenGaussCollection) -> None:
        collection._retrieval_config = dict(getattr(self, "_retrieval_config", {}))
        collection._sparse_weight = getattr(self, "_sparse_weight", 0.0)

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        compiled = super()._compile_filter(expr)
        # Base compile collapses empty Or to ``{}``, which this backend treats
        # as an unfiltered scan. Vacuous OR is a contradiction; keep the DSL
        # so ``_build_where_clause`` emits FALSE (including nested And/Or).
        if isinstance(expr, Or) and not compiled:
            return dict(_EMPTY_OR_FILTER)
        return compiled

    def _table_exists(self, table_name: str) -> bool:
        """Return True if *table_name* exists in the current schema.

        Backend failures propagate to the caller: reporting "does not exist"
        on a transient error would let the creation flow reuse an existing
        physical table with freshly rewritten metadata.
        """
        try:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = %s AND table_schema = current_schema()
                    """,
                    (table_name,),
                )
                row = cur.fetchone()
                self._conn.commit()
                return row is not None
            finally:
                cur.close()
        except Exception:
            self._conn.rollback()
            raise

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return

        meta = _load_collection_meta(
            self._conn, self._collection_name, distributed=self._distributed
        )
        if meta is None:
            return

        # Check the actual table exists
        if not self._table_exists(self._collection_name):
            return

        dim = meta.get("_dim", 0)
        distance = (self._distance_metric or "cosine").lower()
        if meta.get("_distance") != distance:
            meta = _save_collection_meta(
                self._conn,
                self._collection_name,
                {"_distance": distance},
                distributed=self._distributed,
            )
        og_coll = OpenGaussCollection(
            self._conn,
            self._collection_name,
            meta,
            dim,
            distance,
            distributed=self._distributed,
        )
        og_coll._maintenance_work_mem_mb = self._maintenance_work_mem_mb
        self._configure_retrieval(og_coll)
        self._collection = Collection(og_coll)

        if "content" not in og_coll._get_all_columns():
            logger.warning(
                "openGauss collection %s has no content column; keyword search is unavailable. "
                "Existing schema is unchanged; grep engine=auto keeps the filesystem fallback.",
                self._collection_name,
            )

    def create_collection(
        self,
        name: str,
        schema: Dict[str, Any],
        *,
        distance: str,
        sparse_weight: float,
        index_name: str,
    ) -> bool:
        name = _validate_identifier(name, kind="openGauss collection name")
        if (
            self._table_exists(name)
            and _load_collection_meta(
                self._conn,
                name,
                distributed=self._distributed,
            )
            is None
        ):
            raise RuntimeError(
                f"openGauss table {name!r} exists without collection metadata; "
                "refusing to adopt an unverified orphan table"
            )
        self._initializing_collection = None
        try:
            return super().create_collection(
                name,
                schema,
                distance=distance,
                sparse_weight=sparse_weight,
                index_name=index_name,
            )
        except Exception:
            partial_collection = self._initializing_collection
            if self._collection is partial_collection:
                self._collection = None
            if partial_collection is not None:
                try:
                    partial_collection.drop()
                except Exception:
                    logger.exception(
                        "opengauss_adapter: failed to clean up partially created collection %s",
                        name,
                    )
            raise
        finally:
            self._initializing_collection = None

    def drop_collection(self) -> bool:
        if not self.collection_exists():
            return False
        self.get_collection().drop()
        self._collection = None
        return True

    def delete(
        self,
        *,
        ids: Optional[list[str]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        limit: Optional[int] = None,
    ) -> int:
        if ids is not None or filter is None:
            return super().delete(ids=ids, filter=filter)
        if limit is not None and limit <= 0:
            return 0

        collection = self.get_collection()
        compiled_filter = self._compile_filter(filter)
        collection_meta = collection.get_meta_data()
        array_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in collection_meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            in {"list<string>", "list<int64>"}
        }
        date_time_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in collection_meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            == "date_time"
        }

        def normalize_date_times(condition: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not condition:
                return condition
            normalized = dict(condition)
            conditions = normalized.get("conds")
            if normalized.get("op") in {"and", "or"} and isinstance(conditions, list):
                normalized["conds"] = [normalize_date_times(item) for item in conditions]
                return normalized
            if normalized.get("field") not in date_time_fields:
                return normalized
            if isinstance(conditions, list):
                normalized["conds"] = [_date_time_to_epoch_ms(value) for value in conditions]
            for bound in ("gt", "gte", "lt", "lte"):
                if normalized.get(bound) is not None:
                    normalized[bound] = _date_time_to_epoch_ms(normalized[bound])
            return normalized

        normalized_filter = normalize_date_times(compiled_filter)
        where_fragment, where_params = _build_where_clause(
            normalized_filter,
            array_fields,
        )
        if not where_fragment:
            return 0

        cursor = self._conn.cursor()
        try:
            cursor.execute(
                f'DELETE FROM "{self._collection_name}" '
                f"WHERE id IN ("
                f'SELECT id FROM "{self._collection_name}" '
                f"WHERE {where_fragment} ORDER BY id LIMIT %s"
                f") RETURNING id",
                where_params + [limit],
            )
            deleted_count = cursor.rowcount
            deleted_ids = [row[0] for row in cursor.fetchall()]
            raw = collection._Collection__collection
            if raw._has_sparse_column_on_cursor(cursor):
                raw._delete_sparse_postings_on_cursor(cursor, deleted_ids)
            self._conn.commit()
            return deleted_count
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()

    def update_data(self, data_list: List[Dict[str, Any]]) -> list[str]:
        normalized_records = [self._normalize_record_for_write(record) for record in data_list]
        result = self.get_collection().update_data(normalized_records)
        return list(result or [])

    def _existing_scalar_index_fields(self, og_coll: "OpenGaussCollection") -> list[str]:
        for source in (
            getattr(og_coll, "_indexes", {}).get(self._index_name),
            getattr(og_coll, "_pending_indexes", {}).get(self._index_name),
            getattr(og_coll, "_meta", None),
        ):
            if not isinstance(source, dict):
                continue
            fields = source.get("ScalarIndex")
            if isinstance(fields, list) and fields:
                return [str(field) for field in fields]
        return []

    def _ensure_vector_index_exists(self, og_coll: "OpenGaussCollection", distance: str = "cosine"):
        """Ensure the configured vector index exists and is registered.

        Presence of an arbitrary ANN index is not enough: reconnect must
        recover the configured name, operator class, and build options, and
        persist metadata when the physical index survived a failed catalog
        write. A matching physical index is registered in place; only a
        missing or conflicting index goes through create_index().
        """
        requested_meta = self._build_default_index_meta(
            index_name=self._index_name,
            distance=distance,
            use_sparse=bool(
                (
                    og_coll._indexes.get(self._index_name)
                    or og_coll._pending_indexes.get(self._index_name, {})
                ).get("SparseIndex")
            ),
            sparse_weight=getattr(self, "_sparse_weight", 0.0),
            scalar_index_fields=self._existing_scalar_index_fields(og_coll),
        )
        normalized_meta = og_coll._normalized_index_meta(self._index_name, requested_meta)
        existing_definition = og_coll._physical_index_definition(normalized_meta["_pg_index_name"])
        if existing_definition and og_coll._index_definition_matches(
            normalized_meta, existing_definition
        ):
            og_coll._apply_parallel_workers(normalized_meta)
            og_coll._persist_index_meta_and_scalar_indexes(
                self._index_name,
                normalized_meta,
            )
            og_coll._indexes[self._index_name] = normalized_meta
            og_coll._pending_indexes.pop(self._index_name, None)
            logger.info(
                "opengauss_adapter: recovered configured vector index %s",
                normalized_meta["_pg_index_name"],
            )
            return
        og_coll.create_index(self._index_name, requested_meta)
        logger.info("opengauss_adapter: vector index ensured via create_index")

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        if use_sparse and self._distributed:
            raise NotImplementedError("SPQ sparse indexes have not been certified")
        index_meta: Dict[str, Any] = {
            "IndexName": index_name,
            "VectorIndex": {
                "IndexType": self._index_type,
                "Distance": distance or self._distance_metric,
            },
            "ScalarIndex": scalar_index_fields,
            "build_params": dict(self._build_params),
            "search_params": dict(self._search_params),
            "maintenance_work_mem_mb": self._maintenance_work_mem_mb,
            "parallel_workers": self._parallel_workers,
        }
        if use_sparse:
            index_meta["SparseIndex"] = {"implementation": "postings", "weight": sparse_weight}
        return index_meta

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        fields = meta.get("Fields", [])
        dim = 0
        distance = self._distance_metric or "cosine"
        for f in fields:
            ftype = f.get("FieldType") or f.get("field_type") or f.get("type", "")
            if ftype == "vector":
                # Schema may use "Dim", "dimension", or "dim"
                dim = f.get("Dimension") or f.get("dimension") or f.get("Dim") or f.get("dim", 0)
        access_method = _index_access_method(self._index_type)
        if not _VECTOR_OPS.get(distance, {}).get(access_method):
            raise ValueError(
                f"openGauss index_type={self._index_type!r} does not support distance={distance!r}"
            )
        if distance == "l1" and _index_quantization(self._index_type) is not None:
            raise ValueError("openGauss distance='l1' requires plain hnsw without PQ or RabitQ")
        meta["_dim"] = dim
        meta["_distance"] = distance
        meta["_index_type"] = self._index_type
        self._collection_name = _validate_identifier(
            self._collection_name, kind="openGauss collection name"
        )

        table_created = False

        def mark_table_created() -> None:
            nonlocal table_created
            table_created = True

        try:
            _create_collection_table(
                self._conn,
                self._collection_name,
                meta,
                dim,
                distributed=self._distributed,
                shard_count=self._shard_count,
                on_table_created=mark_table_created,
            )
            meta = _save_collection_meta(
                self._conn,
                self._collection_name,
                meta,
                distributed=self._distributed,
            )
            og_coll = OpenGaussCollection(
                self._conn,
                self._collection_name,
                meta,
                dim,
                distance,
                distributed=self._distributed,
            )
            og_coll._maintenance_work_mem_mb = self._maintenance_work_mem_mb
            self._configure_retrieval(og_coll)
            wrapped = Collection(og_coll)
            self._initializing_collection = wrapped
            return wrapped
        except Exception:
            if not table_created:
                raise
            quoted_collection_name = _quote_identifier(
                self._collection_name,
                kind="openGauss collection name",
            )
            try:
                cursor = self._conn.cursor()
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {quoted_collection_name} CASCADE")
                    cursor.execute(
                        f'DELETE FROM "{_META_TABLE}" WHERE table_name = %s',
                        (self._collection_name,),
                    )
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    logger.exception(
                        "opengauss_adapter: failed to clean up orphan collection %s",
                        self._collection_name,
                    )
                finally:
                    cursor.close()
            finally:
                raise

    def begin_bulk_ingest(self) -> None:
        self.get_collection().begin_bulk_ingest()

    def end_bulk_ingest(self) -> None:
        self.get_collection().end_bulk_ingest()

    def close(self) -> None:
        super().close()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
