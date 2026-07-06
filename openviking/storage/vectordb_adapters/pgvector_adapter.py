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

import hashlib
import threading
from typing import Any, Dict, List, Optional

from packaging.version import InvalidVersion, Version

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    _VECTOR_OPS,
    OpenGaussCollection,
    OpenGaussCollectionAdapter,
    _json_dumps,
    _normalize_distance,
    _qualify,
    _quote_ident,
    _safe_identifier,
    _vector_literal,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_DEFAULT_SCHEMA = "public"


def _coerce_int(value: Any, key: str, default: int) -> int:
    """Coerce a user-supplied ``index_params`` value to ``int`` with an actionable
    error, instead of letting a bare ``int(...)`` raise an opaque ``ValueError``
    deep inside collection construction."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"pgvector index_params[{key!r}] must be an integer, got {value!r}"
        ) from exc


# Stable 64-bit advisory-lock key so concurrent workers serialize the one-time
# ``CREATE EXTENSION`` instead of racing (onyx-dot-app/onyx: sha256(name) -> int64).
_EXTENSION_ADVISORY_KEY = int.from_bytes(
    hashlib.sha256(b"openviking.pgvector.create_extension").digest()[:8], "big", signed=True
)


def _import_psycopg2():
    try:
        import psycopg2  # noqa: PLC0415
        import psycopg2.pool  # noqa: PLC0415  # submodule is not auto-imported by `import psycopg2`

        return psycopg2
    except ImportError as exc:  # pragma: no cover - exercised only without optional driver
        raise ImportError(
            "The pgvector backend requires a psycopg2-compatible driver. "
            'Install it with `pip install "openviking[pgvector]"`.'
        ) from exc


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

    # Knobs threaded from the adapter in ``_new_collection``; the class defaults
    # keep ``object.__new__`` Tier-C construction (and mypy) happy.
    _create_extension: bool = True
    _iterative_scan_supported: bool = False
    _ef_search: int = 100
    _max_scan_tuples: int = 20000

    def update_data(self, data_list: List[Dict[str, Any]]) -> Any:
        """Update existing rows by primary key (never insert new ones).

        The ``/data/update`` endpoint is contractually "update existing data",
        and other backends (e.g. ``LocalCollection.update_data``) reject unknown
        ids rather than silently inserting. So pgvector first verifies every id
        already exists, then delegates to the ``INSERT ... ON CONFLICT (id) DO
        UPDATE`` upsert path — which, because it only sets the supplied columns,
        gives the desired "update only the provided fields" semantics for rows
        that are present. (``ICollection.update_data`` is abstract; the inherited
        openGauss collection never implemented it, which is why pgvector must.)
        """
        if not data_list:
            return self.upsert_data(data_list)
        ids = []
        for record in data_list:
            if "id" not in record:
                raise ValueError("primary key 'id' is required for update")
            ids.append(record["id"])
        missing = self.fetch_data(ids).ids_not_exist
        if missing:
            raise ValueError(f"record not found for primary key(s): {missing}")
        return self.upsert_data(data_list)

    def _meta_table_ref(self, table_name: str) -> str:
        return super()._meta_table_ref(_OPENGAUSS_META_REMAP.get(table_name, table_name))

    def _save_collection_meta(self, meta: Dict[str, Any]) -> None:
        """Persist collection metadata with portable ``INSERT ... ON CONFLICT``.

        Overrides the inherited openGauss ``MERGE INTO`` upsert, which requires
        PostgreSQL 15+. ``ON CONFLICT (table_name) DO UPDATE`` is supported on all
        pgvector-capable PostgreSQL (>= 9.5), matching the ``_upsert_row`` idiom.
        """
        self._execute(
            f"""
            INSERT INTO {self._meta_table_ref(_COLLECTION_META_TABLE)}
                (table_name, logical_collection_name, project_name, meta_json, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (table_name) DO UPDATE SET
                logical_collection_name = EXCLUDED.logical_collection_name,
                project_name = EXCLUDED.project_name,
                meta_json = EXCLUDED.meta_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                self.collection_key,
                self._logical_collection_name,
                self._project_name,
                _json_dumps(meta),
            ],
        )

    def _save_index_meta(self, index_name: str, meta: Dict[str, Any]) -> None:
        """Portable ``INSERT ... ON CONFLICT`` index-meta upsert (see
        ``_save_collection_meta`` — avoids the PG15-only ``MERGE INTO``)."""
        self._execute(
            f"""
            INSERT INTO {self._meta_table_ref(_INDEX_META_TABLE)}
                (table_name, index_name, meta_json, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (table_name, index_name) DO UPDATE SET
                meta_json = EXCLUDED.meta_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            [self.collection_key, index_name, _json_dumps(meta)],
        )

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
        if self._create_extension:
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
        return self._iterative_scan_supported

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
        ef_search = max(1, min(self._ef_search, 1000))
        max_scan_tuples = self._max_scan_tuples
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


class PgVectorCollectionAdapter(OpenGaussCollectionAdapter):
    """CollectionAdapter for PostgreSQL with the pgvector extension.

    Re-targets :class:`OpenGaussCollectionAdapter` (symmetric with
    :class:`PgVectorCollection` re-targeting :class:`OpenGaussCollection`). It
    inherits the adapter-level path plane verbatim — ``_normalize_record_for_write``
    (scope_roots/uri_depth augmentation), ``_normalize_record_for_read``,
    ``_sanitize_scalar_index_fields``, ``_build_default_index_meta``, and the
    ``PathScope`` filter compilation — and overrides only the connection/driver
    seam and collection construction (DSN connect, version gate, extension,
    pgvector meta tables, PgVectorCollection). The openGauss-only distributed
    helpers are inherited but never called.
    """

    mode = "pgvector"

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
        # Skip OpenGaussCollectionAdapter.__init__ (openGauss host/mode/shard_count
        # signature); pgvector sets its own connection state below.
        CollectionAdapter.__init__(self, collection_name=collection_name, index_name=index_name)
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
        self._conn: Any = None
        self._pool: Any = None
        self._lock = threading.RLock()
        # Feature flags resolved from the live extension version on connect
        # (_detect_version). Conservative defaults: everything off until proven.
        self._pgvector_version = ""
        self._supports_hnsw = False
        self._supports_halfvec = False
        self._supports_sparsevec = False
        self._supports_iterative_scan = False
        self._ready = False

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

    def _conninfo(self) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """psycopg2 ``connect`` args/kwargs. A DSN ``url`` wins over discrete
        host/port/user/password (mem0's priority chain); ``sslmode`` is injected
        for both forms."""
        if self._url:
            return (self._url,), {
                "sslmode": self._sslmode,
                "connect_timeout": self._connect_timeout,
            }
        return (), {
            "host": self._host,
            "port": self._port,
            "user": self._user,
            "password": self._password,
            "dbname": self._db_name,
            "sslmode": self._sslmode,
            "connect_timeout": self._connect_timeout,
        }

    def _connect(self):
        """Open (or reuse) a psycopg2 connection.

        ``pool_size > 1`` uses a ``ThreadedConnectionPool``; otherwise a single
        lock-serialized connection (openGauss's model). ``register_vector`` is
        intentionally *never* called: the reused ``%s::vector`` literal path needs
        no driver-level type binding, so a plain psycopg2 works (mem0 ships this
        way). If a deployment later opts into binding, it would run per
        checked-out connection right here. The ``_import_psycopg2`` seam is
        module-level so tests can mock the driver without a live server.
        """
        if self._conn is not None and not getattr(self._conn, "closed", 0):
            return self._conn
        if self._conn is not None:
            try:
                # A pooled handle must be returned via putconn (close=True), not
                # merely .close()'d, or its slot leaks and the pool exhausts.
                if self._pool is not None:
                    self._pool.putconn(self._conn, close=True)
                else:
                    self._conn.close()
            except Exception:
                logger.debug("Failed to release stale pgvector connection", exc_info=True)
            self._conn = None
        psycopg2 = _import_psycopg2()
        args, kwargs = self._conninfo()
        if self._pool_size > 1:
            if self._pool is None:
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    1, self._pool_size, *args, **kwargs
                )
            self._conn = self._pool.getconn()
        else:
            self._conn = psycopg2.connect(*args, **kwargs)
        return self._conn

    def _gate_features(self, extversion: str) -> None:
        """Resolve feature flags from a pgvector ``extversion`` string.

        HNSW indexing needs >= 0.5.0; ``halfvec``/``sparsevec`` need >= 0.7.0;
        the ``hnsw.iterative_scan`` GUC needs >= 0.8.0. An unparseable version
        leaves every flag off (conservative).[^raglite]

        [^raglite]: superlinear-ai/raglite gates iterative scan on the parsed
        ``extversion`` the same way.
        """
        self._pgvector_version = str(extversion or "")
        try:
            version: Version | None = Version(self._pgvector_version)
        except (InvalidVersion, TypeError):
            version = None
        self._supports_hnsw = version is not None and version >= Version("0.5.0")
        self._supports_halfvec = version is not None and version >= Version("0.7.0")
        self._supports_sparsevec = self._supports_halfvec
        self._supports_iterative_scan = version is not None and version >= Version("0.8.0")

    def _detect_version(self) -> None:
        """Read the live pgvector version and gate features on it. Raises a
        friendly error if the server predates HNSW support (< 0.5.0)."""
        conn = self._conn
        if conn is None:
            return
        cur = conn.cursor()
        try:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cur.fetchone()
        finally:
            cur.close()
        extversion = row[0] if row else None
        if not extversion:
            # No pg_extension row => the 'vector' extension is not installed. Fail
            # fast with an actionable message instead of proceeding to opaque
            # vector(N)/index DDL errors. (With create_extension=true the extension
            # was just created above, so this only trips on pre-provisioned
            # deployments where it is genuinely absent.)
            raise RuntimeError(
                "The pgvector 'vector' extension is not installed in this database. "
                "Have a superuser run `CREATE EXTENSION vector;`, or set "
                "`create_extension=true` so OpenViking installs it on connect."
            )
        self._gate_features(str(extversion))
        if not self._supports_hnsw:
            raise RuntimeError(
                f"pgvector {extversion} is too old for OpenViking; HNSW indexing requires "
                ">= 0.5.0. Upgrade the extension (e.g. `ALTER EXTENSION vector UPDATE`)."
            )

    def _extension_present(self) -> bool:
        conn = self._conn
        if conn is None:
            return False
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            return cur.fetchone() is not None
        finally:
            cur.close()

    def _ensure_extension(self) -> None:
        """Create the ``vector`` extension on connect, under an advisory lock so
        concurrent workers don't race. If it fails (e.g. a least-privileged role
        on managed PostgreSQL), re-check ``pg_extension`` — another worker or a
        DBA may have installed it — and only then raise a literal remediation.
        ``create_extension=False`` skips this entirely for pre-provisioned
        deployments (RDS/Cloud SQL).[^flag]

        [^flag]: open-webui / danny-avila/rag_api gate this on a
        ``PGVECTOR_CREATE_EXTENSION`` flag; onyx-dot-app/onyx serializes it with
        an advisory lock; serengil/deepface raises the actionable command.
        """
        if not self._create_extension:
            return
        conn = self._conn
        if conn is None:
            return
        cur = conn.cursor()
        try:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [_EXTENSION_ADVISORY_KEY])
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if not self._extension_present():
                raise RuntimeError(
                    "Failed to enable the pgvector 'vector' extension and it is not "
                    "installed. Have a superuser run `CREATE EXTENSION vector;`, or set "
                    "`create_extension=false` if the extension is pre-provisioned."
                ) from exc
        finally:
            cur.close()

    def _ensure_meta_tables(self) -> None:
        """Create the pgvector-named sidecar metadata tables (openGauss's schema,
        pgvector names). The collection reads/writes these via ``_meta_table_ref``,
        which remaps the inherited openGauss constants to these names."""
        conn = self._conn
        if conn is None:
            return
        cur = conn.cursor()
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(self._schema_name)}")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_qualify(self._schema_name, _COLLECTION_META_TABLE)} (
                    table_name TEXT PRIMARY KEY,
                    logical_collection_name TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_qualify(self._schema_name, _INDEX_META_TABLE)} (
                    table_name TEXT NOT NULL,
                    index_name TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (table_name, index_name)
                )
                """
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    def _ensure_ready(self) -> None:
        """Connect and run the one-time bootstrap (extension → version gate →
        meta tables). Idempotent: the bootstrap runs once per adapter lifetime,
        but ``_connect`` still reconnects a dropped connection on every call.

        The bootstrap runs DDL and metadata reads on the shared psycopg2
        connection, which is not safe for overlapping cursor use across threads,
        so the whole block is serialized under ``self._lock`` (an RLock shared
        with collection ``_execute``; reentrant, so nested acquisition is safe).
        ``_ready`` is re-checked inside the lock so only the first thread bootstraps.
        """
        with self._lock:
            self._connect()
            if self._ready:
                return
            self._ensure_extension()
            self._detect_version()
            self._ensure_meta_tables()
            self._ready = True

    def _new_collection(self, meta: Optional[Dict[str, Any]] = None) -> PgVectorCollection:
        self._ensure_ready()
        collection = PgVectorCollection(
            conn=self._conn,
            schema_name=self._schema_name,
            table_name=self.physical_table_name,
            logical_collection_name=self._collection_name,
            project_name=self._project_name,
            dense_vector_name=self._dense_vector_name,
            sparse_vector_name=self._sparse_vector_name,
            distance_metric=self._distance_metric,
            meta=meta,
            lock=self._lock,
            reconnect=self._connect,
        )
        # Thread the adapter-resolved knobs onto the live collection so the
        # CREATE EXTENSION (B3.3) and iterative-scan GUC (B3.8) overrides fire.
        collection._create_extension = self._create_extension
        collection._iterative_scan_supported = self._supports_iterative_scan
        collection._ef_search = _coerce_int(self._index_params.get("ef_search"), "ef_search", 100)
        collection._max_scan_tuples = _coerce_int(
            self._index_params.get("max_scan_tuples"), "max_scan_tuples", 20000
        )
        return collection

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        """Carry the configured HNSW build parameters into the default index meta.

        ``CollectionAdapter.create_collection`` builds a fresh index meta and the
        inherited implementation ignores ``index_params``, so ``m``/``ef_construction``
        from ``PgVectorConfig.index_params`` would otherwise never reach the HNSW
        DDL (which reads ``VectorIndex.M`` / ``VectorIndex.EfConstruction``,
        defaulting to 16/64). Merge them here, accepting either casing.
        """
        index_meta = super()._build_default_index_meta(
            index_name=index_name,
            distance=distance,
            use_sparse=use_sparse,
            sparse_weight=sparse_weight,
            scalar_index_fields=scalar_index_fields,
        )
        vector_index = index_meta["VectorIndex"]
        m = self._index_params.get("m", self._index_params.get("M"))
        if m is not None:
            vector_index["M"] = _coerce_int(m, "m", 16)
        ef_construction = self._index_params.get(
            "ef_construction", self._index_params.get("EfConstruction")
        )
        if ef_construction is not None:
            vector_index["EfConstruction"] = _coerce_int(ef_construction, "ef_construction", 64)
        return index_meta

    def close(self) -> None:
        """Release the wrapped collection *and* the pgvector connection/pool.

        The inherited openGauss ``close()`` only ``.close()``'s ``self._conn`` —
        wrong for a pooled handle (which must be returned via ``putconn``) and it
        never closes the pool object, leaking server connections. This override
        closes the collection like the base adapter, returns/borrowed pooled conn,
        and tears the pool down.
        """
        with self._lock:
            try:
                CollectionAdapter.close(self)
            finally:
                try:
                    if self._pool is not None:
                        if self._conn is not None:
                            self._pool.putconn(self._conn, close=True)
                        self._pool.closeall()
                    elif self._conn is not None:
                        self._conn.close()
                except Exception:
                    logger.debug("Failed to close pgvector connection/pool", exc_info=True)
                finally:
                    self._conn = None
                    self._pool = None
                    self._ready = False

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        raw_collection = self._new_collection()
        if not raw_collection._table_exists():
            return
        meta = raw_collection.get_meta_data()
        if not meta:
            raise RuntimeError(
                "pgvector collection table exists but OpenViking metadata is missing: "
                f"{self.physical_table_name}. Use a different project/name, restore metadata, "
                "or drop the stale table."
            )
        self._collection = Collection(self._new_collection(meta))

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raw_collection = self._new_collection(meta)
        raw_collection.create_remote_collection(meta)
        return Collection(raw_collection)
