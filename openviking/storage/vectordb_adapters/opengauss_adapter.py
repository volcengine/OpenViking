# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""OpenGauss vector database adapter using psycopg2.

OpenGauss has built-in vector type and HNSW/IVFFlat index support.
No pgvector extension is required.

Driver: openGauss-connector-python-psycopg2
  Install: pip install git+https://gitcode.com/opengauss/openGauss-connector-python-psycopg2.git
  The package exposes the standard `psycopg2` module interface.
"""

from __future__ import annotations

import json
import logging
import random
import threading
from typing import Any, Dict, List, Optional

from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.index.index import IIndex
from openviking.storage.vectordb_adapters.base import CollectionAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal metadata table names
# ---------------------------------------------------------------------------
_META_TABLE = "_ov_collection_meta"

# ---------------------------------------------------------------------------
# Field type → PostgreSQL/openGauss column type mapping
# ---------------------------------------------------------------------------
_FIELD_TYPE_MAP: Dict[str, str] = {
    "string": "TEXT",
    "path": "TEXT",
    "int64": "BIGINT",
    "int32": "INTEGER",
    "float": "DOUBLE PRECISION",
    "bool": "BOOLEAN",
    "date_time": "TIMESTAMP WITH TIME ZONE",  # Changed from BIGINT for ISO format compatibility
    "vector": None,  # handled separately
    "sparse_vector": "JSONB",
}

# ---------------------------------------------------------------------------
# Distance metric → pgvector operator
# ---------------------------------------------------------------------------
_DISTANCE_OP: Dict[str, str] = {
    "l2": "<->",
    "ip": "<#>",
    "cosine": "<=>",
}

# ---------------------------------------------------------------------------
# pgvector index type → index ops class
# ---------------------------------------------------------------------------
_VECTOR_OPS: Dict[str, Dict[str, str]] = {
    "l2": {"hnsw": "vector_l2_ops", "ivfflat": "vector_l2_ops"},
    "ip": {"hnsw": "vector_ip_ops", "ivfflat": "vector_ip_ops"},
    "cosine": {"hnsw": "vector_cosine_ops", "ivfflat": "vector_cosine_ops"},
}


def _import_psycopg2():
    """Import psycopg2 (openGauss connector)."""
    try:
        import psycopg2  # noqa: PLC0415

        return psycopg2
    except ImportError as e:
        raise ImportError(
            "psycopg2 is required for the openGauss backend. "
            "Install the openGauss connector via:\n"
            "  pip install git+https://gitcode.com/opengauss/"
            "openGauss-connector-python-psycopg2.git"
        ) from e


def _field_to_column_ddl(field: Dict[str, Any]) -> Optional[str]:
    """Convert an OpenViking field definition to a SQL column definition.

    Returns None for vector fields (handled separately) and
    for unrecognised types (skipped with a warning).
    """
    name = field.get("FieldName") or field.get("field_name") or field.get("name", "")
    ftype = field.get("FieldType") or field.get("field_type") or field.get("type", "string")

    if ftype == "vector":
        return None

    # Skip 'id' field as it's already defined as PRIMARY KEY in the table schema
    if name == "id":
        return None

    col_type = _FIELD_TYPE_MAP.get(ftype, "TEXT")
    quoted = f'"{name}"'
    return f"{quoted} {col_type}"


def _build_where_clause(filters: Optional[Dict[str, Any]]) -> tuple[str, list]:
    """Recursively convert OpenViking filter DSL to a SQL WHERE clause.

    Returns (sql_fragment, params_list).
    """
    if not filters:
        return "", []

    op = filters.get("op", "")

    if op == "and":
        parts, params = [], []
        for cond in filters.get("conds", []):
            frag, p = _build_where_clause(cond)
            if frag:
                parts.append(f"({frag})")
                params.extend(p)
        if not parts:
            return "", []
        return " AND ".join(parts), params

    if op == "or":
        parts, params = [], []
        for cond in filters.get("conds", []):
            frag, p = _build_where_clause(cond)
            if frag:
                parts.append(f"({frag})")
                params.extend(p)
        if not parts:
            return "", []
        return " OR ".join(parts), params

    field = filters.get("field", "")
    quoted_field = f'"{field}"'

    if op == "must":
        conds = filters.get("conds", [])
        para = filters.get("para", "")
        if "-d=" in para and len(conds) == 1:
            # PathScope: depth=0 → exact match, depth!=0 → prefix LIKE match
            depth_str = para.split("-d=")[-1].strip()
            try:
                depth = int(depth_str)
            except ValueError:
                depth = 0
            prefix = conds[0]
            if depth == 0:
                return f"{quoted_field} = %s", [prefix]
            like_prefix = prefix.replace("%", r"\%").replace("_", r"\_")
            return f"{quoted_field} LIKE %s ESCAPE '\\'", [f"{like_prefix}%"]
        if conds:
            placeholders = ", ".join(["%s"] * len(conds))
            return f"{quoted_field} IN ({placeholders})", list(conds)
        return "", []

    if op == "range":
        parts, params = [], []
        if "gte" in filters:
            parts.append(f"{quoted_field} >= %s")
            params.append(filters["gte"])
        if "gt" in filters:
            parts.append(f"{quoted_field} > %s")
            params.append(filters["gt"])
        if "lte" in filters:
            parts.append(f"{quoted_field} <= %s")
            params.append(filters["lte"])
        if "lt" in filters:
            parts.append(f"{quoted_field} < %s")
            params.append(filters["lt"])
        return " AND ".join(parts), params

    if op == "contains":
        substring = filters.get("substring", "")
        return f"{quoted_field} LIKE %s", [f"%{substring}%"]

    logger.warning("opengauss_adapter: unsupported filter op=%r, skipping", op)
    return "", []


# ---------------------------------------------------------------------------
# Dummy IIndex implementation (metadata only, no in-memory structure needed)
# ---------------------------------------------------------------------------
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

    def search(self, query_vector=None, limit=10, filters=None, sparse_raw_terms=None, sparse_values=None):
        """Not used - search handled by OpenGaussCollection."""
        return [], []

    def aggregate(self, filters=None):
        """Not used - aggregation handled by OpenGaussCollection."""
        return {}

    def update(self, scalar_index=None, description=None):
        """Not used - updates handled by OpenGaussCollection."""
        pass

    def close(self):
        """No-op for dummy index."""
        pass

    def drop(self):
        """No-op for dummy index."""
        pass


# ---------------------------------------------------------------------------
# ICollection implementation backed by openGauss via psycopg2
# ---------------------------------------------------------------------------
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
        self._name = collection_name
        self._meta = meta
        self._dim = dim
        self._distance = distance
        self._distributed = distributed
        self._lock = threading.Lock()
        # index name → meta dict
        self._indexes: Dict[str, Dict[str, Any]] = {}
        self._load_index_meta()

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

    def _load_index_meta(self):
        """Load persisted index metadata from the database."""
        idx_table = f"_ov_index_{self._name}"
        try:
            rows = self._execute(
                f'SELECT index_name, meta_json FROM "{idx_table}"',
                fetch=True,
            )
            for row in rows:
                self._indexes[row[0]] = json.loads(row[1])
        except Exception:
            # Table doesn't exist yet or first load failure – ignore
            pass

    def _ensure_index_meta_table(self):
        """Create the per-collection index metadata table if needed.

        In distributed mode the table is turned into a reference table so it is
        replicated to all worker nodes.
        """
        idx_table = f"_ov_index_{self._name}"
        self._execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{idx_table}" (
                index_name VARCHAR(256) PRIMARY KEY,
                meta_json  TEXT NOT NULL
            )
            """
        )
        if self._distributed:
            _try_make_reference_table(self._conn, idx_table)

    def _save_index_meta(self, index_name: str, meta: Dict[str, Any]):
        self._ensure_index_meta_table()
        idx_table = f"_ov_index_{self._name}"
        meta_json = json.dumps(meta)
        # Use UPDATE → INSERT for distributed compatibility
        with self._lock:
            cur = self._cursor()
            try:
                cur.execute(
                    f'UPDATE "{idx_table}" SET meta_json = %s WHERE index_name = %s',
                    (meta_json, index_name),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        f'INSERT INTO "{idx_table}" (index_name, meta_json) VALUES (%s, %s)',
                        (index_name, meta_json),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def _delete_index_meta(self, index_name: str):
        idx_table = f"_ov_index_{self._name}"
        try:
            self._execute(
                f'DELETE FROM "{idx_table}" WHERE index_name = %s',
                (index_name,),
            )
        except Exception:
            pass

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

    def _select_output_columns(self, output_fields: Optional[List[str]]) -> str:
        """Build the SELECT column list from output_fields."""
        if not output_fields:
            return "*"
        cols = ["id"] + [f'"{f}"' for f in output_fields if f != "id"]
        return ", ".join(cols)

    def _row_to_dict(self, row, columns: List[str]) -> Dict[str, Any]:
        return {col: val for col, val in zip(columns, row)}

    # ------------------------------------------------------------------
    # ICollection: collection lifecycle
    # ------------------------------------------------------------------

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        if fields:
            self._meta.update(fields)
        if description is not None:
            self._meta["description"] = description
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
        idx_table = f"_ov_index_{self._name}"
        self._execute(f'DROP TABLE IF EXISTS "{self._name}" CASCADE')
        self._execute(f'DROP TABLE IF EXISTS "{idx_table}" CASCADE')
        self._execute(
            f'DELETE FROM "{_META_TABLE}" WHERE table_name = %s',
            (self._name,),
        )
        self._indexes.clear()

    # ------------------------------------------------------------------
    # ICollection: index management
    # ------------------------------------------------------------------

    def create_index(self, index_name: str, meta_data: Dict[str, Any]) -> IIndex:
        vector_meta = meta_data.get("VectorIndex", {})
        distance = vector_meta.get("Distance", self._distance)
        index_type = vector_meta.get("IndexType", "hnsw").lower()
        if "hnsw" in index_type:
            pg_index_type = "hnsw"
        elif "ivf" in index_type:
            pg_index_type = "ivfflat"
        else:
            pg_index_type = "hnsw"

        ops_map = _VECTOR_OPS.get(distance, _VECTOR_OPS["cosine"])
        ops_class = ops_map.get(pg_index_type, "vector_cosine_ops")
        pg_idx_name = f"idx_{self._name}_{index_name}_vec"

        try:
            if pg_index_type == "hnsw":
                m = meta_data.get("m", 16)
                ef = meta_data.get("ef_construction", 64)
                self._execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS "{pg_idx_name}"
                    ON "{self._name}"
                    USING hnsw (vector {ops_class})
                    WITH (m = {int(m)}, ef_construction = {int(ef)})
                    """
                )
            else:
                lists = meta_data.get("lists", 100)
                self._execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS "{pg_idx_name}"
                    ON "{self._name}"
                    USING ivfflat (vector {ops_class})
                    WITH (lists = {int(lists)})
                    """
                )
        except Exception as e:
            logger.warning("opengauss_adapter: failed to create vector index: %s", e)

        scalar_fields = meta_data.get("ScalarIndex", [])
        for sf in scalar_fields:
            sf_idx_name = f'idx_{self._name}_{index_name}_{sf}'
            try:
                self._execute(
                    f'CREATE INDEX IF NOT EXISTS "{sf_idx_name}" ON "{self._name}" ("{sf}")'
                )
            except Exception as e:
                logger.warning(
                    "opengauss_adapter: failed to create scalar index on %s: %s", sf, e
                )

        full_meta = dict(meta_data)
        full_meta["IndexName"] = index_name
        full_meta["_pg_index_name"] = pg_idx_name
        full_meta["_distance"] = distance
        self._indexes[index_name] = full_meta
        self._save_index_meta(index_name, full_meta)
        return _PgIndex(index_name, full_meta)

    def has_index(self, index_name: str) -> bool:
        return index_name in self._indexes

    def get_index(self, index_name: str) -> Optional[IIndex]:
        meta = self._indexes.get(index_name)
        if meta is None:
            return None
        return _PgIndex(index_name, meta)

    def list_indexes(self) -> List[str]:
        return list(self._indexes.keys())

    def drop_index(self, index_name: str):
        meta = self._indexes.pop(index_name, None)
        if meta:
            pg_idx_name = meta.get("_pg_index_name", "")
            if pg_idx_name:
                try:
                    self._execute(f'DROP INDEX IF EXISTS "{pg_idx_name}"')
                except Exception as e:
                    logger.warning("opengauss_adapter: failed to drop index %s: %s", pg_idx_name, e)
            self._delete_index_meta(index_name)

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        if index_name in self._indexes:
            if scalar_index:
                self._indexes[index_name].update(scalar_index)
            if description is not None:
                self._indexes[index_name]["description"] = description
            self._save_index_meta(index_name, self._indexes[index_name])

    def get_index_meta_data(self, index_name: str) -> Dict[str, Any]:
        return dict(self._indexes.get(index_name, {}))

    # ------------------------------------------------------------------
    # ICollection: search
    # ------------------------------------------------------------------

    def _resolve_distance_and_op(self, index_name: str) -> tuple[str, str]:
        idx_meta = self._indexes.get(index_name, {})
        distance = idx_meta.get("_distance") or idx_meta.get(
            "Distance", self._distance
        )
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
        if not dense_vector:
            return SearchResult(data=[])

        _, op = self._resolve_distance_and_op(index_name)
        all_cols = self._get_all_columns()
        select_cols = self._select_output_columns(output_fields)

        where_frag, where_params = _build_where_clause(filters)
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        vector_str = "[" + ",".join(str(v) for v in dense_vector) + "]"
        sql = f"""
            SELECT {select_cols}, vector {op} %s::vector AS _distance
            FROM "{self._name}"
            {where_clause}
            ORDER BY _distance
            LIMIT %s OFFSET %s
        """
        params = [vector_str] + where_params + [limit, offset]

        try:
            rows = self._execute(sql, params, fetch=True)
        except Exception as e:
            logger.warning("opengauss_adapter: search_by_vector failed: %s", e)
            return SearchResult(data=[])

        if not rows:
            return SearchResult(data=[])

        # Determine actual column list from query result
        with self._lock:
            cur = self._cursor()
            cur.execute(sql, params)
            col_names = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            cur.close()

        items = []
        for row in rows:
            record = self._row_to_dict(row, col_names)
            distance = record.pop("_distance", 0.0)
            record_id = record.pop("id", None)
            # Convert cosine distance to similarity: similarity = 1 - distance
            # Cosine distance range is [0, 2], similarity range is [-1, 1]
            similarity = 1.0 - float(distance or 0)
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
        where_frag, where_params = _build_where_clause(filters)
        where_clause = f"WHERE {where_frag}" if where_frag else ""
        sort_dir = "DESC" if (order or "desc").lower() == "desc" else "ASC"

        sql = f"""
            SELECT {select_cols}, "{field}" AS _scalar_val
            FROM "{self._name}"
            {where_clause}
            ORDER BY "{field}" {sort_dir}
            LIMIT %s OFFSET %s
        """
        params = where_params + [limit, offset]

        try:
            with self._lock:
                cur = self._cursor()
                cur.execute(sql, params)
                col_names = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                self._conn.commit()
                cur.close()
        except Exception as e:
            logger.warning("opengauss_adapter: search_by_scalar failed: %s", e)
            return SearchResult(data=[])

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
        where_frag, where_params = _build_where_clause(filters)
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
                cur.execute(sql, params)
                col_names = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                self._conn.commit()
                cur.close()
        except Exception as e:
            logger.warning("opengauss_adapter: search_by_random failed: %s", e)
            return SearchResult(data=[])

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
        # Fallback to random when no keyword index is available
        return self.search_by_random(index_name, limit, offset, filters, output_fields)

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        # Fetch the source vector and use it for similarity search
        try:
            rows = self._execute(
                f'SELECT vector FROM "{self._name}" WHERE id = %s',
                (str(id),),
                fetch=True,
            )
        except Exception:
            return SearchResult(data=[])

        if not rows or rows[0][0] is None:
            return SearchResult(data=[])

        vec = rows[0][0]
        if hasattr(vec, "tolist"):
            vec = vec.tolist()
        elif isinstance(vec, str):
            vec = [float(x) for x in vec.strip("[]").split(",")]

        return self.search_by_vector(
            index_name,
            dense_vector=vec,
            limit=limit + 1,
            offset=offset,
            filters=filters,
            output_fields=output_fields,
        )

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
        return self.search_by_random(index_name, limit, offset, filters, output_fields)

    # ------------------------------------------------------------------
    # ICollection: data operations
    # ------------------------------------------------------------------

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        if not data_list:
            return

        all_cols = self._get_all_columns()
        # Get column types for proper casting
        col_types = self._get_column_types()
        
        for record in data_list:
            record_id = record.get("id") or record.get("_id")
            if not record_id:
                import uuid

                record_id = str(uuid.uuid4())

            vector_val = record.get("vector")
            extra_fields = {
                k: v
                for k, v in record.items()
                if k not in ("id", "_id", "vector") and k in all_cols
            }

            col_names = ["id"] + list(extra_fields.keys())
            col_placeholders = []
            values = [str(record_id)]
            
            # Build placeholders with proper type casting
            col_placeholders.append("%s")  # id
            for col_name in list(extra_fields.keys()):
                col_type = col_types.get(col_name, "")
                if "timestamp" in col_type.lower():
                    col_placeholders.append("%s::timestamp with time zone")
                else:
                    col_placeholders.append("%s")
                values.append(extra_fields[col_name])

            if vector_val is not None and "vector" in all_cols:
                col_names.append("vector")
                col_placeholders.append("%s::vector")
                if isinstance(vector_val, (list, tuple)):
                    values.append("[" + ",".join(str(v) for v in vector_val) + "]")
                else:
                    values.append(str(vector_val))

            cols_sql = ", ".join(f'"{c}"' for c in col_names)
            insert_placeholders = ", ".join(col_placeholders)

            try:
                # Use UPDATE → INSERT for both standalone and distributed modes.
                # This avoids MERGE's constant-SELECT restriction on distributed
                # tables and is semantically equivalent on standalone OpenGauss.
                update_cols = [c for c in col_names if c != "id"]
                update_placeholders = [
                    p for c, p in zip(col_names, col_placeholders) if c != "id"
                ]
                update_set = ", ".join(
                    f'"{c}" = {p}' for c, p in zip(update_cols, update_placeholders)
                )
                update_values = [v for c, v in zip(col_names, values) if c != "id"] + [str(record_id)]
                with self._lock:
                    cur = self._cursor()
                    try:
                        if update_set:
                            cur.execute(
                                f'UPDATE "{self._name}" SET {update_set} WHERE "id" = %s',
                                update_values,
                            )
                            updated = cur.rowcount
                        else:
                            updated = 0
                        if updated == 0:
                            cur.execute(
                                f'INSERT INTO "{self._name}" ({cols_sql}) VALUES ({insert_placeholders})',
                                values,
                            )
                        self._conn.commit()
                    except Exception:
                        self._conn.rollback()
                        raise
                    finally:
                        cur.close()
            except Exception as e:
                logger.warning("opengauss_adapter: upsert failed for id=%s: %s", record_id, e)
                raise

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        if not primary_keys:
            return FetchDataInCollectionResult(items=[], ids_not_exist=[])

        str_keys = [str(k) for k in primary_keys]
        placeholders = ", ".join(["%s"] * len(str_keys))
        sql = f'SELECT * FROM "{self._name}" WHERE id IN ({placeholders})'

        try:
            with self._lock:
                cur = self._cursor()
                cur.execute(sql, str_keys)
                col_names = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                self._conn.commit()
                cur.close()
        except Exception as e:
            logger.warning("opengauss_adapter: fetch_data failed: %s", e)
            return FetchDataInCollectionResult(items=[], ids_not_exist=list(str_keys))

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
        where_frag, where_params = _build_where_clause(filters)
        where_clause = f"WHERE {where_frag}" if where_frag else ""

        if op == "count":
            if field:
                sql = f"""
                    SELECT "{field}", COUNT(*) AS cnt
                    FROM "{self._name}"
                    {where_clause}
                    GROUP BY "{field}"
                """
                try:
                    rows = self._execute(sql, where_params, fetch=True)
                except Exception as e:
                    logger.warning("opengauss_adapter: aggregate_data (grouped) failed: %s", e)
                    return AggregateResult(agg={"_total": 0}, op=op, field=field)

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
                except Exception as e:
                    logger.warning("opengauss_adapter: aggregate_data (count) failed: %s", e)
                    total = 0
                return AggregateResult(agg={"_total": int(total)}, op=op, field=None)

        logger.warning("opengauss_adapter: unsupported aggregate op=%r", op)
        return AggregateResult(agg={"_total": 0}, op=op, field=field)


# ---------------------------------------------------------------------------
# Distributed table helpers (spq_plugin_v2 / Citus extension)
# ---------------------------------------------------------------------------

def _is_table_already_distributed(conn, table_name: str) -> bool:
    """Return True if *table_name* is already a distributed or reference table.

    Checks ``pg_dist_partition`` which is populated by the spq_plugin_v2 /
    Citus extension when a table is distributed or made into a reference table.
    The function returns False gracefully when the extension is not installed.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT 1 FROM pg_dist_partition
            WHERE logicalrelid = %s::regclass
            """,
            (table_name,),
        )
        row = cur.fetchone()
        conn.commit()
        return row is not None
    except Exception:
        conn.rollback()
        return False
    finally:
        cur.close()


def _try_make_distributed_table(conn, table_name: str, shard_count: int = 32) -> None:
    """Convert *table_name* to a distributed table via ``create_distributed_table``.

    Distributes by the ``id`` column (hash partitioning) with the given
    *shard_count*.  This is a no-op if the table is already distributed.
    Failures are logged as warnings rather than raised so that the adapter
    degrades gracefully when the spq_plugin_v2 extension is absent.
    """
    if _is_table_already_distributed(conn, table_name):
        logger.info("opengauss_adapter: table '%s' is already distributed, skipping", table_name)
        return
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT create_distributed_table(%s, 'id', 'hash', {int(shard_count)})",
            (table_name,),
        )
        conn.commit()
        logger.info(
            "opengauss_adapter: distributed table '%s' created with %d shards",
            table_name,
            shard_count,
        )
    except Exception as e:
        conn.rollback()
        logger.warning(
            "opengauss_adapter: failed to distribute table '%s': %s "
            "(ensure the spq_plugin_v2 extension is installed on the CN node)",
            table_name,
            e,
        )
    finally:
        cur.close()


def _try_make_reference_table(conn, table_name: str) -> None:
    """Convert *table_name* to a reference table via ``create_reference_table``.

    Reference tables are replicated to all worker nodes and are suitable for
    small metadata tables that are read frequently and written rarely.
    This is a no-op if the table is already distributed/reference.
    """
    if _is_table_already_distributed(conn, table_name):
        logger.info(
            "opengauss_adapter: table '%s' is already a reference/distributed table, skipping",
            table_name,
        )
        return
    cur = conn.cursor()
    try:
        cur.execute("SELECT create_reference_table(%s)", (table_name,))
        conn.commit()
        logger.info("opengauss_adapter: reference table '%s' created", table_name)
    except Exception as e:
        conn.rollback()
        logger.warning(
            "opengauss_adapter: failed to create reference table '%s': %s "
            "(ensure the spq_plugin_v2 extension is installed on the CN node)",
            table_name,
            e,
        )
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Helper: create collection table
# ---------------------------------------------------------------------------

def _create_collection_table(
    conn,
    name: str,
    meta: Dict[str, Any],
    dim: int,
    distributed: bool = False,
    shard_count: int = 32,
):
    """Create the collection data table and optionally distribute it.

    In distributed mode the table is converted to a distributed table keyed on
    ``id`` after creation.  The caller must have already created / ensured the
    metadata tables so that those can be set up as reference tables first.
    """
    fields: List[Dict[str, Any]] = meta.get("Fields", [])
    col_ddls = []
    has_vector = False

    for field in fields:
        ftype = field.get("FieldType") or field.get("field_type") or field.get("type", "string")
        if ftype == "vector":
            has_vector = True
            continue
        ddl = _field_to_column_ddl(field)
        if ddl:
            col_ddls.append(ddl)

    if has_vector and dim > 0:
        col_ddls.append(f"vector vector({dim})")

    col_defs = ", ".join(col_ddls) if col_ddls else ""
    sep = ", " if col_defs else ""
    sql = f"""
        CREATE TABLE IF NOT EXISTS "{name}" (
            id VARCHAR(256) PRIMARY KEY{sep}{col_defs}
        )
    """
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if distributed:
        _try_make_distributed_table(conn, name, shard_count)


def _ensure_meta_table(conn, distributed: bool = False):
    """Create the global collection metadata table if it doesn't exist.

    In distributed mode the table is turned into a reference table so that it
    is replicated to all worker nodes and queries from any shard can read it.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_META_TABLE}" (
                table_name VARCHAR(256) PRIMARY KEY,
                meta_json  TEXT NOT NULL
            )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if distributed:
        _try_make_reference_table(conn, _META_TABLE)


def _save_collection_meta(conn, name: str, meta: Dict[str, Any], distributed: bool = False):
    _ensure_meta_table(conn, distributed=distributed)
    cur = conn.cursor()
    try:
        # Use UPDATE → INSERT for distributed compatibility
        meta_json = json.dumps(meta)
        cur.execute(
            f'UPDATE "{_META_TABLE}" SET meta_json = %s WHERE table_name = %s',
            (meta_json, name),
        )
        if cur.rowcount == 0:
            cur.execute(
                f'INSERT INTO "{_META_TABLE}" (table_name, meta_json) VALUES (%s, %s)',
                (name, meta_json),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _load_collection_meta(conn, name: str, distributed: bool = False) -> Optional[Dict[str, Any]]:
    _ensure_meta_table(conn, distributed=distributed)
    cur = conn.cursor()
    try:
        cur.execute(
            f'SELECT meta_json FROM "{_META_TABLE}" WHERE table_name = %s',
            (name,),
        )
        row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        return None
    finally:
        cur.close()

    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# CollectionAdapter
# ---------------------------------------------------------------------------

class OpenGaussCollectionAdapter(CollectionAdapter):
    """OpenViking CollectionAdapter backed by openGauss via psycopg2 + pgvector.

    Supports two deployment modes controlled by the ``distributed`` config flag:

    * **Single-node** (``distributed=false``, default): connects to a standalone
      openGauss instance.  Tables are created as regular local tables.

    * **Distributed** (``distributed=true``): connects to the **CN (coordinator)**
      node of an openGauss cluster with the spq_plugin_v2 (Citus-compatible)
      extension installed.  Collection tables are distributed across worker nodes
      via ``create_distributed_table`` and metadata tables are replicated via
      ``create_reference_table``.
    """

    mode = "opengauss"

    def __init__(
        self,
        collection_name: str,
        host: str,
        port: int,
        user: str,
        password: str,
        db_name: str,
        distributed: bool = False,
        shard_count: int = 32,
    ):
        super().__init__(collection_name)
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._db_name = db_name
        self._distributed = distributed
        self._shard_count = shard_count
        self._conn = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self):
        psycopg2 = _import_psycopg2()
        self._conn = psycopg2.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            dbname=self._db_name,
            options='-c client_encoding=UTF8 -c search_path=public',
        )
        self._conn.autocommit = False
        _ensure_meta_table(self._conn, distributed=self._distributed)
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
    def from_config(cls, config: Any) -> "OpenGaussCollectionAdapter":
        og_cfg = config.opengauss
        collection_name = config.name or "context"
        # Support both the new ``mode`` field and the legacy ``distributed`` bool
        # so that existing configs are not broken.
        if hasattr(og_cfg, "is_distributed"):
            distributed = og_cfg.is_distributed
        else:
            distributed = getattr(og_cfg, "distributed", False)
        return cls(
            collection_name=collection_name,
            host=og_cfg.host,
            port=og_cfg.port,
            user=og_cfg.user,
            password=og_cfg.password,
            db_name=og_cfg.db_name,
            distributed=distributed,
            shard_count=getattr(og_cfg, "shard_count", 32),
        )

    def _table_exists(self, table_name: str) -> bool:
        """Return True if *table_name* exists in the current schema."""
        try:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = %s AND table_schema = current_schema()
                """,
                (table_name,),
            )
            row = cur.fetchone()
            self._conn.commit()
            cur.close()
            return row is not None
        except Exception:
            self._conn.rollback()
            return False

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return

        meta = _load_collection_meta(self._conn, self._collection_name, distributed=self._distributed)
        if meta is None:
            return

        # Check the actual table exists
        if not self._table_exists(self._collection_name):
            return

        dim = meta.get("_dim", 0)
        distance = meta.get("_distance", "cosine")
        og_coll = OpenGaussCollection(
            self._conn,
            self._collection_name,
            meta,
            dim,
            distance,
            distributed=self._distributed,
        )
        self._collection = Collection(og_coll)

        # Auto-create vector index if missing
        self._ensure_vector_index_exists(og_coll, distance)

    def _ensure_vector_index_exists(self, og_coll: "OpenGaussCollection", distance: str = "cosine"):
        """Ensure vector index exists on the collection table."""
        try:
            cur = self._conn.cursor()
            # Check if vector index already exists
            cur.execute(
                """
                SELECT indexname FROM pg_indexes 
                WHERE tablename = %s AND indexdef LIKE '%%USING hnsw%%'
                """,
                (self._collection_name,),
            )
            existing_index = cur.fetchone()
            self._conn.commit()
            cur.close()
            
            if existing_index:
                logger.info("opengauss_adapter: vector index already exists: %s", existing_index[0])
                return
            
            # Create HNSW vector index
            ops_map = _VECTOR_OPS.get(distance, _VECTOR_OPS["cosine"])
            ops_class = ops_map.get("hnsw", "vector_cosine_ops")
            idx_name = f"idx_{self._collection_name}_default_vec"
            
            logger.info("opengauss_adapter: creating vector index %s", idx_name)
            cur = self._conn.cursor()
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS "{idx_name}"
                ON "{self._collection_name}"
                USING hnsw (vector {ops_class})
                WITH (m = 16, ef_construction = 64)
                """
            )
            self._conn.commit()
            cur.close()
            logger.info("opengauss_adapter: vector index created successfully")
        except Exception as e:
            self._conn.rollback()
            logger.warning("opengauss_adapter: failed to ensure vector index: %s", e)

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        fields = meta.get("Fields", [])
        dim = 0
        distance = "cosine"
        for f in fields:
            ftype = f.get("FieldType") or f.get("field_type") or f.get("type", "")
            if ftype == "vector":
                # Schema may use "Dim", "dimension", or "dim"
                dim = (
                    f.get("Dimension")
                    or f.get("dimension")
                    or f.get("Dim")
                    or f.get("dim", 0)
                )
        meta["_dim"] = dim
        meta["_distance"] = distance

        _create_collection_table(
            self._conn,
            self._collection_name,
            meta,
            dim,
            distributed=self._distributed,
            shard_count=self._shard_count,
        )
        _save_collection_meta(
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
        return Collection(og_coll)

    def close(self) -> None:
        super().close()
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
