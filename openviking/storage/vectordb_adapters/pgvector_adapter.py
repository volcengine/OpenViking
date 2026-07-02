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
from typing import Any, Dict

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb_adapters.base import CollectionAdapter
from openviking.storage.vectordb_adapters.opengauss_adapter import (
    OpenGaussCollection,
    _normalize_distance,
    _safe_identifier,
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

    def _meta_table_ref(self, table_name: str) -> str:
        return super()._meta_table_ref(_OPENGAUSS_META_REMAP.get(table_name, table_name))


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
