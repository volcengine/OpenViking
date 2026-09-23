# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQL-backed collection CRUD, search and index lifecycle."""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, List, Optional

from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.index.index import IIndex
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.vectordb_config import (
    OpenGaussIndexConfig,
)

from .catalog import (
    _try_make_metadata_table_distributed,
)
from .metadata import BUILD_KEYS, SEARCH_KEYS, migrate_persisted_index_meta
from .sql import (
    _DISTANCE_OP,
    _META_TABLE,
    _QUANTIZATION_OPTION_NAMES,
    _UNSTORED_FIELDS,
    _VECTOR_OPS,
    _bounded_identifier,
    _build_where_clause,
    _coerce_sparse_vector,
    _coerce_vector,
    _date_time_to_epoch_ms,
    _distance_to_similarity,
    _index_access_method,
    _index_meta_table_name,
    _index_quantization,
    _is_undefined_table_error,
    _normalize_index_type,
    _quote_identifier,
    _resolve_build_params,
    _resolve_search_params,
    _validate_identifier,
    _validate_vector,
)

logger = get_logger(__name__)


class _PgIndex(IIndex):
    """Dummy IIndex implementation for OpenGauss.

    This class provides metadata-only index operations. The actual vector search
    is performed directly via SQL queries in OpenGaussCollection.
    """

    def __init__(self, name: str, meta: Dict[str, Any]):
        self._name = name
        self._meta = meta

    def get_name(self) -> str:
        return self._name

    def get_meta_data(self) -> Dict[str, Any]:
        return dict(self._meta)

    def upsert_data(self, delta_list):
        """Not used - data operations handled by OpenGaussCollection."""
        pass

    def delete_data(self, delta_list):
        """Not used - data operations handled by OpenGaussCollection."""
        pass

    def search(
        self, query_vector=None, limit=10, filters=None, sparse_raw_terms=None, sparse_values=None
    ):
        """Not used - search handled by OpenGaussCollection."""
        return [], []

    def aggregate(self, filters=None):
        """Not used - aggregation handled by OpenGaussCollection."""
        return {}

    def update(self, scalar_index=None, description=None):
        """Not used - updates handled by OpenGaussCollection."""
        pass

    def rebuild_scalar_index(self, scalar_index, cands_fields):
        """Not used - scalar indexes are SQL indexes managed by OpenGaussCollection.update_index."""
        pass

    def close(self):
        """No-op for dummy index."""
        pass

    def drop(self):
        """No-op for dummy index."""
        pass


class OpenGaussCollection(ICollection):
    """A single OpenViking collection stored in an openGauss/PostgreSQL table.

    Schema design:
      - One table per collection: ``{collection_name}``
      - Column ``id`` VARCHAR(256) PRIMARY KEY
      - One column per non-vector field
      - Column ``vector`` vector(dim) for dense vectors
      - Metadata persisted in ``_ov_collection_meta`` table
      - Index metadata persisted in ``_ov_index_{collection_name}`` table
    """

    def __init__(
        self,
        conn,
        collection_name: str,
        meta: Dict[str, Any],
        dim: int,
        distance: str = "cosine",
        distributed: bool = False,
    ):
        super().__init__()
        self._conn = conn
        self._name = _validate_identifier(collection_name, kind="openGauss collection name")
        self._meta = meta
        self._dim = dim
        self._distance = distance
        self._distributed = distributed
        self._lock = threading.RLock()
        self._field_names = {
            _validate_identifier(
                field.get("FieldName") or field.get("field_name") or field.get("name", ""),
                kind="openGauss field name",
            )
            for field in meta.get("Fields", [])
        }
        self._field_names.add("id")
        if dim > 0:
            self._field_names.add("vector")
        self._array_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            in {"list<string>", "list<int64>"}
        }
        self._date_time_fields = {
            field.get("FieldName") or field.get("field_name") or field.get("name", "")
            for field in meta.get("Fields", [])
            if (field.get("FieldType") or field.get("field_type") or field.get("type"))
            == "date_time"
        }
        # Verified physical indexes and requested indexes waiting for data.
        self._indexes: Dict[str, Dict[str, Any]] = {}
        self._pending_indexes: Dict[str, Dict[str, Any]] = {}
        self._bulk_ingest_depth = 0
        self._load_index_meta()
        self._reconcile_index_metadata()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cursor(self):
        return self._conn.cursor()

    def _execute(self, sql: str, params=None, fetch: bool = False):
        with self._lock:
            cur = self._cursor()
            try:
                cur.execute(sql, params)
                if fetch:
                    rows = cur.fetchall()
                    self._conn.commit()
                    return rows
                self._conn.commit()
                return cur
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def _index_catalog_table(self) -> str:
        return _index_meta_table_name(self._name)

    def _load_index_meta(self):
        """Load persisted index metadata from the database.

        Only the undefined-table error is tolerated (no index has been
        created for this collection yet); any other backend failure must
        surface instead of silently leaving the index registry empty, which
        could trigger a spurious index rebuild.
        """
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        try:
            rows = self._execute(
                f"SELECT index_name, meta_json FROM {idx_table}",
                fetch=True,
            )
        except Exception as error:
            if _is_undefined_table_error(error):
                return
            raise
        for row in rows:
            index_meta = self._normalized_index_meta(
                row[0], migrate_persisted_index_meta(json.loads(row[1]))
            )
            if index_meta.get("_state") == "pending":
                self._pending_indexes[row[0]] = index_meta
            else:
                self._indexes[row[0]] = index_meta

    def _ensure_index_meta_table(self):
        """Create the per-collection index metadata table if needed.

        In distributed mode the table follows CN capabilities: a reference table
        on Citus-compatible deployments or an spq hash-distributed metadata table.
        """
        idx_table = self._index_catalog_table()
        quoted_idx_table = _quote_identifier(idx_table, kind="openGauss index metadata table")
        self._execute(
            f"""
            CREATE TABLE IF NOT EXISTS {quoted_idx_table} (
                index_name VARCHAR(256) PRIMARY KEY,
                meta_json  TEXT NOT NULL
            )
            """
        )
        if self._distributed:
            _try_make_metadata_table_distributed(self._conn, idx_table, "index_name")

    def _save_index_meta(self, index_name: str, meta: Dict[str, Any]):
        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        meta_json = json.dumps(meta)
        # Use UPDATE -> INSERT for distributed compatibility
        with self._lock:
            cur = self._cursor()
            try:
                cur.execute(
                    f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                    (meta_json, index_name),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        f"INSERT INTO {idx_table} (index_name, meta_json) VALUES (%s, %s)",
                        (index_name, meta_json),
                    )
                self._conn.commit()
            except Exception as error:
                self._conn.rollback()
                if getattr(error, "pgcode", None) != "23505":
                    raise
                retry_cursor = self._cursor()
                try:
                    retry_cursor.execute(
                        f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                        (meta_json, index_name),
                    )
                    if retry_cursor.rowcount != 1:
                        raise RuntimeError(
                            f"openGauss index metadata race recovery failed for {index_name!r}"
                        ) from error
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    retry_cursor.close()
            finally:
                cur.close()

    def _delete_index_meta(self, index_name: str):
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        try:
            self._execute(
                f"DELETE FROM {idx_table} WHERE index_name = %s",
                (index_name,),
            )
        except Exception as error:
            # Nothing to delete when the metadata table was never created.
            if _is_undefined_table_error(error):
                return
            raise

    def _get_all_columns(self) -> List[str]:
        """Return all non-system column names of the collection table."""
        rows = self._execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
              AND table_schema = current_schema()
            ORDER BY ordinal_position
            """,
            (self._name,),
            fetch=True,
        )
        return [row[0] for row in rows] if rows else []

    def _get_column_types(self) -> Dict[str, str]:
        """Return column name to data type mapping."""
        rows = self._execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
              AND table_schema = current_schema()
            """,
            (self._name,),
            fetch=True,
        )
        return {row[0]: row[1] for row in rows} if rows else {}

    def _normalize_filter_date_times(
        self, filters: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if not filters:
            return filters
        normalized = dict(filters)
        conditions = normalized.get("conds")
        if normalized.get("op") in {"and", "or"} and isinstance(conditions, list):
            normalized["conds"] = [
                self._normalize_filter_date_times(condition) for condition in conditions
            ]
            return normalized

        field = normalized.get("field")
        if field not in self._date_time_fields:
            return normalized
        if isinstance(conditions, list):
            normalized["conds"] = [_date_time_to_epoch_ms(value) for value in conditions]
        for bound in ("gt", "gte", "lt", "lte"):
            if normalized.get(bound) is not None:
                normalized[bound] = _date_time_to_epoch_ms(normalized[bound])
        return normalized

    def _select_output_columns(self, output_fields: Optional[List[str]]) -> str:
        """Build the SELECT column list from output_fields."""
        if not output_fields:
            return "*"
        allowed_fields = self._field_names | _UNSTORED_FIELDS
        unknown_fields = sorted(set(output_fields) - allowed_fields)
        if unknown_fields:
            raise ValueError(f"Unknown openGauss output fields: {unknown_fields}")
        cols = [_quote_identifier("id")]
        for field in output_fields:
            if field == "id":
                continue
            quoted = _quote_identifier(field, kind="openGauss output field")
            if field in _UNSTORED_FIELDS:
                # Schema/metadata still lists ``content``; the physical table
                # does not. Project NULL so official URI rewrite/migration
                # output_fields do not become a missing-column SQL error.
                cols.append(f"NULL AS {quoted}")
            else:
                cols.append(quoted)
        return ", ".join(cols)

    def _row_to_dict(self, row, columns: List[str]) -> Dict[str, Any]:
        record = {}
        for column, value in zip(columns, row, strict=True):
            if column == "vector":
                record[column] = _coerce_vector(value)
            elif column == "sparse_vector":
                record[column] = _coerce_sparse_vector(value)
            else:
                record[column] = value
        return record

    # ------------------------------------------------------------------
    # ICollection: collection lifecycle
    # ------------------------------------------------------------------

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        if fields:
            self._meta.update(fields)
        if description is not None:
            self._meta["Description"] = description
        # Persist updated meta
        self._execute(
            f"""
            UPDATE "{_META_TABLE}"
            SET meta_json = %s
            WHERE table_name = %s
            """,
            (json.dumps(self._meta), self._name),
        )

    def get_meta_data(self) -> Dict[str, Any]:
        return dict(self._meta)

    def close(self):
        pass  # Connection lifecycle managed by adapter

    def drop(self):
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        self._execute(f'DROP TABLE IF EXISTS "{self._name}" CASCADE')
        self._execute(f"DROP TABLE IF EXISTS {idx_table} CASCADE")
        self._execute(
            f'DELETE FROM "{_META_TABLE}" WHERE table_name = %s',
            (self._name,),
        )
        self._indexes.clear()

    # ------------------------------------------------------------------
    # ICollection: index management
    # ------------------------------------------------------------------

    def _physical_index_definition(self, physical_index_name: str) -> Optional[str]:
        rows = self._execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = %s
              AND indexname = %s
            """,
            (self._name, physical_index_name),
            fetch=True,
        )
        return rows[0][0] if rows else None

    def _table_has_rows(self) -> bool:
        rows = self._execute(
            f"SELECT 1 FROM {_quote_identifier(self._name)} LIMIT 1",
            fetch=True,
        )
        return bool(rows)

    @staticmethod
    def _index_requires_data(index_meta: Dict[str, Any]) -> bool:
        if index_meta["_pg_index_type"] != "hnsw":
            return True
        return index_meta["_quantization"] is not None

    @staticmethod
    def _format_index_option(value: Any) -> str:
        if isinstance(value, bool):
            return "on" if value else "off"
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        return str(int(value))

    def _normalized_index_meta(self, index_name: str, meta_data: Dict[str, Any]) -> Dict[str, Any]:
        misplaced = (BUILD_KEYS | SEARCH_KEYS) & meta_data.keys()
        if misplaced:
            raise ValueError(f"openGauss index parameters must be nested: {sorted(misplaced)}")
        vector_meta = dict(meta_data.get("VectorIndex") or {})
        index_type = _normalize_index_type(vector_meta.get("IndexType", "hnsw"))
        access_method = _index_access_method(index_type)
        quantization = _index_quantization(index_type)
        distance = vector_meta.get("Distance", self._distance)
        operator_class = _VECTOR_OPS.get(distance, {}).get(access_method)
        if not operator_class:
            raise ValueError(
                f"openGauss index_type={index_type!r} does not support distance={distance!r}"
            )

        options = OpenGaussIndexConfig(
            index_type=index_type,
            build_params=_resolve_build_params(index_type, meta_data),
            search_params=_resolve_search_params(index_type, meta_data),
            parallel_workers=meta_data.get("parallel_workers", 0),
            maintenance_work_mem_mb=meta_data.get("maintenance_work_mem_mb", 64),
        )
        options.validate_capabilities(distributed=self._distributed, distance=distance)
        index_type = options.index_type
        quantization = options.quantization
        build_params = options.build_params

        physical_index_name = _bounded_identifier(
            f"idx_{self._name}_{index_name}_vec",
            kind="openGauss physical index name",
        )
        full_meta = dict(meta_data)
        full_meta["IndexName"] = index_name
        full_meta["build_params"] = build_params
        full_meta["search_params"] = options.search_params
        full_meta["parallel_workers"] = options.parallel_workers
        full_meta["maintenance_work_mem_mb"] = int(
            meta_data.get(
                "maintenance_work_mem_mb",
                getattr(self, "_maintenance_work_mem_mb", 64),
            )
        )
        full_meta["_pg_index_name"] = physical_index_name
        full_meta["_pg_index_type"] = access_method
        full_meta["_logical_index_type"] = index_type
        full_meta["_quantization"] = quantization
        full_meta["_operator_class"] = operator_class
        full_meta["_distance"] = distance
        vector_meta["IndexType"] = index_type
        vector_meta["Distance"] = distance
        full_meta["VectorIndex"] = vector_meta
        return full_meta

    def _index_options(self, index_meta: Dict[str, Any]) -> list[tuple[str, Any]]:
        access_method = index_meta["_pg_index_type"]
        build_params = index_meta["build_params"]
        if access_method == "hnsw":
            options = [
                ("m", build_params.get("m", 16)),
                ("ef_construction", build_params.get("ef_construction", 64)),
            ]
        elif access_method == "ivfflat":
            options = [("lists", build_params.get("lists", 100))]
        else:
            options = [("index_size", build_params.get("index_size", 100))]

        if index_meta["_quantization"] == "pq":
            options.extend(
                [
                    ("enable_pq", True),
                    ("pq_m", build_params.get("pq_m", 8)),
                    ("pq_ksub", build_params.get("pq_ksub", 256)),
                ]
            )
            if access_method == "ivfflat":
                options.append(("by_residual", build_params.get("by_residual", False)))
        if index_meta["_quantization"] == "rabitq":
            options.extend(
                [
                    ("enable_rabitq", True),
                    ("rabitq_refine_type", build_params.get("rabitq_refine_type", "none")),
                ]
            )
            if "rabitq_fht" in build_params:
                options.append(("rabitq_fht", build_params["rabitq_fht"]))
        return options

    def _create_index_sql(self, index_meta: Dict[str, Any]) -> str:
        option_sql = ", ".join(
            f"{name} = {self._format_index_option(value)}"
            for name, value in self._index_options(index_meta)
        )
        return (
            f"CREATE INDEX {_quote_identifier(index_meta['_pg_index_name'])} "
            f"ON {_quote_identifier(self._name)} "
            f"USING {index_meta['_pg_index_type']} "
            f"({_quote_identifier('vector')} {index_meta['_operator_class']}) "
            f"WITH ({option_sql})"
        )

    @staticmethod
    def _normalized_indexdef(indexdef: str) -> str:
        return " ".join(indexdef.lower().replace('"', "").replace("'", "").split())

    def _index_identity_matches(self, index_meta: Dict[str, Any], indexdef: str) -> bool:
        compact_definition = self._normalized_indexdef(indexdef).replace(" ", "")
        return (
            f"using{index_meta['_pg_index_type']}" in compact_definition
            and index_meta["_operator_class"].lower().replace(" ", "") in compact_definition
        )

    def _catalog_index_options(self, indexdef: str) -> Optional[Dict[str, str]]:
        match = re.search(r"\bwith\s*\((.*)\)", self._normalized_indexdef(indexdef))
        if not match:
            return None
        options: Dict[str, str] = {}
        for part in match.group(1).split(","):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            options[name.strip()] = value.strip()
        return options

    @staticmethod
    def _default_index_option_value(option_name: str) -> Any:
        return {
            "m": 16,
            "ef_construction": 64,
            "lists": 100,
            "index_size": 100,
            "pq_m": 8,
            "pq_ksub": 256,
            "by_residual": False,
            "rabitq_refine_type": "none",
        }.get(option_name)

    def _option_is_omittable_default(self, option_name: str, option_value: Any) -> bool:
        default = self._default_index_option_value(option_name)
        if default is None:
            return False
        return (
            self._format_index_option(option_value).strip("'").lower()
            == self._format_index_option(default).strip("'").lower()
        )

    def _index_definition_matches(self, index_meta: Dict[str, Any], indexdef: str) -> bool:
        if not self._index_identity_matches(index_meta, indexdef):
            return False
        catalog_options = self._catalog_index_options(indexdef) or {}
        expected_pairs = list(self._index_options(index_meta))
        expected_options = {
            option_name: self._format_index_option(option_value).strip("'").lower()
            for option_name, option_value in expected_pairs
        }
        for option_name, option_value in expected_pairs:
            actual = catalog_options.get(option_name)
            if actual is None:
                # Catalog may omit default WITH options; non-defaults must appear.
                if not self._option_is_omittable_default(option_name, option_value):
                    return False
                continue
            if actual != expected_options[option_name]:
                return False
        # Downgrades such as hnsw-pq -> hnsw leave enable_pq/pq_m in catalog.
        # Those extras must force a rebuild; one-way expected-only checks miss them.
        for option_name, actual in catalog_options.items():
            if option_name in expected_options:
                continue
            if option_name in _QUANTIZATION_OPTION_NAMES:
                if option_name in {"enable_pq", "enable_rabitq"} and actual in {
                    "off",
                    "false",
                    "0",
                }:
                    continue
                return False
            default = self._default_index_option_value(option_name)
            if default is None:
                return False
            if actual != self._format_index_option(default).strip("'").lower():
                return False
        return True

    def _create_scalar_indexes(self, index_name: str, index_meta: Dict[str, Any]) -> None:
        for scalar_field in index_meta.get("ScalarIndex", []):
            if scalar_field not in self._field_names:
                raise ValueError(f"Unknown openGauss scalar index field: {scalar_field!r}")
            scalar_index_name = _bounded_identifier(
                f"idx_{self._name}_{index_name}_{scalar_field}",
                kind="openGauss scalar index name",
            )
            self._execute(
                f"CREATE INDEX IF NOT EXISTS {_quote_identifier(scalar_index_name)} "
                f"ON {_quote_identifier(self._name)} ({_quote_identifier(scalar_field)})"
            )

    def _persist_index_meta_and_scalar_indexes(
        self,
        index_name: str,
        index_meta: Dict[str, Any],
    ) -> None:
        scalar_fields = list(index_meta.get("ScalarIndex", []))
        unknown_fields = sorted(set(scalar_fields) - self._field_names)
        if unknown_fields:
            raise ValueError(f"Unknown openGauss scalar index field(s): {unknown_fields}")

        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        meta_json = json.dumps(index_meta)

        def execute_transaction(cursor, *, allow_insert: bool) -> None:
            for scalar_field in scalar_fields:
                scalar_index_name = _bounded_identifier(
                    f"idx_{self._name}_{index_name}_{scalar_field}",
                    kind="openGauss scalar index name",
                )
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"{_quote_identifier(scalar_index_name)} "
                    f"ON {_quote_identifier(self._name)} "
                    f"({_quote_identifier(scalar_field)})"
                )
            cursor.execute(
                f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                (meta_json, index_name),
            )
            if cursor.rowcount == 0:
                if not allow_insert:
                    raise RuntimeError(
                        f"openGauss index metadata race recovery failed for {index_name!r}"
                    )
                cursor.execute(
                    f"INSERT INTO {idx_table} (index_name, meta_json) VALUES (%s, %s)",
                    (index_name, meta_json),
                )

        with self._lock:
            cursor = self._cursor()
            try:
                execute_transaction(cursor, allow_insert=True)
                self._conn.commit()
            except Exception as error:
                self._conn.rollback()
                if getattr(error, "pgcode", None) != "23505":
                    raise
                retry_cursor = self._cursor()
                try:
                    execute_transaction(retry_cursor, allow_insert=False)
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    retry_cursor.close()
            finally:
                cursor.close()

    def _materialize_index(self, index_name: str, index_meta: Dict[str, Any]) -> None:
        index_meta = dict(index_meta)
        index_meta.pop("_state", None)
        physical_index_name = index_meta["_pg_index_name"]
        existing_definition = self._physical_index_definition(physical_index_name)
        if existing_definition and not self._index_definition_matches(
            index_meta, existing_definition
        ):
            self._execute(f"DROP INDEX {_quote_identifier(physical_index_name)}")
            existing_definition = None

        self._apply_parallel_workers(index_meta)
        if not existing_definition:
            maintenance_work_mem_mb = int(index_meta["maintenance_work_mem_mb"])
            with self._lock:
                cursor = self._cursor()
                try:
                    cursor.execute(
                        f"SET LOCAL maintenance_work_mem = '{maintenance_work_mem_mb}MB'"
                    )
                    cursor.execute(self._create_index_sql(index_meta))
                    self._conn.commit()
                except Exception as error:
                    self._conn.rollback()
                    deployment_mode = "distributed" if self._distributed else "standalone"
                    raise RuntimeError(
                        "Failed to create openGauss ANN index "
                        f"{index_meta['_logical_index_type']!r} using "
                        f"{index_meta['_pg_index_type']!r} on collection {self._name!r} "
                        f"in {deployment_mode} mode with "
                        f"maintenance_work_mem={maintenance_work_mem_mb}MB: {error}"
                    ) from error
                finally:
                    cursor.close()

        verified_definition = self._physical_index_definition(physical_index_name)
        if not verified_definition or not self._index_definition_matches(
            index_meta, verified_definition
        ):
            raise RuntimeError(
                f"openGauss created index {physical_index_name!r} but catalog verification failed"
            )

        self._persist_index_meta_and_scalar_indexes(index_name, index_meta)
        self._indexes[index_name] = index_meta
        self._pending_indexes.pop(index_name, None)

    def _apply_parallel_workers(self, index_meta: Dict[str, Any]) -> None:
        parallel_workers = index_meta.get("parallel_workers", 0)
        quoted_table = _quote_identifier(self._name)
        if parallel_workers > 0:
            self._execute(f"ALTER TABLE {quoted_table} SET (parallel_workers = {parallel_workers})")
            return
        self._execute(f"ALTER TABLE {quoted_table} RESET (parallel_workers)")

    def _materialize_pending_indexes(self) -> None:
        if self._bulk_ingest_depth > 0 or not self._pending_indexes:
            return
        if not self._table_has_rows():
            return
        for index_name, index_meta in list(self._pending_indexes.items()):
            self._materialize_index(index_name, index_meta)

    def _materialize_pending_index(self, index_name: str) -> None:
        if self._bulk_ingest_depth > 0:
            return
        index_meta = self._pending_indexes.get(index_name)
        if index_meta is None or not self._table_has_rows():
            return
        self._materialize_index(index_name, index_meta)

    def _reconcile_index_metadata(self) -> None:
        for index_name, index_meta in list(self._indexes.items()):
            physical_index_name = index_meta.get("_pg_index_name")
            if not physical_index_name:
                self._delete_index_meta_and_scalar_indexes(index_name, index_meta)
                self._indexes.pop(index_name, None)
                continue
            index_definition = self._physical_index_definition(physical_index_name)
            try:
                normalized_meta = self._normalized_index_meta(index_name, index_meta)
            except Exception:
                self._delete_index_meta_and_scalar_indexes(index_name, index_meta)
                self._indexes.pop(index_name, None)
                continue
            if not index_definition or not self._index_definition_matches(
                normalized_meta, index_definition
            ):
                self._delete_index_meta_and_scalar_indexes(index_name, index_meta)
                self._indexes.pop(index_name, None)

    def _search_param_statements(self, index_name: str) -> list[str]:
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        if not index_meta:
            raise RuntimeError(f"openGauss index {index_name!r} is not configured")
        index_type = index_meta.get("_logical_index_type") or _normalize_index_type(
            index_meta.get("VectorIndex", {}).get("IndexType", "hnsw")
        )
        access_method = _index_access_method(index_type)
        params = _resolve_search_params(index_type, index_meta)
        statements: list[str] = []
        if access_method == "hnsw":
            ef_search = params.get("ef_search", params.get("hnsw_ef_search"))
            earlystop = params.get("earlystop_threshold", params.get("hnsw_earlystop_threshold"))
            if ef_search is not None:
                statements.append(f"SET LOCAL hnsw_ef_search = {int(ef_search)}")
            if earlystop is not None:
                statements.append(f"SET LOCAL hnsw_earlystop_threshold = {int(earlystop)}")
        elif access_method == "ivfflat":
            probes = params.get("probes", params.get("ivfflat_probes"))
            if probes is not None:
                statements.append(f"SET LOCAL ivfflat_probes = {int(probes)}")
            if params.get("ivfpq_kreorder") is not None:
                statements.append(f"SET LOCAL ivfpq_kreorder = {int(params['ivfpq_kreorder'])}")
        else:
            probes = params.get("probes", params.get("diskann_probes"))
            if probes is not None:
                statements.append(f"SET LOCAL diskann_probes = {int(probes)}")
        if params.get("rbq_query_bits") is not None:
            statements.append(f"SET LOCAL rbq_query_bits = {int(params['rbq_query_bits'])}")
        if params.get("rbq_refinek") is not None:
            statements.append(f"SET LOCAL rbq_refinek = {int(params['rbq_refinek'])}")
        if statements and self._distributed:
            # spq defaults to propagate_set_commands='none', so plain SET LOCAL
            # would only change the CN session while DN shard scans keep server
            # defaults. Propagation makes the parameters reach the DN scans.
            statements.insert(0, "SET LOCAL spq.propagate_set_commands = 'local'")
        return statements

    def _apply_search_params_on_cursor(self, cursor, index_name: str) -> None:
        for statement in self._search_param_statements(index_name):
            cursor.execute(statement)

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        _validate_identifier(index_name, kind="openGauss index name")
        index_meta = self._normalized_index_meta(index_name, meta_data)
        if self._index_requires_data(index_meta) and not self._table_has_rows():
            existing_definition = self._physical_index_definition(index_meta["_pg_index_name"])
            if existing_definition and not self._index_definition_matches(
                index_meta, existing_definition
            ):
                self._execute(
                    f"DROP INDEX IF EXISTS {_quote_identifier(index_meta['_pg_index_name'])}"
                )
            index_meta["_state"] = "pending"
            self._persist_index_meta_and_scalar_indexes(index_name, index_meta)
            self._pending_indexes[index_name] = index_meta
            self._indexes.pop(index_name, None)
            return _PgIndex(index_name, index_meta)
        self._materialize_index(index_name, index_meta)
        return _PgIndex(index_name, index_meta)

    def has_index(self, index_name: str) -> bool:
        index_meta = self._indexes.get(index_name)
        if index_meta is None:
            return False
        index_definition = self._physical_index_definition(index_meta["_pg_index_name"])
        return bool(
            index_definition and self._index_definition_matches(index_meta, index_definition)
        )

    def get_index(self, index_name: str) -> Optional[IIndex]:
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        return _PgIndex(index_name, index_meta) if index_meta else None

    def list_indexes(self) -> List[str]:
        return sorted(set(self._indexes) | set(self._pending_indexes))

    def drop_index(self, index_name: str):
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        if not index_meta:
            return
        physical_index_name = index_meta.get("_pg_index_name")
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        with self._lock:
            cursor = self._cursor()
            try:
                if physical_index_name:
                    cursor.execute(f"DROP INDEX IF EXISTS {_quote_identifier(physical_index_name)}")
                for scalar_field in index_meta.get("ScalarIndex", []):
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(f"DROP INDEX IF EXISTS {_quote_identifier(scalar_index_name)}")
                cursor.execute(
                    f"DELETE FROM {idx_table} WHERE index_name = %s",
                    (index_name,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()
        self._indexes.pop(index_name, None)
        self._pending_indexes.pop(index_name, None)

    def _delete_index_meta_and_scalar_indexes(
        self,
        index_name: str,
        index_meta: Dict[str, Any],
    ) -> None:
        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        with self._lock:
            cursor = self._cursor()
            try:
                for scalar_field in index_meta.get("ScalarIndex", []):
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(f"DROP INDEX IF EXISTS {_quote_identifier(scalar_index_name)}")
                cursor.execute(
                    f"DELETE FROM {idx_table} WHERE index_name = %s",
                    (index_name,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[List[str]] = None,
        description: Optional[str] = None,
    ):
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        if not index_meta:
            return
        if scalar_index is not None:
            if not isinstance(scalar_index, list) or not all(
                isinstance(field, str) for field in scalar_index
            ):
                raise ValueError("openGauss ScalarIndex must be a list of field names")
            new_scalar_fields = list(dict.fromkeys(scalar_index))
            unknown_fields = sorted(set(new_scalar_fields) - self._field_names)
            if unknown_fields:
                raise ValueError(f"Unknown openGauss scalar index field(s): {unknown_fields}")
        self._ensure_index_meta_table()
        idx_table = _quote_identifier(
            self._index_catalog_table(), kind="openGauss index metadata table"
        )
        with self._lock:
            cursor = self._cursor()
            try:
                cursor.execute(
                    f"SELECT meta_json FROM {idx_table} WHERE index_name = %s FOR UPDATE",
                    (index_name,),
                )
                metadata_row = cursor.fetchone()
                current_meta = json.loads(metadata_row[0]) if metadata_row else dict(index_meta)
                updated_meta = dict(current_meta)
                old_scalar_fields = list(current_meta.get("ScalarIndex", []))
                new_scalar_fields = old_scalar_fields
                if scalar_index is not None:
                    new_scalar_fields = list(dict.fromkeys(scalar_index))
                    updated_meta["ScalarIndex"] = new_scalar_fields
                if description is not None:
                    updated_meta["Description"] = description

                removed_fields = sorted(set(old_scalar_fields) - set(new_scalar_fields))
                added_fields = sorted(set(new_scalar_fields) - set(old_scalar_fields))
                for scalar_field in removed_fields:
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(f"DROP INDEX IF EXISTS {_quote_identifier(scalar_index_name)}")
                for scalar_field in added_fields:
                    scalar_index_name = _bounded_identifier(
                        f"idx_{self._name}_{index_name}_{scalar_field}",
                        kind="openGauss scalar index name",
                    )
                    cursor.execute(
                        f"CREATE INDEX {_quote_identifier(scalar_index_name)} "
                        f"ON {_quote_identifier(self._name)} "
                        f"({_quote_identifier(scalar_field)})"
                    )
                cursor.execute(
                    f"UPDATE {idx_table} SET meta_json = %s WHERE index_name = %s",
                    (json.dumps(updated_meta), index_name),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        f"INSERT INTO {idx_table} (index_name, meta_json) VALUES (%s, %s)",
                        (index_name, json.dumps(updated_meta)),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

        if index_name in self._indexes:
            self._indexes[index_name] = updated_meta
        else:
            self._pending_indexes[index_name] = updated_meta

    def get_index_meta_data(self, index_name: str) -> Dict[str, Any]:
        index_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name)
        return dict(index_meta or {})

    def begin_bulk_ingest(self) -> None:
        with self._lock:
            self._bulk_ingest_depth += 1

    def end_bulk_ingest(self) -> None:
        with self._lock:
            if self._bulk_ingest_depth <= 0:
                raise RuntimeError("openGauss end_bulk_ingest called without matching begin")
            self._bulk_ingest_depth -= 1
            if self._bulk_ingest_depth == 0:
                self._materialize_pending_indexes()

    # ------------------------------------------------------------------
    # ICollection: search
    # ------------------------------------------------------------------

    def _resolve_distance_and_op(self, index_name: str) -> tuple[str, str]:
        idx_meta = self._indexes.get(index_name) or self._pending_indexes.get(index_name, {})
        distance = idx_meta.get("_distance") or idx_meta.get("Distance", self._distance)
        op = _DISTANCE_OP.get(distance, "<=>")
        return distance, op

    def search_by_vector(
        self,
        index_name: str,
        dense_vector: Optional[List[float]] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sparse_vector: Optional[Dict[str, float]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        # Sparse support is checked before the dense emptiness short-circuit:
        # a pure sparse query must fail loudly instead of returning an empty
        # result set that looks like "no recall".
        if sparse_vector:
            raise NotImplementedError(
                "openGauss backend does not support sparse or hybrid vector search"
            )

        if not dense_vector:
            return SearchResult(data=[])

        self._materialize_pending_index(index_name)
        if index_name in self._pending_indexes and not self._table_has_rows():
            return SearchResult(data=[])
        if not self.has_index(index_name):
            raise RuntimeError(f"openGauss physical ANN index {index_name!r} is missing or invalid")
        distance_metric, op = self._resolve_distance_and_op(index_name)
        select_cols = self._select_output_columns(output_fields)

        where_frag, where_params = _build_where_clause(
            self._normalize_filter_date_times(filters), self._array_fields
        )
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        normalized_vector = _validate_vector(dense_vector, self._dim, field_name="query vector")
        vector_str = "[" + ",".join(str(value) for value in normalized_vector) + "]"
        sql = f"""
            SELECT {select_cols}, vector {op} %s::vector AS _distance
            FROM "{self._name}"
            {where_clause}
            ORDER BY _distance, id
            LIMIT %s OFFSET %s
        """
        params = [vector_str] + where_params + [limit, offset]

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    self._apply_search_params_on_cursor(cur, index_name)
                    cur.execute(sql, params)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: search_by_vector failed: %s", error)
            raise

        if not rows:
            return SearchResult(data=[])

        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            distance = record.pop("_distance", 0.0)
            record_id = record.pop("id", None)
            similarity = _distance_to_similarity(distance_metric, distance)
            items.append(SearchItemResult(id=record_id, fields=record, score=similarity))
        return SearchResult(data=items)

    def search_by_scalar(
        self,
        index_name: str,
        field: str,
        order: Optional[str] = "desc",
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        select_cols = self._select_output_columns(output_fields)
        quoted_field = _quote_identifier(field, kind="openGauss scalar sort field")
        where_frag, where_params = _build_where_clause(
            self._normalize_filter_date_times(filters), self._array_fields
        )
        where_clause = f"WHERE {where_frag}" if where_frag else ""
        sort_dir = "DESC" if (order or "desc").lower() == "desc" else "ASC"

        sql = f"""
            SELECT {select_cols}, {quoted_field} AS _scalar_val
            FROM "{self._name}"
            {where_clause}
            ORDER BY {quoted_field} {sort_dir}, id {sort_dir}
            LIMIT %s OFFSET %s
        """
        params = where_params + [limit, offset]

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    cur.execute(sql, params)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: search_by_scalar failed: %s", error)
            raise

        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            score = record.pop("_scalar_val", 0.0)
            record_id = record.pop("id", None)
            try:
                score_float = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                score_float = 0.0
            items.append(SearchItemResult(id=record_id, fields=record, score=score_float))
        return SearchResult(data=items)

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        select_cols = self._select_output_columns(output_fields)
        where_frag, where_params = _build_where_clause(
            self._normalize_filter_date_times(filters), self._array_fields
        )
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        sql = f"""
            SELECT {select_cols}
            FROM "{self._name}"
            {where_clause}
            ORDER BY RANDOM()
            LIMIT %s OFFSET %s
        """
        params = where_params + [limit, offset]

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    cur.execute(sql, params)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: search_by_random failed: %s", error)
            raise

        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            record_id = record.pop("id", None)
            items.append(SearchItemResult(id=record_id, fields=record, score=0.0))
        return SearchResult(data=items)

    def search_by_keywords(
        self,
        index_name: str,
        keywords: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError(
            "openGauss backend does not provide OpenViking keyword/full-text search"
        )

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        # Fetch the source vector and use it for similarity search. Backend
        # failures propagate; only a genuinely missing id yields empty data.
        try:
            rows = self._execute(
                f'SELECT vector FROM "{self._name}" WHERE id = %s',
                (str(id),),
                fetch=True,
            )
        except Exception as error:
            logger.error(
                "opengauss_adapter: search_by_id failed to fetch source vector: %s",
                error,
            )
            raise

        if not rows or rows[0][0] is None:
            return SearchResult(data=[])

        vec = _coerce_vector(rows[0][0])
        if not isinstance(vec, list) or not vec:
            return SearchResult(data=[])

        result = self.search_by_vector(
            index_name,
            dense_vector=vec,
            limit=limit + offset + 1,
            offset=0,
            filters=filters,
            output_fields=output_fields,
        )
        candidates = [item for item in result.data if str(item.id) != str(id)]
        result.data = candidates[offset : offset + limit]
        return result

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str],
        image: Optional[Any],
        video: Optional[Any],
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        raise NotImplementedError(
            "openGauss backend requires upstream multimodal embedding before vector search"
        )

    # ------------------------------------------------------------------
    # ICollection: data operations
    # ------------------------------------------------------------------

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        if not data_list:
            return

        import uuid

        all_columns = set(self._get_all_columns())
        column_types = self._get_column_types()
        prepared_records: list[tuple[str, list[str], list[str], list[Any]]] = []

        for record_index, record in enumerate(data_list):
            record_id = record.get("id") or record.get("_id") or str(uuid.uuid4())
            unknown_fields = sorted(
                set(record) - {"id", "_id", "vector"} - all_columns - _UNSTORED_FIELDS
            )
            if unknown_fields:
                raise ValueError(
                    f"openGauss record at index {record_index} contains unknown fields: "
                    f"{unknown_fields}"
                )

            extra_fields = {
                field_name: field_value
                for field_name, field_value in record.items()
                if field_name not in {"id", "_id", "vector"} | _UNSTORED_FIELDS
            }
            column_names = ["id"]
            placeholders = ["%s"]
            values: list[Any] = [str(record_id)]

            for field_name, field_value in extra_fields.items():
                _validate_identifier(field_name, kind="openGauss field name")
                column_names.append(field_name)
                column_type = column_types.get(field_name, "").lower()
                if field_name in self._date_time_fields:
                    field_value = _date_time_to_epoch_ms(field_value)
                if column_type == "jsonb":
                    placeholders.append("%s::jsonb")
                    field_value = json.dumps(field_value, ensure_ascii=False)
                else:
                    placeholders.append("%s")
                values.append(field_value)

            vector_value = record.get("vector")
            if vector_value is not None:
                if "vector" not in all_columns:
                    raise ValueError("openGauss collection does not define a vector column")
                normalized_vector = _validate_vector(
                    vector_value,
                    self._dim,
                    field_name=f"record[{record_index}].vector",
                )
                column_names.append("vector")
                placeholders.append("%s::vector")
                values.append("[" + ",".join(str(value) for value in normalized_vector) + "]")

            prepared_records.append((str(record_id), column_names, placeholders, values))

        table_identifier = _quote_identifier(self._name, kind="openGauss collection name")
        with self._lock:
            cursor = self._cursor()
            try:
                for record_id, column_names, placeholders, values in prepared_records:
                    update_columns = [
                        column_name for column_name in column_names if column_name != "id"
                    ]
                    update_placeholders = [
                        placeholder
                        for column_name, placeholder in zip(column_names, placeholders, strict=True)
                        if column_name != "id"
                    ]
                    update_set = ", ".join(
                        f"{_quote_identifier(column_name)} = {placeholder}"
                        for column_name, placeholder in zip(
                            update_columns, update_placeholders, strict=True
                        )
                    )
                    update_values = [
                        value
                        for column_name, value in zip(column_names, values, strict=True)
                        if column_name != "id"
                    ] + [record_id]

                    updated = 0
                    if update_set:
                        cursor.execute(
                            f"UPDATE {table_identifier} SET {update_set} "
                            f"WHERE {_quote_identifier('id')} = %s",
                            update_values,
                        )
                        updated = cursor.rowcount
                    if updated == 0:
                        columns_sql = ", ".join(
                            _quote_identifier(column_name) for column_name in column_names
                        )
                        insert_placeholders = ", ".join(placeholders)
                        cursor.execute("SAVEPOINT ov_upsert_insert")
                        try:
                            cursor.execute(
                                f"INSERT INTO {table_identifier} ({columns_sql}) "
                                f"VALUES ({insert_placeholders})",
                                values,
                            )
                        except Exception as error:
                            cursor.execute("ROLLBACK TO SAVEPOINT ov_upsert_insert")
                            if getattr(error, "pgcode", None) != "23505":
                                raise
                            if update_set:
                                cursor.execute(
                                    f"UPDATE {table_identifier} SET {update_set} "
                                    f"WHERE {_quote_identifier('id')} = %s",
                                    update_values,
                                )
                                if cursor.rowcount != 1:
                                    raise RuntimeError(
                                        f"openGauss concurrent upsert recovery failed for {record_id!r}"
                                    ) from error
                        finally:
                            cursor.execute("RELEASE SAVEPOINT ov_upsert_insert")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()
        try:
            self._materialize_pending_indexes()
        except Exception:
            logger.exception(
                "opengauss_adapter: data committed but pending ANN index materialization failed"
            )

    def update_data(self, data_list: List[Dict[str, Any]]):
        """Update existing records while preserving unspecified fields."""
        if not data_list:
            return []
        for record in data_list:
            if "id" not in record:
                raise ValueError("primary key 'id' is required for update")

        all_columns = set(self._get_all_columns())
        column_types = self._get_column_types()
        prepared_updates: list[tuple[str, list[str], list[Any]]] = []

        for record_index, record in enumerate(data_list):
            primary_key = str(record["id"])
            unknown_fields = sorted(
                set(record) - {"id", "_id", "vector"} - all_columns - _UNSTORED_FIELDS
            )
            if unknown_fields:
                raise ValueError(
                    f"openGauss record at index {record_index} contains unknown fields: "
                    f"{unknown_fields}"
                )

            assignments: list[str] = []
            values: list[Any] = []
            for field_name, field_value in record.items():
                if field_name in {"id", "_id"} | _UNSTORED_FIELDS:
                    continue
                _validate_identifier(field_name, kind="openGauss field name")
                if field_name == "vector":
                    if "vector" not in all_columns:
                        raise ValueError("openGauss collection does not define a vector column")
                    normalized_vector = _validate_vector(
                        field_value,
                        self._dim,
                        field_name=f"record[{record_index}].vector",
                    )
                    assignments.append(f"{_quote_identifier(field_name)} = %s::vector")
                    values.append("[" + ",".join(str(value) for value in normalized_vector) + "]")
                    continue
                if field_name in self._date_time_fields:
                    field_value = _date_time_to_epoch_ms(field_value)
                if column_types.get(field_name, "").lower() == "jsonb":
                    assignments.append(f"{_quote_identifier(field_name)} = %s::jsonb")
                    field_value = json.dumps(field_value, ensure_ascii=False)
                else:
                    assignments.append(f"{_quote_identifier(field_name)} = %s")
                values.append(field_value)
            prepared_updates.append((primary_key, assignments, values))

        table_identifier = _quote_identifier(
            self._name,
            kind="openGauss collection name",
        )
        with self._lock:
            cursor = self._cursor()
            try:
                missing_ids: list[str] = []
                for primary_key, assignments, values in prepared_updates:
                    if assignments:
                        cursor.execute(
                            f"UPDATE {table_identifier} SET {', '.join(assignments)} "
                            f"WHERE {_quote_identifier('id')} = %s",
                            values + [primary_key],
                        )
                    else:
                        cursor.execute(
                            f"UPDATE {table_identifier} "
                            f"SET {_quote_identifier('id')} = {_quote_identifier('id')} "
                            f"WHERE {_quote_identifier('id')} = %s",
                            (primary_key,),
                        )
                    if cursor.rowcount == 0:
                        missing_ids.append(primary_key)
                if missing_ids:
                    raise ValueError(f"record not found for primary key(s): {missing_ids}")
                self._conn.commit()
                return [primary_key for primary_key, _, _ in prepared_updates]
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        if not primary_keys:
            return FetchDataInCollectionResult(items=[], ids_not_exist=[])

        str_keys = [str(k) for k in primary_keys]
        placeholders = ", ".join(["%s"] * len(str_keys))
        sql = f'SELECT * FROM "{self._name}" WHERE id IN ({placeholders})'

        try:
            with self._lock:
                cur = self._cursor()
                try:
                    cur.execute(sql, str_keys)
                    col_names = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                finally:
                    cur.close()
        except Exception as error:
            logger.error("opengauss_adapter: fetch_data failed: %s", error)
            raise

        found_ids = set()
        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            record_id = record.pop("id", None)
            found_ids.add(str(record_id))
            items.append(DataItem(id=record_id, fields=record))

        ids_not_exist = [k for k in str_keys if k not in found_ids]
        return FetchDataInCollectionResult(items=items, ids_not_exist=ids_not_exist)

    def delete_data(self, primary_keys: List[Any]):
        if not primary_keys:
            return
        str_keys = [str(k) for k in primary_keys]
        placeholders = ", ".join(["%s"] * len(str_keys))
        self._execute(
            f'DELETE FROM "{self._name}" WHERE id IN ({placeholders})',
            str_keys,
        )

    def delete_all_data(self):
        self._execute(f'TRUNCATE TABLE "{self._name}"')

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        where_frag, where_params = _build_where_clause(
            self._normalize_filter_date_times(filters), self._array_fields
        )
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        if op == "count":
            if field:
                quoted_field = _quote_identifier(field, kind="openGauss aggregate field")
                sql = f"""
                    SELECT {quoted_field}, COUNT(*) AS cnt
                    FROM "{self._name}"
                    {where_clause}
                    GROUP BY {quoted_field}
                """
                try:
                    rows = self._execute(sql, where_params, fetch=True)
                except Exception as error:
                    logger.error("opengauss_adapter: aggregate_data (grouped) failed: %s", error)
                    raise

                agg: Dict[str, Any] = {}
                for row in rows:
                    key, cnt = row[0], row[1]
                    if cond:
                        gt = cond.get("gt")
                        gte = cond.get("gte")
                        lt = cond.get("lt")
                        lte = cond.get("lte")
                        if gt is not None and cnt <= gt:
                            continue
                        if gte is not None and cnt < gte:
                            continue
                        if lt is not None and cnt >= lt:
                            continue
                        if lte is not None and cnt > lte:
                            continue
                    agg[str(key)] = cnt
                return AggregateResult(agg=agg, op=op, field=field)
            else:
                sql = f'SELECT COUNT(*) FROM "{self._name}" {where_clause}'
                try:
                    rows = self._execute(sql, where_params, fetch=True)
                    total = rows[0][0] if rows else 0
                except Exception as error:
                    logger.error("opengauss_adapter: aggregate_data (count) failed: %s", error)
                    raise
                return AggregateResult(agg={"_total": int(total)}, op=op, field=None)

        logger.warning("opengauss_adapter: unsupported aggregate op=%r", op)
        return AggregateResult(agg={"_total": 0}, op=op, field=field)
