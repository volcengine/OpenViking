# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
PostgreSQL + pgvector storage backend for OpenViking SaaS mode.

Implements VikingDBInterface using PostgreSQL as both metadata store
and vector similarity search engine (via pgvector extension).

Each collection maps to a table named ov_{collection_name}.
Vectors are stored using the pgvector 'vector' column type.
"""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore

from openviking.storage.vikingdb_interface import (
    CollectionNotFoundError,
    VikingDBInterface,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Known scalar columns - used for safe query building
_SCALAR_COLUMNS = frozenset(
    {
        "id",
        "uri",
        "parent_uri",
        "type",
        "context_type",
        "level",
        "name",
        "description",
        "tags",
        "abstract",
        "active_count",
        "created_at",
        "updated_at",
    }
)

# Columns returned by default (no vector, no extra_data unpacked)
_DEFAULT_SELECT = (
    "id, uri, parent_uri, type, context_type, level, name, "
    "description, tags, abstract, active_count, created_at, updated_at, "
    "sparse_vector, extra_data, vector::text AS vector"
)


class PostgreSQLBackend(VikingDBInterface):
    """
    PostgreSQL + pgvector backend implementing VikingDBInterface.

    Features:
    - Stores all context metadata in PostgreSQL tables
    - Uses pgvector for dense vector similarity search (cosine)
    - Translates OpenViking filter DSL to SQL WHERE clauses
    - Supports scroll/pagination, batch operations, and URI-tree deletion
    """

    def __init__(self, dsn: str, vector_dim: int = 1024):
        """
        Initialize PostgreSQL backend.

        Args:
            dsn: PostgreSQL DSN e.g. 'postgresql://user:pass@host:5432/dbname'
            vector_dim: Dense vector dimension (must match embedding model)
        """
        if asyncpg is None:
            raise ImportError(
                "asyncpg is required for PostgreSQL backend. "
                "Install it with: pip install asyncpg"
            )
        self._dsn = dsn
        self._vector_dim = vector_dim
        self._pool: Optional[Any] = None  # asyncpg.Pool

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _ensure_pool(self):
        """Lazily create the connection pool and ensure pgvector extension."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=2,
                max_size=20,
                command_timeout=60,
                statement_cache_size=0,  # Avoid prepared-statement issues
            )
            async with self._pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            logger.info(f"PostgreSQL pool created (dim={self._vector_dim})")
        return self._pool

    def _tbl(self, collection: str) -> str:
        """Return sanitized table name for a collection."""
        safe = "".join(c for c in collection if c.isalnum() or c == "_")
        return f"ov_{safe}"

    def _record_to_dict(self, record) -> Dict[str, Any]:
        """Convert asyncpg Record to plain dict, parsing stored JSON/vector."""
        d = dict(record)

        # Parse vector from text representation '[0.1,0.2,...]'
        raw_vec = d.pop("vector", None)
        if raw_vec is not None and isinstance(raw_vec, str) and raw_vec.strip():
            try:
                d["vector"] = [
                    float(x) for x in raw_vec.strip("[]").split(",") if x.strip()
                ]
            except ValueError:
                d["vector"] = []

        # Merge extra_data back into top level for transparency
        extra = d.pop("extra_data", None)
        if extra and isinstance(extra, (dict, str)):
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {}
            for k, v in extra.items():
                if k not in d:
                    d[k] = v

        # Parse sparse_vector JSON
        sv = d.get("sparse_vector")
        if isinstance(sv, str):
            try:
                d["sparse_vector"] = json.loads(sv)
            except Exception:
                d["sparse_vector"] = {}

        # Remove None values
        return {k: v for k, v in d.items() if v is not None}

    # -------------------------------------------------------------------------
    # Filter DSL → SQL translation
    # -------------------------------------------------------------------------

    def _filter_to_sql(self, filt: Optional[Dict[str, Any]], params: list) -> str:
        """
        Translate OpenViking filter DSL to a SQL WHERE clause (without 'WHERE').

        Filter DSL operators:
          and / or : logical combination of sub-conditions
          must     : field IN [values]
          range    : field comparison (gte, gt, lte, lt)
          prefix   : field LIKE 'prefix%'
          contains : field LIKE '%substring%'
          not      : NOT (inner)
        """
        if not filt:
            return ""
        return self._translate_node(filt, params)

    def _translate_node(self, node: Dict[str, Any], params: list) -> str:
        op = node.get("op", "")

        if op in ("and", "or"):
            parts = []
            for cond in node.get("conds", []):
                part = self._translate_node(cond, params)
                if part:
                    parts.append(f"({part})")
            if not parts:
                return ""
            sql_op = "AND" if op == "and" else "OR"
            return f" {sql_op} ".join(parts)

        elif op == "must":
            field = node.get("field", "")
            values = node.get("conds", [])
            if not values:
                return ""
            col = self._col(field)
            if len(values) == 1:
                params.append(values[0])
                return f"{col} = ${len(params)}"
            else:
                placeholders = ", ".join(
                    f"${len(params) + i + 1}" for i in range(len(values))
                )
                params.extend(values)
                return f"{col} IN ({placeholders})"

        elif op == "range":
            field = node.get("field", "")
            col = self._col(field)
            parts = []
            for operator, sql_op in [("gte", ">="), ("gt", ">"), ("lte", "<="), ("lt", "<")]:
                if operator in node:
                    params.append(node[operator])
                    parts.append(f"{col} {sql_op} ${len(params)}")
            return " AND ".join(parts)

        elif op == "prefix":
            field = node.get("field", "")
            prefix = node.get("prefix", "")
            col = self._col(field)
            params.append(f"{prefix}%")
            return f"{col} LIKE ${len(params)}"

        elif op == "contains":
            field = node.get("field", "")
            substring = node.get("substring", "")
            col = self._col(field)
            params.append(f"%{substring}%")
            return f"{col} LIKE ${len(params)}"

        elif op == "not":
            inner = self._translate_node(node.get("cond", {}), params)
            return f"NOT ({inner})" if inner else ""

        logger.debug(f"Unknown filter op: '{op}', skipping")
        return ""

    def _col(self, field: str) -> str:
        """Return safe SQL column reference for a field name."""
        if field in _SCALAR_COLUMNS:
            return field
        # Unknown field → look up in extra_data JSONB
        safe = "".join(c for c in field if c.isalnum() or c == "_")
        return f"extra_data->>'{safe}'"

    def _make_where(self, sql_filter: str, extra_parts: List[str] = None) -> str:
        """Build full WHERE clause from filter string and optional extra parts."""
        parts = []
        if sql_filter:
            parts.append(f"({sql_filter})")
        if extra_parts:
            parts.extend(extra_parts)
        return f"WHERE {' AND '.join(parts)}" if parts else ""

    # =========================================================================
    # Collection Management
    # =========================================================================

    async def create_collection(self, name: str, schema: Dict[str, Any]) -> bool:
        pool = await self._ensure_pool()
        tbl = self._tbl(name)

        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=$1)",
                tbl,
            )
            if exists:
                logger.debug(f"Collection '{name}' already exists")
                return False

            # Derive vector dimension from schema if available
            dim = self._vector_dim
            for field in schema.get("Fields", []):
                if field.get("FieldType") == "vector":
                    dim = field.get("Dim", dim)
                    break

            await conn.execute(
                f"""
                CREATE TABLE {tbl} (
                    id            TEXT PRIMARY KEY,
                    uri           TEXT,
                    parent_uri    TEXT,
                    type          TEXT DEFAULT 'resource',
                    context_type  TEXT DEFAULT 'resource',
                    level         INTEGER DEFAULT 2,
                    name          TEXT,
                    description   TEXT,
                    tags          TEXT,
                    abstract      TEXT,
                    active_count  BIGINT DEFAULT 0,
                    created_at    TIMESTAMPTZ DEFAULT NOW(),
                    updated_at    TIMESTAMPTZ DEFAULT NOW(),
                    vector        vector({dim}),
                    sparse_vector JSONB,
                    extra_data    JSONB DEFAULT '{{}}'::jsonb
                )
                """
            )

            # Scalar indexes
            for col in ("uri", "parent_uri", "context_type", "level", "active_count"):
                await conn.execute(
                    f"CREATE INDEX idx_{tbl}_{col} ON {tbl} ({col})"
                )

            logger.info(f"Created collection '{name}' (table={tbl}, dim={dim})")
            return True

    async def drop_collection(self, name: str) -> bool:
        pool = await self._ensure_pool()
        tbl = self._tbl(name)
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        logger.info(f"Dropped collection '{name}'")
        return True

    async def collection_exists(self, name: str) -> bool:
        pool = await self._ensure_pool()
        tbl = self._tbl(name)
        async with pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=$1)",
                    tbl,
                )
            )

    async def list_collections(self) -> List[str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'ov_%'"
            )
            return [r["table_name"][3:] for r in rows]  # strip 'ov_'

    async def get_collection_info(self, name: str) -> Optional[Dict[str, Any]]:
        if not await self.collection_exists(name):
            return None
        pool = await self._ensure_pool()
        tbl = self._tbl(name)
        async with pool.acquire() as conn:
            cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl}")
            return {
                "name": name,
                "vector_dim": self._vector_dim,
                "count": int(cnt),
                "status": "ready",
                "backend": "postgresql",
            }

    # =========================================================================
    # CRUD — Single Record
    # =========================================================================

    async def insert(self, collection: str, data: Dict[str, Any]) -> str:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)

        record_id = data.get("id")
        if not record_id:
            uri = data.get("uri", "")
            record_id = hashlib.md5(uri.encode()).hexdigest() if uri else str(uuid.uuid4())

        vector = data.get("vector")
        vector_str = f"[{','.join(str(x) for x in vector)}]" if vector else None

        sparse = data.get("sparse_vector")
        sparse_json = json.dumps(sparse) if sparse is not None else None

        known = _SCALAR_COLUMNS | {"id", "vector", "sparse_vector"}
        extra = {k: v for k, v in data.items() if k not in known}

        created_at = self._parse_dt(data.get("created_at"))
        updated_at = self._parse_dt(data.get("updated_at"))

        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {tbl} (
                    id, uri, parent_uri, type, context_type, level,
                    name, description, tags, abstract, active_count,
                    created_at, updated_at, vector, sparse_vector, extra_data
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9, $10, $11,
                    $12, $13, $14::vector, $15::jsonb, $16::jsonb
                )
                ON CONFLICT (id) DO UPDATE SET
                    uri           = EXCLUDED.uri,
                    parent_uri    = EXCLUDED.parent_uri,
                    type          = EXCLUDED.type,
                    context_type  = EXCLUDED.context_type,
                    level         = EXCLUDED.level,
                    name          = EXCLUDED.name,
                    description   = EXCLUDED.description,
                    tags          = EXCLUDED.tags,
                    abstract      = EXCLUDED.abstract,
                    active_count  = EXCLUDED.active_count,
                    updated_at    = NOW(),
                    vector        = EXCLUDED.vector,
                    sparse_vector = EXCLUDED.sparse_vector,
                    extra_data    = EXCLUDED.extra_data
                """,
                record_id,
                data.get("uri"),
                data.get("parent_uri"),
                data.get("type", "resource"),
                data.get("context_type", "resource"),
                int(data.get("level", 2)),
                data.get("name"),
                data.get("description"),
                data.get("tags"),
                data.get("abstract"),
                int(data.get("active_count", 0)),
                created_at,
                updated_at,
                vector_str,
                sparse_json,
                json.dumps(extra),
            )

        return record_id

    async def update(self, collection: str, id: str, data: Dict[str, Any]) -> bool:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)

        set_parts = []
        params: List[Any] = [id]

        updatable = [
            "uri", "parent_uri", "type", "context_type", "level",
            "name", "description", "tags", "abstract", "active_count",
        ]
        for field in updatable:
            if field in data:
                params.append(data[field])
                set_parts.append(f"{field} = ${len(params)}")

        if "vector" in data and data["vector"]:
            vec_str = f"[{','.join(str(x) for x in data['vector'])}]"
            params.append(vec_str)
            set_parts.append(f"vector = ${len(params)}::vector")

        if not set_parts:
            return False

        set_parts.append("updated_at = NOW()")
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE {tbl} SET {', '.join(set_parts)} WHERE id = $1",
                *params,
            )
        return result.split()[-1] != "0"

    async def upsert(self, collection: str, data: Dict[str, Any]) -> str:
        return await self.insert(collection, data)

    async def delete(self, collection: str, ids: List[str]) -> int:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        async with pool.acquire() as conn:
            placeholders = ", ".join(f"${i+1}" for i in range(len(ids)))
            result = await conn.execute(
                f"DELETE FROM {tbl} WHERE id IN ({placeholders})", *ids
            )
        return int(result.split()[-1])

    async def get(self, collection: str, ids: List[str]) -> List[Dict[str, Any]]:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        async with pool.acquire() as conn:
            placeholders = ", ".join(f"${i+1}" for i in range(len(ids)))
            rows = await conn.fetch(
                f"SELECT {_DEFAULT_SELECT} FROM {tbl} WHERE id IN ({placeholders})",
                *ids,
            )
        return [self._record_to_dict(r) for r in rows]

    async def exists(self, collection: str, id: str) -> bool:
        if not await self.collection_exists(collection):
            return False
        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        async with pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    f"SELECT EXISTS (SELECT 1 FROM {tbl} WHERE id = $1)", id
                )
            )

    # =========================================================================
    # CRUD — Batch
    # =========================================================================

    async def batch_insert(
        self, collection: str, data: List[Dict[str, Any]]
    ) -> List[str]:
        return [await self.insert(collection, item) for item in data]

    async def batch_upsert(
        self, collection: str, data: List[Dict[str, Any]]
    ) -> List[str]:
        return await self.batch_insert(collection, data)

    async def batch_delete(self, collection: str, filter: Dict[str, Any]) -> int:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        params: List[Any] = []
        sql_filter = self._filter_to_sql(filter, params)
        where = self._make_where(sql_filter)

        async with pool.acquire() as conn:
            result = await conn.execute(f"DELETE FROM {tbl} {where}", *params)
        return int(result.split()[-1])

    async def remove_by_uri(self, collection: str, uri: str) -> int:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        # Escape LIKE special characters
        escaped = uri.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {tbl} WHERE uri = $1 OR uri LIKE $2 ESCAPE '\\'",
                uri,
                f"{escaped}/%",
            )
        return int(result.split()[-1])

    # =========================================================================
    # Search Operations
    # =========================================================================

    async def search(
        self,
        collection: str,
        query_vector: Optional[List[float]] = None,
        sparse_query_vector: Optional[Dict[str, float]] = None,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        with_vector: bool = False,
    ) -> List[Dict[str, Any]]:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)

        params: List[Any] = []
        sql_filter = self._filter_to_sql(filter, params)

        select_cols = _DEFAULT_SELECT

        if query_vector:
            vec_str = f"[{','.join(str(x) for x in query_vector)}]"
            params.append(vec_str)
            vp_idx = len(params)
            vp = f"${vp_idx}::vector"

            where = self._make_where(sql_filter, ["vector IS NOT NULL"])

            params.extend([limit, offset])
            lp, op = f"${len(params)-1}", f"${len(params)}"

            query = (
                f"SELECT {select_cols}, "
                f"ROUND((1 - (vector <=> {vp}))::numeric, 4) AS _score "
                f"FROM {tbl} {where} "
                f"ORDER BY vector <=> {vp} "
                f"LIMIT {lp} OFFSET {op}"
            )
        else:
            where = self._make_where(sql_filter)
            params.extend([limit, offset])
            lp, op = f"${len(params)-1}", f"${len(params)}"
            query = (
                f"SELECT {select_cols} "
                f"FROM {tbl} {where} "
                f"ORDER BY active_count DESC, created_at DESC "
                f"LIMIT {lp} OFFSET {op}"
            )

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._record_to_dict(r) for r in rows]

    async def filter(
        self,
        collection: str,
        filter: Dict[str, Any],
        limit: int = 10,
        offset: int = 0,
        output_fields: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[Dict[str, Any]]:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)

        params: List[Any] = []
        sql_filter = self._filter_to_sql(filter, params)
        where = self._make_where(sql_filter)

        safe_sort = _SCALAR_COLUMNS
        if order_by and order_by in safe_sort:
            direction = "DESC" if order_desc else "ASC"
            order_clause = f"ORDER BY {order_by} {direction}"
        else:
            order_clause = "ORDER BY active_count DESC, created_at DESC"

        params.extend([limit, offset])
        lp, op = f"${len(params)-1}", f"${len(params)}"

        query = (
            f"SELECT {_DEFAULT_SELECT} "
            f"FROM {tbl} {where} {order_clause} "
            f"LIMIT {lp} OFFSET {op}"
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._record_to_dict(r) for r in rows]

    async def scroll(
        self,
        collection: str,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)

        params: List[Any] = []
        sql_filter = self._filter_to_sql(filter, params)
        where = self._make_where(sql_filter)

        offset = int(cursor) if cursor and cursor.isdigit() else 0
        fetch_limit = limit + 1
        params.extend([fetch_limit, offset])
        lp, op = f"${len(params)-1}", f"${len(params)}"

        query = (
            f"SELECT {_DEFAULT_SELECT} "
            f"FROM {tbl} {where} ORDER BY id "
            f"LIMIT {lp} OFFSET {op}"
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        records = [self._record_to_dict(r) for r in rows[:limit]]
        next_cursor = str(offset + limit) if len(rows) > limit else None
        return records, next_cursor

    # =========================================================================
    # Aggregation
    # =========================================================================

    async def count(self, collection: str, filter: Optional[Dict[str, Any]] = None) -> int:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")

        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        params: List[Any] = []
        sql_filter = self._filter_to_sql(filter, params)
        where = self._make_where(sql_filter)

        async with pool.acquire() as conn:
            return int(await conn.fetchval(f"SELECT COUNT(*) FROM {tbl} {where}", *params))

    # =========================================================================
    # Index Operations
    # =========================================================================

    async def create_index(
        self, collection: str, field: str, index_type: str, **kwargs
    ) -> bool:
        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        col = self._col(field)
        safe_field = "".join(c for c in field if c.isalnum() or c == "_")
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{tbl}_{safe_field} "
                    f"ON {tbl} ({col})"
                )
                return True
            except Exception as e:
                logger.warning(f"Failed to create index on {field}: {e}")
                return False

    async def drop_index(self, collection: str, field: str) -> bool:
        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        safe_field = "".join(c for c in field if c.isalnum() or c == "_")
        async with pool.acquire() as conn:
            await conn.execute(f"DROP INDEX IF EXISTS idx_{tbl}_{safe_field}")
        return True

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def clear(self, collection: str) -> bool:
        if not await self.collection_exists(collection):
            raise CollectionNotFoundError(f"Collection '{collection}' not found")
        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        async with pool.acquire() as conn:
            await conn.execute(f"TRUNCATE TABLE {tbl}")
        return True

    async def optimize(self, collection: str) -> bool:
        pool = await self._ensure_pool()
        tbl = self._tbl(collection)
        async with pool.acquire() as conn:
            await conn.execute(f"VACUUM ANALYZE {tbl}")
        return True

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL connection pool closed")

    # =========================================================================
    # Health & Status
    # =========================================================================

    async def health_check(self) -> bool:
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")
            return False

    async def get_stats(self) -> Dict[str, Any]:
        try:
            pool = await self._ensure_pool()
            collections = await self.list_collections()
            total_records = 0
            async with pool.acquire() as conn:
                for coll in collections:
                    tbl = self._tbl(coll)
                    cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl}")
                    total_records += int(cnt)
                db_size = await conn.fetchval(
                    "SELECT pg_database_size(current_database())"
                )
            return {
                "backend": "postgresql",
                "collections": len(collections),
                "total_records": total_records,
                "storage_size": int(db_size),
            }
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL stats: {e}")
            return {"backend": "postgresql", "error": str(e)}

    # =========================================================================
    # Private helpers
    # =========================================================================

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        """Parse various datetime representations to datetime object."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        return None
