# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL + pgvector collection adapter.

openGauss ships a fork of the pgvector extension, so ``opengauss_adapter.py``
already emits pgvector-shaped SQL (the ``vector`` column type, the
``<=>``/``<#>``/``<->`` distance operators, ``USING hnsw``). This adapter is a
*re-target* of that module for stock PostgreSQL: it reuses the shared data plane
in :class:`~openviking.storage.vectordb_adapters.base.CollectionAdapter`, adds
``CREATE EXTENSION vector`` / DSN connect / ``ON CONFLICT`` upsert, and drops the
openGauss/Citus-only distributed-table machinery. See the line-by-line reuse map
in ``.wiki/pgvector/refs/02-opengauss-as-pgvector-reference.md``.

The pure SQL/identifier helpers (``_safe_identifier``, ``_normalize_distance``,
...) are reused verbatim from ``opengauss_adapter`` rather than duplicated; the
shared boundary that would host them lives behind issue #2357 (out of scope for
this build).

Built test-first: this lands the config model (B1) and factory wiring (B2); the
SQL ``ICollection`` and live connection seam are filled in over B3-B4.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    _VECTOR_OPS,
    OpenGaussCollection,
    _normalize_distance,
    _quote_ident,
    _safe_identifier,
    _vector_literal,
)

_DEFAULT_SCHEMA = "public"

# pgvector keeps its collection/index metadata in its own sidecar tables so a
# pgvector deployment never collides with an openGauss one in the same schema.
_COLLECTION_META_TABLE = "__openviking_pgvector_collections"
_INDEX_META_TABLE = "__openviking_pgvector_indexes"

# openGauss meta-table constants that the inherited collection SQL bakes into its
# statements; PgVectorCollection remaps them to the pgvector names above.
_OPENGAUSS_META_REMAP = {
    "__openviking_opengauss_collections": _COLLECTION_META_TABLE,
    "__openviking_opengauss_indexes": _INDEX_META_TABLE,
}


class PgVectorCollection(OpenGaussCollection):
    """SQL collection for PostgreSQL + pgvector.

    Re-targets :class:`OpenGaussCollection` (openGauss ships a pgvector fork, so
    the generated SQL is already pgvector-shaped). It inherits the read paths,
    filter compilation, and identifier/vector helpers verbatim, overriding only
    the parts where stock PostgreSQL diverges: its own metadata-table names (via
    ``_meta_table_ref``), native ``ON CONFLICT`` upsert, ``CREATE EXTENSION``
    ordering, and the per-query iterative-scan GUC bundle. Those overrides land
    in their respective build-loop slices (B3.3/B3.6/B3.8).
    """

    def update_data(self, data_list: List[Dict[str, Any]]) -> Any:
        """Update rows by primary key.

        For a SQL backend an update-by-id is exactly the ``INSERT ... ON CONFLICT
        (id) DO UPDATE`` upsert path, so ``update_data`` delegates to
        ``upsert_data``. (``ICollection.update_data`` is abstract; the inherited
        openGauss collection never implemented it, which is why pgvector must.)
        """
        return self.upsert_data(data_list)

    def _meta_table_ref(self, table_name: str) -> str:
        return super()._meta_table_ref(_OPENGAUSS_META_REMAP.get(table_name, table_name))

    def _build_create_ddl(self, meta_data: Dict[str, Any]) -> list[str]:
        """Return the ordered DDL for a collection: ``CREATE EXTENSION`` (when
        enabled) *before* the ``CREATE TABLE`` carrying the ``vector(N)`` column.

        Pure string builder (no connection) so the ordering and extension gate
        are unit-testable. Stock PostgreSQL needs the extension created before any
        ``vector(N)`` DDL; openGauss ships it natively so it never ran this.
        """
        columns = ["id TEXT PRIMARY KEY"]
        seen = {"id"}
        for field in meta_data.get("Fields", []) or []:
            ddl = self._field_to_column_ddl(field)
            field_name = field.get("FieldName")
            if ddl and field_name not in seen:
                columns.append(ddl)
                seen.add(str(field_name))
        for field_name, sql_type in self.INTERNAL_PATH_FIELDS.items():
            if field_name not in seen:
                columns.append(f"{_quote_ident(field_name)} {sql_type}")
                seen.add(field_name)

        statements: list[str] = []
        if getattr(self, "_create_extension", True):
            statements.append("CREATE EXTENSION IF NOT EXISTS vector")
        statements.append(f"CREATE TABLE IF NOT EXISTS {self._table_ref()} ({', '.join(columns)})")
        return statements

    def create_remote_collection(self, meta_data: Dict[str, Any]) -> None:
        self._meta = dict(meta_data)
        self._vector_dim = self._extract_vector_dim(self._meta)
        self._field_types = self._build_field_type_map(self._meta)
        if self._vector_dim <= 0:
            raise ValueError("pgvector collection requires a positive dense vector dimension")
        for statement in self._build_create_ddl(meta_data):
            self._execute(statement)
        self._save_collection_meta(meta_data)

    def _upsert_row(self, columns: List[str], values: List[Any]) -> None:
        """Single-row upsert via native ``INSERT ... ON CONFLICT (id) DO UPDATE``.

        Replaces the inherited openGauss UPDATE-then-INSERT + 23505 retry with the
        portable pgvector idiom: one atomic round trip. The dense-vector column is
        cast with ``%s::vector`` in VALUES, and ``EXCLUDED`` carries that typed
        value into the SET list. Identifiers are quoted with the shared
        ``_quote_ident`` (codebase idiom); the ``ON CONFLICT``/``EXCLUDED`` shape
        follows langchain-postgres / mem0 (see design.md provenance).
        """
        insert_cols = ", ".join(_quote_ident(column) for column in columns)
        placeholders = ", ".join(
            "%s::vector" if column == self._dense_vector_name else "%s" for column in columns
        )
        update_columns = [column for column in columns if column != "id"]
        if update_columns:
            set_clause = ", ".join(
                f"{_quote_ident(column)} = EXCLUDED.{_quote_ident(column)}"
                for column in update_columns
            )
            conflict = f"ON CONFLICT (id) DO UPDATE SET {set_clause}"
        else:
            conflict = "ON CONFLICT (id) DO NOTHING"
        self._execute(
            f"INSERT INTO {self._table_ref()} ({insert_cols}) VALUES ({placeholders}) {conflict}",
            values,
        )

    def _supports_iterative_scan(self) -> bool:
        """Whether the connected server has pgvector >= 0.8 (iterative scan).

        Wired by the version gate on connect (B4.2). Defaults to ``False`` so a
        pre-0.8 server falls back to the inherited plain scan rather than issuing
        GUCs it cannot parse (which would abort the transaction).
        """
        return bool(getattr(self, "_iterative_scan_supported", False))

    def _iterative_scan_guc_prefix(self) -> str:
        """The ``SET LOCAL`` bundle that keeps HNSW recall under a selective
        filter (metabase's tested shape). Values are inlined integers (clamped),
        never user input, so they compose safely ahead of the parameterized
        SELECT in one transaction.[^metabase]

        [^metabase]: metabase/metabase
        enterprise/backend/src/metabase_enterprise/semantic_search/index.clj
        (iterative_scan/ef_search[clamp 1..1000]/max_scan_tuples + enable_seqscan);
        Tencent/WeKnora gates it on version ("ignore failure on older pgvector").
        """
        ef_search = max(1, min(int(getattr(self, "_ef_search", 100)), 1000))
        max_scan_tuples = int(getattr(self, "_max_scan_tuples", 20000))
        return (
            "SET LOCAL hnsw.iterative_scan = strict_order; "
            f"SET LOCAL hnsw.ef_search = {ef_search}; "
            f"SET LOCAL hnsw.max_scan_tuples = {max_scan_tuples}; "
            "SET LOCAL enable_seqscan = off; "
        )

    def search_by_vector(
        self,
        index_name: str,
        dense_vector: list[float] | None = None,
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        sparse_vector: dict[str, float] | None = None,
        output_fields: list[str] | None = None,
    ) -> SearchResult:
        """Dense ANN search that issues the iterative-scan GUC bundle when a
        scalar filter is present (and the server supports it). Plain HNSW
        post-filters at most ``ef_search`` candidates, so a selective filter can
        silently return fewer than ``LIMIT`` rows; the bundle makes the scan keep
        pulling candidates until ``LIMIT`` is satisfied. Everything else (no
        filter, sparse/hybrid, unsupported server) delegates to the inherited
        implementation unchanged — the param order stays ``[vec, …where…, vec,
        limit, offset]`` (pinned by ``test_vector_search_binds_..._filter_params``).
        """
        if (
            dense_vector is None
            or sparse_vector
            or not filters
            or not self._supports_iterative_scan()
        ):
            return super().search_by_vector(
                index_name, dense_vector, limit, offset, filters, sparse_vector, output_fields
            )
        if limit <= 0:
            return SearchResult()
        fetch_limit = max(limit + offset, limit)
        columns = self._select_columns(output_fields, include_sparse=False)
        where_sql, params = self._where_sql(filters)
        operator = _VECTOR_OPS[self._distance_metric]["operator"]
        vector_text = _vector_literal(dense_vector)
        sql = self._iterative_scan_guc_prefix() + (
            f"SELECT {', '.join(_quote_ident(col) for col in columns)}, "
            f"{_quote_ident(self._dense_vector_name)} {operator} %s::vector AS _distance "
            f"FROM {self._table_ref()}"
            f"{where_sql} "
            f"ORDER BY {_quote_ident(self._dense_vector_name)} {operator} %s::vector "
            "LIMIT %s OFFSET %s"
        )
        rows = self._execute(sql, [vector_text, *params, vector_text, fetch_limit, 0], fetch=True)
        scored_items: list[SearchItemResult] = []
        for row in rows:
            record_id, payload = self._row_to_payload(row[:-1], columns)
            score = self._distance_to_score(row[-1], self._distance_metric)
            scored_items.append(SearchItemResult(id=record_id, fields=payload, score=score))
        return SearchResult(data=scored_items[offset : offset + limit])


class PgVectorCollectionAdapter(CollectionAdapter):
    """CollectionAdapter for PostgreSQL with the pgvector extension."""

    mode = "pgvector"
    INTERNAL_PATH_FIELDS = ["parent_uri", "scope_roots", "uri_depth"]

    def __init__(
        self,
        *,
        url: str | None,
        host: str,
        port: int,
        user: str,
        password: str,
        db_name: str,
        schema_name: str,
        sslmode: str,
        project_name: str,
        collection_name: str,
        index_name: str,
        distance_metric: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        connect_timeout: int,
        pool_size: int,
        create_extension: bool,
        index_type: str,
        index_params: Dict[str, Any],
        dimension: int = 0,
    ) -> None:
        super().__init__(collection_name=collection_name, index_name=index_name)
        self._url = url
        self._host = host
        self._port = int(port)
        self._user = user
        self._password = password
        self._db_name = db_name
        self._schema_name = (schema_name or _DEFAULT_SCHEMA).strip() or _DEFAULT_SCHEMA
        self._sslmode = sslmode
        self._project_name = project_name
        self._distance_metric = _normalize_distance(distance_metric)
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._connect_timeout = int(connect_timeout)
        self._pool_size = int(pool_size)
        self._create_extension = bool(create_extension)
        self._index_type = index_type
        self._index_params = dict(index_params or {})
        self._dimension = int(dimension)
        self._conn = None
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, config: Any) -> "PgVectorCollectionAdapter":
        cfg = getattr(config, "pgvector", None)
        params = dict(getattr(config, "custom_params", {}) or {})
        if cfg is None:
            raise ValueError("pgvector backend requires pgvector config")
        return cls(
            url=(getattr(cfg, "url", None) or params.get("url") or None),
            host=str(getattr(cfg, "host", None) or params.get("host") or "127.0.0.1"),
            port=int(getattr(cfg, "port", None) or params.get("port") or 5432),
            user=str(getattr(cfg, "user", None) or params.get("user") or "postgres"),
            password=str(getattr(cfg, "password", None) or params.get("password") or ""),
            db_name=str(getattr(cfg, "db_name", None) or params.get("db_name") or "postgres"),
            schema_name=str(
                getattr(cfg, "schema_name", None)
                or getattr(cfg, "schema", None)
                or params.get("schema")
                or _DEFAULT_SCHEMA
            ),
            sslmode=str(getattr(cfg, "sslmode", None) or params.get("sslmode") or "prefer"),
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
            index_name=config.index_name or "default",
            distance_metric=config.distance_metric or "cosine",
            dense_vector_name=str(
                getattr(cfg, "dense_vector_name", None)
                or params.get("dense_vector_name")
                or "vector"
            ),
            sparse_vector_name=str(
                getattr(cfg, "sparse_vector_name", None)
                or params.get("sparse_vector_name")
                or "sparse_vector"
            ),
            connect_timeout=int(
                getattr(cfg, "connect_timeout", None) or params.get("connect_timeout") or 10
            ),
            pool_size=int(getattr(cfg, "pool_size", None) or params.get("pool_size") or 1),
            create_extension=bool(getattr(cfg, "create_extension", True)),
            index_type=str(getattr(cfg, "index_type", None) or params.get("index_type") or "hnsw"),
            index_params=dict(getattr(cfg, "index_params", None) or {}),
            dimension=int(getattr(config, "dimension", 0) or 0),
        )

    @property
    def physical_table_name(self) -> str:
        return _safe_identifier(self._project_name, self._collection_name, prefix="ov")

    def _load_existing_collection_if_needed(self) -> None:
        raise NotImplementedError

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raise NotImplementedError
