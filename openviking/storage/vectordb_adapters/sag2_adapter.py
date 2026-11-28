# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Single-instance SQLite records + FTS5/BM25 CollectionAdapter for SAG2."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from openviking.storage.abstract_overview import AbstractOverviewFormatError, body_for_preview
from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.expr import And, FilterExpr, Or

from .base import VIKINGDB_TEXT_FIELD_BYTE_LIMIT, CollectionAdapter
from .sag2_lexical import (
    CHUNK_POLICY,
    SCHEMA_VERSION,
    TOKENIZER_IMPLEMENTATION,
    TOKENIZER_PROFILE,
    index_text,
    match_query,
    quote,
    title_and_heading,
)

_ARRAY_FIELDS = {
    "search_tags",
    "acl_direct_grants",
    "acl_inherited_grants",
}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PATH_DEPTH = re.compile(r"\s*-d=(-?\d+)\s*")


class IndexChanged(RuntimeError):
    """The account index changed while a multi-round retrieval was in flight."""


class _Sag2Collection:
    """Minimal collection handle used by the existing asynchronous facade."""

    def __init__(self, adapter: "Sag2CollectionAdapter"):
        self._adapter = adapter

    def get_meta_data(self, raise_on_error: bool = False) -> Dict[str, Any]:
        return dict(self._adapter._schema)

    def get_index_meta_data(self, index_name: str) -> Dict[str, Any]:
        return {
            "IndexName": index_name,
            "ScalarIndex": list(self._adapter._schema.get("ScalarIndex", [])),
            "LexicalIndex": {"Type": "fts5", "Similarity": "bm25"},
        }

    def update(self, description: Optional[str] = None, **_: Any) -> None:
        if description is not None:
            self._adapter._schema["Description"] = description
            self._adapter._persist_schema()

    def close(self) -> None:
        self._adapter.close()


class Sag2CollectionAdapter(CollectionAdapter):
    """OV scalar records and their derived, pre-tokenized FTS projection."""

    mode = "sag2"
    USE_CONTENT_FIELD = True
    _STRING_FIELD_BYTE_LIMIT = None
    _TEXT_FIELD_BYTE_LIMIT = None
    _DATA_BATCH_SIZE = 500

    def __init__(self, collection_name: str, workspace: str, index_name: str, config: Any):
        super().__init__(collection_name, index_name)
        self._workspace = Path(workspace).expanduser().resolve()
        self._db_path = self._workspace / "sag2" / f"{collection_name}.sqlite3"
        self._sag2_config = config
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._schema: Dict[str, Any] = {}

    @classmethod
    def from_config(cls, config: Any) -> "Sag2CollectionAdapter":
        if not config.path:
            raise ValueError("SAG2 requires storage.workspace (or vectordb.path)")
        return cls(
            config.name or "context",
            config.path,
            config.index_name or "default",
            config.sag2,
        )

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path, timeout=5.0, check_same_thread=False, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ov_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn = conn
        return conn

    def _load_existing_collection_if_needed(self) -> None:
        with self._lock:
            if self._collection is not None or not self._db_path.exists():
                return
            conn = self._connect()
            row = conn.execute("SELECT value FROM ov_metadata WHERE key='schema'").fetchone()
            if row is None:
                return
            self._schema = json.loads(row[0])
            self._validate_provenance(conn)
            self._collection = _Sag2Collection(self)

    def _validate_provenance(self, conn: sqlite3.Connection) -> None:
        rows = dict(conn.execute("SELECT key,value FROM ov_metadata").fetchall())
        expected = {
            "schema_version": str(SCHEMA_VERSION),
            "tokenizer_profile": TOKENIZER_PROFILE,
            "chunk_policy": CHUNK_POLICY,
            "tokenizer_implementation": TOKENIZER_IMPLEMENTATION,
        }
        for key, value in expected.items():
            if rows.get(key) != value:
                raise RuntimeError(
                    f"SAG2 {key} mismatch (expected {value!r}, got {rows.get(key)!r}); reindex required"
                )

    def _create_backend_collection(self, meta: Dict[str, Any]) -> _Sag2Collection:
        # create_collection below owns initialization; this hook is not used.
        raise NotImplementedError

    def create_collection(
        self,
        name: str,
        schema: Dict[str, Any],
        *,
        distance: str,
        sparse_weight: float,
        index_name: str,
    ) -> bool:
        del distance, sparse_weight
        with self._lock:
            self._collection_name = name
            self._index_name = index_name
            self._db_path = self._workspace / "sag2" / f"{name}.sqlite3"
            if self.collection_exists():
                return False
            conn = self._connect()
            self._schema = dict(schema)
            self._schema["CollectionName"] = name
            fields = self._schema.setdefault("Fields", [])
            fields[:] = [
                field
                for field in fields
                if field.get("FieldType") not in {"vector", "sparse_vector"}
            ]
            self._schema["FullText"] = [
                {"Field": "content", "Analyzer": {"Tokenizer": TOKENIZER_PROFILE}}
            ]
            try:
                conn.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE records (
                        rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        account_id TEXT,
                        uri TEXT,
                        parent_uri TEXT,
                        context_type TEXT,
                        type TEXT,
                        level INTEGER,
                        name TEXT,
                        description TEXT,
                        tags TEXT,
                        search_tags TEXT,
                        abstract TEXT,
                        content TEXT,
                        md5 TEXT,
                        owner_user_id TEXT,
                        acl_mode TEXT,
                        acl_direct_grants TEXT,
                        acl_inherited_grants TEXT,
                        active_count INTEGER,
                        created_at,
                        updated_at,
                        data TEXT NOT NULL
                    );
                    CREATE VIRTUAL TABLE record_fts USING fts5(title, heading, body);
                    CREATE VIRTUAL TABLE record_fts_vocab USING fts5vocab(record_fts, 'row');
                    CREATE TABLE retrieval_epochs (
                        account_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL
                    );
                    CREATE INDEX records_account_idx ON records(account_id);
                    CREATE INDEX records_uri_idx ON records(uri);
                    CREATE INDEX records_updated_idx ON records(updated_at);
                    """
                )
                metadata = {
                    "schema": json.dumps(self._schema, ensure_ascii=False),
                    "schema_version": str(SCHEMA_VERSION),
                    "tokenizer_profile": TOKENIZER_PROFILE,
                    "chunk_policy": CHUNK_POLICY,
                    "tokenizer_implementation": TOKENIZER_IMPLEMENTATION,
                }
                conn.executemany(
                    "INSERT INTO ov_metadata(key,value) VALUES (?,?)", metadata.items()
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            self._collection = _Sag2Collection(self)
            return True

    def _persist_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO ov_metadata(key,value) VALUES('schema',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(self._schema, ensure_ascii=False),),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def drop_collection(self) -> bool:
        with self._lock:
            self._load_existing_collection_if_needed()
            if self._conn is None and not self._db_path.exists():
                return False
            self.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(str(self._db_path) + suffix).unlink()
                except FileNotFoundError:
                    pass
            return True

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._collection = None

    def _compile_filter(self, expr: FilterExpr | Dict[str, Any] | None) -> Dict[str, Any]:
        # The base compiler drops empty groups; an empty OR must remain a contradiction.
        if isinstance(expr, Or):
            return {"op": "or", "conds": [self._compile_filter(item) for item in expr.conds]}
        if isinstance(expr, And):
            return {
                "op": "and",
                "conds": [self._compile_filter(item) for item in expr.conds if item],
            }
        return super()._compile_filter(expr)

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def _write_row(self, record: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_record_for_write(record)
        for field in ("content", "abstract"):
            value = normalized.get(field)
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"SAG2 {field} must be valid UTF-8") from exc
                normalized[field] = value
            size = len(value.encode("utf-8")) if isinstance(value, str) else 0
            if size > VIKINGDB_TEXT_FIELD_BYTE_LIMIT:
                raise ValueError(
                    f"SAG2 {field} exceeds {VIKINGDB_TEXT_FIELD_BYTE_LIMIT} UTF-8 bytes; "
                    "refusing to truncate indexed evidence"
                )
        return {key: self._json_value(value) for key, value in normalized.items()}

    @staticmethod
    def _fts_values(record: Dict[str, Any]) -> tuple[str, str, str]:
        title, heading = title_and_heading(record)
        level = 2 if record.get("level") is None else int(record["level"])
        body = record.get("content") if level == 2 else record.get("abstract")
        if not body and level == 2:
            body = record.get("abstract")
        try:
            body = body_for_preview(str(body or "")) if level in {0, 1} else str(body or "")
        except AbstractOverviewFormatError:
            body = ""
        return index_text(title), index_text(heading), index_text(body)

    def _bump_epochs(self, conn: sqlite3.Connection, account_ids: Iterable[str]) -> None:
        for account_id in sorted({str(value) for value in account_ids if value}):
            conn.execute(
                "INSERT INTO retrieval_epochs(account_id,epoch) VALUES(?,1) "
                "ON CONFLICT(account_id) DO UPDATE SET epoch=epoch+1",
                (account_id,),
            )

    def upsert(self, data: Dict[str, Any] | list[Dict[str, Any]]) -> list[str]:
        records = [data] if isinstance(data, dict) else data
        return self._store(records)

    def _store(
        self,
        records: list[Dict[str, Any]],
        *,
        merge_existing: bool = False,
        require_existing: bool = False,
    ) -> list[str]:
        self.get_collection()
        with self._lock:
            conn = self._connect()
            # The write lock also pins every strict-update read/merge in this transaction.
            conn.execute("BEGIN IMMEDIATE")
            ids: list[str] = []
            try:
                affected_accounts: list[str] = []
                for item in records:
                    record_id = str(item.get("id") or uuid.uuid4())
                    old = conn.execute(
                        "SELECT rowid,account_id,data FROM records WHERE id=?", (record_id,)
                    ).fetchone()
                    if require_existing and old is None:
                        raise CollectionNotFoundError(
                            f"SAG2 record {record_id} not found for update"
                        )
                    if merge_existing and old is not None:
                        item = {**self._decode_record(old["data"]), **item}
                    record = self._write_row({**item, "id": record_id})
                    ids.append(record_id)
                    if old:
                        affected_accounts.append(old["account_id"])
                        conn.execute(
                            "DELETE FROM record_fts WHERE rowid=(SELECT rowid FROM records WHERE id=?)",
                            (record_id,),
                        )
                        conn.execute("DELETE FROM records WHERE id=?", (record_id,))
                    columns = [
                        "id",
                        "account_id",
                        "uri",
                        "parent_uri",
                        "context_type",
                        "type",
                        "level",
                        "name",
                        "description",
                        "tags",
                        "search_tags",
                        "abstract",
                        "content",
                        "md5",
                        "owner_user_id",
                        "acl_mode",
                        "acl_direct_grants",
                        "acl_inherited_grants",
                        "active_count",
                        "created_at",
                        "updated_at",
                    ]
                    values = [record.get(column) for column in columns]
                    cursor = conn.execute(
                        f"INSERT INTO records({','.join(columns)},data) VALUES ({','.join('?' for _ in columns)},?)",
                        (*values, json.dumps(record, ensure_ascii=False)),
                    )
                    conn.execute(
                        "INSERT INTO record_fts(rowid,title,heading,body) VALUES(?,?,?,?)",
                        (cursor.lastrowid, *self._fts_values(record)),
                    )
                    affected_accounts.append(str(record.get("account_id") or ""))
                self._bump_epochs(conn, affected_accounts)
                conn.execute("COMMIT")
                return ids
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def update_data(self, data_list: list[Dict[str, Any]]) -> list[str]:
        return self._store(data_list, merge_existing=True, require_existing=True)

    def get(self, ids: list[str]) -> list[Dict[str, Any]]:
        if not ids:
            return []
        self.get_collection()
        with self._lock:
            placeholders = ",".join("?" for _ in ids)
            rows = (
                self._connect()
                .execute(f"SELECT data FROM records WHERE id IN ({placeholders})", list(ids))
                .fetchall()
            )
            by_id = {record["id"]: record for record in map(self._decode_record, rows)}
            return [by_id[record_id] for record_id in ids if record_id in by_id]

    @staticmethod
    def _decode_record(row: sqlite3.Row | str) -> Dict[str, Any]:
        raw = row["data"] if isinstance(row, sqlite3.Row) else row
        record = json.loads(raw)
        for key, value in list(record.items()):
            if key in _ARRAY_FIELDS and isinstance(value, str):
                try:
                    record[key] = json.loads(value)
                except json.JSONDecodeError:
                    record[key] = []
                value = record[key]
            if key in CollectionAdapter._URI_FIELD_NAMES and isinstance(value, str):
                record[key] = CollectionAdapter._decode_uri_field_value(value)
        return record

    @staticmethod
    def _field_sql(field: str) -> str:
        if not _IDENTIFIER.fullmatch(field):
            raise ValueError(f"Invalid SAG2 filter field: {field!r}")
        known = {
            "id",
            "account_id",
            "uri",
            "parent_uri",
            "context_type",
            "type",
            "level",
            "name",
            "description",
            "tags",
            "search_tags",
            "abstract",
            "content",
            "md5",
            "owner_user_id",
            "acl_mode",
            "acl_direct_grants",
            "acl_inherited_grants",
            "active_count",
            "created_at",
            "updated_at",
        }
        return f"r.{field}" if field in known else f"json_extract(r.data,'$.{field}')"

    def _path_clause(self, field: str, prefix: Any, depth: int) -> tuple[str, list[Any]]:
        column = self._field_sql(field)
        normalized = str(prefix).strip().rstrip("/") or "/"
        expression = (
            f"(CASE WHEN rtrim(trim({column}),'/')='' THEN '/' ELSE rtrim(trim({column}),'/') END)"
        )
        if depth == 0:
            return f"{expression}=?", [normalized]
        child = (
            "/%"
            if normalized == "/"
            else normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        )
        clause = f"({expression}=? OR {expression} LIKE ? ESCAPE '\\')"
        params: list[Any] = [normalized, child]
        if depth > 0:
            start = 2 if normalized == "/" else len(normalized) + 2
            suffix = f"substr({expression},{start})"
            relative = f"(length({suffix})-length(replace({suffix},'/',''))+1)"
            clause = f"({expression}=? OR ({expression} LIKE ? ESCAPE '\\' AND {relative}<=?))"
            params.append(depth)
        return clause, params

    def _where(self, filters: Optional[Dict[str, Any]]) -> tuple[str, list[Any]]:
        if not filters:
            return "", []
        op = filters.get("op", "")
        if op in {"and", "or"}:
            parts: list[str] = []
            params: list[Any] = []
            for condition in filters.get("conds", []):
                fragment, values = self._where(condition)
                if fragment:
                    parts.append(f"({fragment})")
                    params.extend(values)
            if not parts:
                return ("0", []) if op == "or" else ("", [])
            return f" {op.upper()} ".join(parts), params
        field = str(filters.get("field", ""))
        column = self._field_sql(field)
        conditions = filters.get("conds", [])
        match = (
            _PATH_DEPTH.fullmatch(filters.get("para", ""))
            if isinstance(filters.get("para"), str)
            else None
        )
        if op in {"must", "must_not"} and match and len(conditions) == 1:
            fragment, params = self._path_clause(field, conditions[0], int(match.group(1)))
            return (f"NOT ({fragment})" if op == "must_not" else fragment), params
        if op == "must":
            if not conditions:
                return "0", []
            if field in _ARRAY_FIELDS:
                parts = [
                    f"EXISTS (SELECT 1 FROM json_each({column}) WHERE json_each.value=?)"
                    for _ in conditions
                ]
                return "(" + " OR ".join(parts) + ")", list(conditions)
            return f"{column} IN ({','.join('?' for _ in conditions)})", list(conditions)
        if op == "must_not":
            if not conditions:
                return "", []
            if field in _ARRAY_FIELDS:
                parts = [
                    f"NOT EXISTS (SELECT 1 FROM json_each({column}) WHERE json_each.value=?)"
                    for _ in conditions
                ]
                return " AND ".join(parts), list(conditions)
            return (
                f"({column} NOT IN ({','.join('?' for _ in conditions)}) OR {column} IS NULL)",
                list(conditions),
            )
        if op == "prefix":
            prefix = (
                str(filters.get("prefix", ""))
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            return f"{column} LIKE ? ESCAPE '\\'", [prefix + "%"]
        if op == "contains":
            substring = (
                str(filters.get("substring") or "")
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            return f"{column} LIKE ? ESCAPE '\\'", [f"%{substring}%"]
        if op == "range":
            parts: list[str] = []
            params: list[Any] = []
            for key, operator in (("gte", ">="), ("gt", ">"), ("lte", "<="), ("lt", "<")):
                if key in filters:
                    parts.append(f"{column}{operator}?")
                    value = filters[key]
                    params.append(self._json_value(value))
            return " AND ".join(parts), params
        raise NotImplementedError(f"SAG2 does not support filter op={op!r}")

    def _scalar_query(
        self,
        *,
        filter: Optional[Dict[str, Any] | FilterExpr],
        limit: int,
        offset: int,
        output_fields: Optional[list[str]],
        order_by: Optional[str],
        order_desc: bool,
    ) -> list[Dict[str, Any]]:
        self.get_collection()
        where, params = self._where(self._compile_filter(filter))
        order = "r.rowid"
        if order_by:
            order = self._field_sql(order_by) + (" DESC" if order_desc else " ASC")
        order += ",r.id"
        sql = "SELECT r.data FROM records r"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._connect().execute(sql, (*params, limit, offset)).fetchall()
        records = [self._decode_record(row) for row in rows]
        return [self._project(record, output_fields) for record in records]

    @staticmethod
    def _project(record: Dict[str, Any], output_fields: Optional[list[str]]) -> Dict[str, Any]:
        if output_fields is None:
            return record
        projected = {key: record[key] for key in output_fields if key in record}
        projected["id"] = record["id"]
        return projected

    def query(self, **kwargs: Any) -> list[Dict[str, Any]]:
        if kwargs.get("query_vector") or kwargs.get("sparse_query_vector"):
            raise ValueError("SAG2 does not accept dense or sparse vector queries")
        if kwargs.get("advance"):
            raise NotImplementedError("SAG2 does not support vector time decay")
        return self._scalar_query(
            filter=kwargs.get("filter"),
            limit=kwargs.get("limit", 10),
            offset=kwargs.get("offset", 0),
            output_fields=kwargs.get("output_fields"),
            order_by=kwargs.get("order_by"),
            order_desc=kwargs.get("order_desc", False),
        )

    def search_by_random(self, **kwargs: Any) -> list[Dict[str, Any]]:
        return self.query(**kwargs)

    def delete(
        self,
        *,
        ids: Optional[list[str]] = None,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
    ) -> int:
        self.get_collection()
        with self._lock:
            conn = self._connect()
            params: list[Any] = []
            where = ""
            if ids is not None:
                if not ids:
                    return 0
                where = f"id IN ({','.join('?' for _ in ids)})"
                params = list(ids)
            elif filter is not None:
                where, params = self._where(self._compile_filter(filter))
            else:
                return 0
            if not where:
                return 0
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    f"SELECT r.rowid,r.account_id FROM records r WHERE {where}", params
                ).fetchall()
                if rows:
                    conn.executemany(
                        "DELETE FROM record_fts WHERE rowid=?",
                        [(row["rowid"],) for row in rows],
                    )
                    conn.executemany(
                        "DELETE FROM records WHERE rowid=?",
                        [(row["rowid"],) for row in rows],
                    )
                self._bump_epochs(conn, [row["account_id"] for row in rows])
                conn.execute("COMMIT")
                return len(rows)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def count(self, filter: Optional[Dict[str, Any] | FilterExpr] = None) -> int:
        self.get_collection()
        where, params = self._where(self._compile_filter(filter))
        sql = "SELECT count(*) FROM records r" + (f" WHERE {where}" if where else "")
        with self._lock:
            return int(self._connect().execute(sql, params).fetchone()[0])

    strict_count = count

    def clear(self) -> bool:
        self.get_collection()
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                accounts = [
                    row[0] for row in conn.execute("SELECT DISTINCT account_id FROM records")
                ]
                conn.execute("DELETE FROM record_fts")
                conn.execute("DELETE FROM records")
                self._bump_epochs(conn, accounts)
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def current_epoch(self, account_id: str) -> int:
        self.get_collection()
        with self._lock:
            row = (
                self._connect()
                .execute("SELECT epoch FROM retrieval_epochs WHERE account_id=?", (account_id,))
                .fetchone()
            )
            return int(row[0]) if row else 0

    def check_epoch(self, account_id: str, expected_epoch: int) -> None:
        if self.current_epoch(account_id) != expected_epoch:
            raise IndexChanged("SAG2 index changed during retrieval; retry the query")

    def lexical_query(
        self,
        *,
        expression: str,
        filter: Optional[Dict[str, Any] | FilterExpr],
        limit: int,
        output_fields: Optional[list[str]] = None,
        offset: int = 0,
        required_term: Optional[str] = None,
        excluded_ids: Optional[list[str]] = None,
        expected_epoch: Optional[int] = None,
        account_id: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        self.get_collection()
        if not expression:
            return []
        if expected_epoch is not None and account_id:
            self.check_epoch(account_id, expected_epoch)
        where, params = self._where(self._compile_filter(filter))
        exclusions = ""
        exclusion_params: list[Any] = []
        if excluded_ids:
            exclusions = f" AND r.id NOT IN ({','.join('?' for _ in excluded_ids)})"
            exclusion_params = list(excluded_ids)
        required_join = ""
        required_where = ""
        query_params: list[Any] = [expression]
        if required_term:
            required_where = (
                " AND r.rowid IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)"
            )
            query_params.append(quote(required_term))
        condition = f"({where}) AND " if where else ""
        sql = (
            "SELECT r.data,bm25(record_fts,2.0,2.0,1.0) AS rank FROM record_fts "
            "JOIN records r ON r.rowid=record_fts.rowid"
            + required_join
            + f" WHERE {condition}record_fts MATCH ?{required_where}{exclusions} "
            "ORDER BY rank ASC,r.id ASC LIMIT ? OFFSET ?"
        )
        # MATCH parameters occur after the scope parameters in the SQL text.
        with self._lock:
            rows = (
                self._connect()
                .execute(sql, (*params, *query_params, *exclusion_params, limit, offset))
                .fetchall()
            )
            results = []
            for row in rows:
                rank = float(row["rank"])
                score = -rank if math.isfinite(rank) else 0.0
                record = self._project(self._decode_record(row), output_fields)
                record["_score"] = score
                results.append(record)
        if expected_epoch is not None and account_id:
            self.check_epoch(account_id, expected_epoch)
        return results

    def search_by_keywords(
        self,
        keywords: Optional[list[str]] = None,
        query: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        filter: Optional[Dict[str, Any] | FilterExpr] = None,
        output_fields: Optional[list[str]] = None,
        mode: Optional[str] = None,
        fields: Optional[list[str]] = None,
    ) -> list[Dict[str, Any]]:
        del mode, fields
        expression = match_query(
            " ".join(keywords or []) if keywords else query or "", self._sag2_config.max_query_terms
        )
        return self.lexical_query(
            expression=expression,
            filter=filter,
            limit=limit,
            offset=offset,
            output_fields=output_fields,
        )

    def scope_titles(
        self,
        *,
        filter: Optional[Dict[str, Any] | FilterExpr],
        expected_epoch: Optional[int] = None,
        account_id: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        if expected_epoch is not None and account_id:
            self.check_epoch(account_id, expected_epoch)
        where, params = self._where(self._compile_filter(filter))
        titles: list[Dict[str, Any]] = []
        with self._lock:
            cursor = self._connect().execute(
                "SELECT DISTINCT r.name,r.uri FROM records r"
                + (f" WHERE {where}" if where else ""),
                params,
            )
            while True:
                rows = cursor.fetchmany(256)
                if not rows:
                    break
                titles.extend(dict(row) for row in rows)
        return titles

    def term_frequencies(
        self,
        *,
        account_id: str,
        terms: list[str],
        expected_epoch: Optional[int] = None,
    ) -> tuple[int, dict[str, int]]:
        if expected_epoch is not None:
            self.check_epoch(account_id, expected_epoch)
        with self._lock:
            conn = self._connect()
            total = int(
                conn.execute(
                    "SELECT count(*) FROM records WHERE account_id=?", (account_id,)
                ).fetchone()[0]
            )
            frequencies: dict[str, int] = {}
            for term in dict.fromkeys(terms):
                row = conn.execute(
                    "SELECT count(*) FROM record_fts JOIN records r ON r.rowid=record_fts.rowid "
                    "WHERE r.account_id=? AND record_fts MATCH ?",
                    (account_id, quote(term)),
                ).fetchone()
                frequencies[term] = int(row[0])
        if expected_epoch is not None:
            self.check_epoch(account_id, expected_epoch)
        return total, frequencies
