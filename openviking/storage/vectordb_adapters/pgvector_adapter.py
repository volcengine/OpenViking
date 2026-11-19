# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL pgvector backend.

Reuse the wire-compatible SQL CRUD/filter and sparse posting implementation,
with PostgreSQL-specific index lifecycle and session settings. Dense vectors
remain full precision; flat search supports dimensions above the HNSW limit.
"""

from __future__ import annotations

import json
import os
import re
import threading

from openviking.observability.log_safety import short_hash
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import PgvectorConfig

from .opengauss.catalog import _create_collection_table, _ensure_meta_table, _load_collection_meta
from .opengauss.collection import OpenGaussCollection, _PgIndex
from .opengauss.connection import _import_psycopg2, _PooledConnectionProxy
from .opengauss.retrieval import _validate_search_parameters
from .opengauss.sql import (
    _META_TABLE,
    _bounded_identifier,
    _build_where_clause,
    _distance_to_similarity,
    _quote_identifier,
    _validate_identifier,
    _validate_vector,
)
from .opengauss_adapter import OpenGaussCollectionAdapter, _save_collection_meta

logger = get_logger(__name__)

_SPARSE_FREQUENCY_PROBE_ROWS = 4096
_SPARSE_FREQUENCY_PROBE_MAX_ROWS = 65536
_SPARSE_SCOPE_MAX_ROWS = 100000


class PgvectorCollection(OpenGaussCollection):
    """SQL collection with flat or native pgvector HNSW dense search."""

    def _sparse_candidate_ctes(
        self, table, postings, condition, filter_params, sparse, has_dense, index_name, budget
    ):
        # Exact flat dense search disables indexscan for the entire statement.
        # The scoped lookup relies on an index covering both id and term.
        if condition == "TRUE" or (
            has_dense and self._indexes[index_name]["_pg_index_type"] == "flat"
        ):
            return super()._sparse_candidate_ctes(
                table, postings, condition, filter_params, sparse, has_dense, index_name, budget
            )
        # Keep routing in the ranking statement's MVCC snapshot. These probes
        # choose an execution strategy, never truncate the actual candidate set.
        # Raise the frequency gate with table size: a moderately frequent term
        # should not pay for a full main-table scope scan. Stale ANALYZE stats
        # affect strategy only; every chosen route applies the complete filter.
        terms = list(sparse)
        probe_rows = _SPARSE_FREQUENCY_PROBE_ROWS
        scope_max = _SPARSE_SCOPE_MAX_ROWS
        vector_guard = ' AND "vector" IS NOT NULL' if has_dense else ""
        ctes = [
            "sparse_probe_size AS MATERIALIZED (SELECT "
            f"GREATEST({probe_rows}, LEAST({_SPARSE_FREQUENCY_PROBE_MAX_ROWS}, "
            "CEIL(COALESCE((SELECT reltuples FROM pg_catalog.pg_class "
            "WHERE oid = %s::regclass), 0) * 0.05)))::integer AS n)",
            f"sparse_frequency AS MATERIALIZED (SELECT 1 FROM {postings} "
            "WHERE term = ANY(%s::text[]) LIMIT (SELECT n FROM sparse_probe_size))",
            f"sparse_scope AS MATERIALIZED (SELECT id FROM {table} "
            f"WHERE {condition}{vector_guard} LIMIT "
            "(SELECT CASE WHEN count(*) = (SELECT n FROM sparse_probe_size) "
            f"THEN {scope_max + 1} ELSE 0 END "
            "FROM sparse_frequency))",
            "sparse_scope_size AS MATERIALIZED (SELECT count(*) AS n FROM sparse_scope)",
            "sparse_route AS MATERIALIZED (SELECT CASE "
            "WHEN (SELECT count(*) FROM sparse_frequency) < "
            "(SELECT n FROM sparse_probe_size) THEN FALSE "
            f"WHEN n > {scope_max} THEN FALSE "
            "WHEN n <= (SELECT n/2 FROM sparse_probe_size) THEN TRUE "
            f"ELSE (SELECT count(*) FROM (SELECT 1 FROM {postings} "
            "WHERE term = ANY(%s::text[]) LIMIT (SELECT 2*n FROM sparse_scope_size)"
            ") frequency) >= 2*n END AS scoped FROM sparse_scope_size)",
            "sparse_matches AS ("
            f"SELECT p.id, p.weight * q.weight AS score FROM sparse_scope s "
            f"CROSS JOIN LATERAL (SELECT id, term, weight FROM {postings} "
            "WHERE id = s.id AND term = ANY(%s::text[]) OFFSET 0) p "
            "JOIN query_sparse q ON p.term = q.term "
            "WHERE (SELECT scoped FROM sparse_route) UNION ALL "
            f"SELECT p.id, p.weight * q.weight AS score FROM {postings} p "
            "JOIN query_sparse q ON p.term = q.term "
            "WHERE NOT (SELECT scoped FROM sparse_route) "
            f'AND EXISTS (SELECT 1 FROM {table} WHERE "id" = p.id '
            f"AND {condition}{vector_guard}))",
            "sparse_candidates AS (SELECT id AS _ov_candidate_id FROM sparse_matches "
            "GROUP BY id HAVING SUM(score) > 0 ORDER BY SUM(score) DESC, id LIMIT %s)",
        ]
        return ctes, [table, terms, *filter_params, terms, terms, *filter_params, budget]

    def _normalized_index_meta(self, index_name, meta_data):
        meta = dict(meta_data)
        vector = dict(meta.get("VectorIndex") or {})
        index_type = vector.get("IndexType", "flat")
        if index_type not in {"flat", "hnsw"}:
            raise ValueError("pgvector supports flat and hnsw indexes")
        if index_type == "hnsw" and self._dim > 2000:
            raise ValueError("pgvector HNSW supports at most 2000 vector dimensions")
        metric = vector.get("Distance", self._distance)
        ops = {
            "cosine": "vector_cosine_ops",
            "l2": "vector_l2_ops",
            "ip": "vector_ip_ops",
            "l1": "vector_l1_ops",
        }
        if metric not in ops:
            raise ValueError(f"Unsupported pgvector distance: {metric}")
        vector.update(IndexType=index_type, Distance=metric)
        build = dict(meta.get("build_params") or {})
        search = dict(meta.get("search_params") or {})
        if set(build) - {"m", "ef_construction"} or set(search) - {"ef_search"}:
            raise ValueError("Unsupported pgvector index parameters")
        validated = PgvectorConfig(**build, **search)
        meta["build_params"] = {
            "m": validated.m,
            "ef_construction": validated.ef_construction,
        }
        meta["search_params"] = {"ef_search": validated.ef_search}
        meta.update(
            IndexName=index_name,
            VectorIndex=vector,
            _distance=metric,
            _pg_index_type=index_type,
            _logical_index_type=index_type,
            _quantization=None,
            _operator_class=ops[metric],
            _pg_index_name=_bounded_identifier(f"idx_{self._name}_{index_name}_vec")
            if index_type == "hnsw"
            else None,
        )
        return meta

    def _load_index_meta(self):
        # The openGauss metadata migrator validates DataVec-only index options.
        try:
            rows = self._execute(
                f"SELECT index_name, meta_json FROM {_quote_identifier(self._index_catalog_table())}",
                fetch=True,
            )
        except Exception as error:
            if getattr(error, "pgcode", None) == "42P01":
                return
            raise
        for name, value in rows:
            self._indexes[name] = self._normalized_index_meta(name, json.loads(value))

    def _reconcile_index_metadata(self):
        for name in list(self._indexes):
            if not self.has_index(name):
                self._indexes.pop(name, None)

    def has_index(self, index_name):
        meta = self._indexes.get(index_name)
        if not meta:
            return False
        if meta["_pg_index_type"] == "flat":
            return True
        definition = self._physical_index_definition(meta["_pg_index_name"])
        return bool(definition and self._index_identity_matches(meta, definition))

    def _physical_index_definition(self, physical_index_name):
        # Reject incomplete concurrent builds, partial indexes and expression
        # indexes: their presence does not certify this collection's ANN index.
        rows = self._execute(
            """
            SELECT pg_get_indexdef(i.indexrelid)
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_class x ON x.oid = i.indexrelid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = i.indkey[0]
            WHERE n.nspname = current_schema() AND t.relname = %s
              AND x.relname = %s AND i.indisvalid AND i.indisready
              AND i.indpred IS NULL AND i.indexprs IS NULL
              AND i.indnatts = 1
              AND a.attname = 'vector'
            """,
            (self._name, physical_index_name),
            fetch=True,
        )
        return rows[0][0] if rows else None

    def create_index(self, index_name, meta_data):
        _validate_identifier(index_name)
        meta = self._normalized_index_meta(index_name, meta_data)
        if meta.get("SparseIndex"):
            self.create_sparse_index(
                candidate_limit=self._retrieval_config["candidate_limit"],
                sparse_weight=meta["SparseIndex"]["weight"],
            )
        if meta["_pg_index_type"] == "hnsw":
            existing = self._physical_index_definition(meta["_pg_index_name"])
            if existing and not self._index_identity_matches(meta, existing):
                raise RuntimeError("pgvector existing index has incompatible operator class")
            if not existing:
                build = meta["build_params"]
                m, ef = build.get("m", 16), build.get("ef_construction", 64)
                self._execute(
                    f"CREATE INDEX {_quote_identifier(meta['_pg_index_name'])} "
                    f"ON {_quote_identifier(self._name)} USING hnsw "
                    f"(vector {meta['_operator_class']}) "
                    f"WITH (m = {int(m)}, ef_construction = {int(ef)})"
                )
        self._persist_index_meta_and_scalar_indexes(index_name, meta)
        self._indexes[index_name] = meta
        return _PgIndex(index_name, meta)

    def _apply_search_params_on_cursor(self, cursor, index_name):
        meta = self._indexes[index_name]
        if meta["_pg_index_type"] == "flat":
            # Flat means exact even if another administrator added an ANN index.
            cursor.execute("SET LOCAL enable_indexscan = off")
        else:
            ef = int(meta["search_params"].get("ef_search", 100))
            cursor.execute(f"SET LOCAL hnsw.ef_search = {ef}")
            cursor.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")

    def upsert_data(self, data_list, ttl=0):
        if ttl:
            raise NotImplementedError("pgvector TTL writes are not supported")
        return super().upsert_data(data_list, ttl=0)

    def search_by_vector(
        self,
        index_name,
        dense_vector=None,
        limit=10,
        offset=0,
        filters=None,
        sparse_vector=None,
        output_fields=None,
        advance=None,
        return_detail_info=False,
    ):
        if dense_vector and limit and not self.has_index(index_name):
            raise FailedPreconditionError(f"pgvector index {index_name!r} is missing or invalid")
        if sparse_vector or advance is not None:
            return super().search_by_vector(
                index_name,
                dense_vector,
                limit,
                offset,
                filters,
                sparse_vector,
                output_fields,
                advance,
                return_detail_info,
            )
        _validate_search_parameters(limit, offset, output_fields)
        self._validate_ranked_filters(filters)
        if dense_vector is None or dense_vector == [] or limit == 0:
            return SearchResult(data=[])
        vector = _validate_vector(dense_vector, self._dim)
        metric, op = self._resolve_distance_and_op(index_name)
        where, params = _build_where_clause(
            self._normalize_filter_date_times(filters), self._array_fields
        )
        condition = f"({where}) AND " if where else ""
        columns, rows = self._query_rows(
            f"SELECT {self._select_output_columns(output_fields)}, "
            f"vector {op} %s::vector AS _ov_distance "
            f"FROM {_quote_identifier(self._name)} "
            f"WHERE {condition}vector IS NOT NULL "
            f"ORDER BY vector {op} %s::vector, id LIMIT %s OFFSET %s",
            [str(vector), *params, str(vector), limit, offset],
            index_name,
        )
        items = []
        for row in rows:
            record = self._row_to_dict(row[:-1], columns[:-1])
            items.append(
                SearchItemResult(
                    id=record.pop("id"),
                    fields=record,
                    score=_distance_to_similarity(metric, row[-1]),
                )
            )
        return SearchResult(data=items)


class PgvectorCollectionAdapter(OpenGaussCollectionAdapter):
    """pgvector backend using PostgreSQL MVCC and pooled transactions."""

    mode = "pgvector"
    USE_CONTENT_FIELD = True
    _STRING_FIELD_BYTE_LIMIT = None
    _TEXT_FIELD_BYTE_LIMIT = None

    def __init__(
        self,
        collection_name,
        config: PgvectorConfig,
        index_name="default",
        distance_metric="cosine",
    ):
        CollectionAdapter.__init__(self, _validate_identifier(collection_name), index_name)
        self._config = config
        self._distance_metric = distance_metric
        self._distributed = False
        self._shard_count = 0
        self._index_type = config.index_type
        self._build_params = {"m": config.m, "ef_construction": config.ef_construction}
        self._search_params = {"ef_search": config.ef_search}
        self._maintenance_work_mem_mb = 64
        self._parallel_workers = 0
        self._retrieval_config = {"candidate_limit": config.candidate_limit}
        self._sparse_weight = 0.0
        self._load_lock = threading.RLock()
        self._connect()

    def _connect(self):
        dsn = os.environ.get(self._config.dsn_env)
        if not dsn:
            raise ValueError(f"Set PostgreSQL DSN environment variable {self._config.dsn_env}")
        try:
            driver = _import_psycopg2()
        except ImportError as error:
            raise ImportError(
                'Install the PostgreSQL driver: pip install "openviking[pgvector]"'
            ) from error
        self._pool = driver.pool.ThreadedConnectionPool(
            self._config.connection_pool_min_size,
            self._config.connection_pool_max_size,
            dsn=dsn,
            connect_timeout=10,
            options="-c client_encoding=UTF8 -c search_path=public",
        )
        self._conn = None
        try:
            self._conn = _PooledConnectionProxy(self._pool)
            cursor = self._conn.cursor()
            try:
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                version = cursor.fetchone()
                if version is None:
                    raise RuntimeError(
                        "Install pgvector and CREATE EXTENSION vector in this database"
                    )
                release = re.match(r"^(\d+)\.(\d+)\.(\d+)", version[0])
                if not release or tuple(map(int, release.groups())) < (0, 8, 0):
                    raise RuntimeError("OpenViking requires pgvector >= 0.8.0")
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("openviking.pgvector.catalog",),
                )
                _ensure_meta_table(self._conn)
            finally:
                cursor.close()
        except Exception:
            try:
                if self._conn is not None:
                    self._conn.close()
                else:
                    self._pool.closeall()
            finally:
                self._conn = None
                self._pool = None
            raise

    @classmethod
    def from_config(cls, config):
        if config.pgvector is None:
            raise ValueError("pgvector configuration is required")
        adapter = cls(
            config.name or "context",
            config.pgvector,
            config.index_name or "default",
            config.distance_metric,
        )
        adapter._sparse_weight = config.sparse_weight
        return adapter

    def _load_existing_collection_if_needed(self, *, require_index=True):
        with self._load_lock:
            if self._collection is not None:
                return
            meta = _load_collection_meta(self._conn, self._collection_name)
            if meta is None:
                return
            if not self._table_exists(self._collection_name):
                raise RuntimeError("pgvector collection metadata exists but table is missing")
            if meta["_distance"] != self._distance_metric:
                raise ValueError("pgvector collection distance differs from configured distance")
            if self._dimension and meta["_dim"] != self._dimension:
                raise ValueError("pgvector collection dimension differs from configured dimension")
            if meta.get("_backend") != "pgvector":
                raise ValueError("Collection metadata belongs to a different backend")
            wrapped = self._wrap_collection(meta)
            if not require_index:
                # Deleting a collection must not require a usable ANN index.
                self._collection = wrapped
                return
            index = wrapped.get_index_meta_data(self._index_name)
            if not index:
                raise RuntimeError(f"pgvector configured index {self._index_name!r} is missing")
            if index["_pg_index_type"] != self._index_type:
                raise ValueError("pgvector index_type differs from the persisted index")
            if index["_distance"] != self._distance_metric:
                raise ValueError("pgvector index distance differs from configured distance")
            if self._index_type == "hnsw" and index["build_params"] != self._build_params:
                raise ValueError("pgvector build parameters differ; rebuild the index first")
            self._collection = wrapped

    def drop_collection(self) -> bool:
        with self._load_lock:
            self._load_existing_collection_if_needed(require_index=False)
            return super().drop_collection()

    def create_collection(self, name, schema, *, distance, sparse_weight, index_name):
        # Session lock survives the catalog helper commits. Keeping this cursor
        # open pins the pool connection until initialization and unlock finish.
        _validate_identifier(name)
        if distance != self._distance_metric:
            raise ValueError("pgvector collection distance differs from configured distance")
        cursor = self._conn.cursor()
        try:
            cursor.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                ("openviking.pgvector.collection." + name,),
            )
            return super().create_collection(
                name,
                schema,
                distance=distance,
                sparse_weight=sparse_weight,
                index_name=index_name,
            )
        finally:
            try:
                self._conn.rollback()
                cursor.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    ("openviking.pgvector.collection." + name,),
                )
                self._conn.commit()
            finally:
                cursor.close()

    def _wrap_collection(self, meta):
        raw = PgvectorCollection(
            self._conn, self._collection_name, meta, meta["_dim"], self._distance_metric
        )
        self._configure_retrieval(raw)
        # Search settings can change without rebuilding the physical index.
        if self._index_name in raw._indexes:
            raw._indexes[self._index_name]["search_params"] = dict(self._search_params)
        return Collection(raw)

    def _create_backend_collection(self, meta):
        meta = dict(meta)
        dim = next(
            (
                f.get("Dim") or f.get("Dimension") or f.get("dimension") or 0
                for f in meta["Fields"]
                if f["FieldType"] == "vector"
            ),
            0,
        )
        if not 1 <= dim <= 16000:
            raise ValueError("pgvector vector dimension must be between 1 and 16000")
        if self._index_type == "hnsw" and dim > 2000:
            raise ValueError("pgvector HNSW supports at most 2000 vector dimensions; use flat")
        # PostgreSQL has no DataVec bm25 AM. Keep content for read/migration;
        # OpenViking grep uses its existing filesystem fallback for this backend.
        meta.pop("FullText", None)
        meta.update(_dim=dim, _distance=self._distance_metric, _backend="pgvector")
        self._initializing_collection = None
        table_created = False

        def mark_created():
            nonlocal table_created
            table_created = True

        try:
            _create_collection_table(
                self._conn,
                self._collection_name,
                meta,
                dim,
                on_table_created=mark_created,
            )
            meta = _save_collection_meta(self._conn, self._collection_name, meta)
            wrapped = self._wrap_collection(meta)
            self._initializing_collection = wrapped
            return wrapped
        except Exception:
            if table_created:
                try:
                    self._execute_cleanup()
                except Exception as _log_error:
                    logger.info(
                        "pgvector_adapter: failed to clean up partially created collection %s error_type=%s",
                        short_hash(str(self._collection_name)),
                        type(_log_error).__name__,
                    )
            raise

    def _execute_cleanup(self):
        cursor = self._conn.cursor()
        try:
            cursor.execute(f"DROP TABLE {_quote_identifier(self._collection_name)}")
            cursor.execute(
                f"DELETE FROM {_quote_identifier(_META_TABLE)} WHERE table_name = %s",
                (self._collection_name,),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()
