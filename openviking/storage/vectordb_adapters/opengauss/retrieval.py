# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Explicit sparse posting lifecycle and bounded, snapshot-consistent ranking.

The collection owns transactions. Mutation hooks accept its active cursor and
never commit; call them after the base-row mutation, before its commit. All
writers (including adapter-level filtered deletes) must participate in these
hooks. Direct SQL writes are not maintained by database triggers.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking_cli.exceptions import (
    FailedPreconditionError,
    ResourceExhaustedError,
    UnimplementedError,
)

from .sql import (
    _DISTANCE_OP,
    _META_TABLE,
    _bounded_identifier,
    _build_where_clause,
    _coerce_sparse_vector,
    _distance_to_similarity,
    _quote_identifier,
    _validate_vector,
)

MAX_SPARSE_NONZERO = 4096
MAX_SPARSE_TERM_BYTES = 256
MAX_CANDIDATE_LIMIT = 10000
DEFAULT_CANDIDATE_LIMIT = 1000


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _validate_sparse_vector(value: Any) -> dict[str, float]:
    """Validate the JSONB contract without silently coercing terms or weights."""
    value = _coerce_sparse_vector(value)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("openGauss sparse_vector must be a JSON object")
    result = {}
    for term, weight in value.items():
        if not isinstance(term, str) or not term or "\x00" in term:
            raise ValueError("openGauss sparse terms must be non-empty strings without NUL")
        try:
            term_bytes = len(term.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError("openGauss sparse terms must be valid UTF-8") from error
        if term_bytes > MAX_SPARSE_TERM_BYTES:
            raise ValueError(f"openGauss sparse terms exceed {MAX_SPARSE_TERM_BYTES} UTF-8 bytes")
        number = _finite_number(weight, "openGauss sparse weight")
        if number < 0:
            raise ValueError("openGauss negative sparse weights are not supported")
        if number:
            result[term] = number
            if len(result) > MAX_SPARSE_NONZERO:
                raise ValueError(f"openGauss sparse_vector exceeds {MAX_SPARSE_NONZERO} nonzeros")
    return result


def _validate_search_parameters(limit, offset, output_fields) -> None:
    if type(limit) is not int or type(offset) is not int or limit < 0 or offset < 0:
        raise ValueError("openGauss limit and offset must be non-negative integers")
    _validate_output_fields(output_fields)


def _validate_output_fields(output_fields) -> None:
    if output_fields is not None and (
        not isinstance(output_fields, list)
        or any(not isinstance(field, str) for field in output_fields)
    ):
        raise ValueError("openGauss output_fields must be a list of strings or null")


def _validate_filters(filters, field_names) -> None:
    if filters is None:
        return
    if not isinstance(filters, dict):
        raise ValueError("openGauss filters must be an object or null")
    if not filters:
        return
    if filters.get("op") in {"and", "or"}:
        conditions = filters.get("conds", [])
        if not isinstance(conditions, list):
            raise ValueError("openGauss logical filter conds must be a list")
        for condition in conditions:
            if not isinstance(condition, dict) or not condition:
                raise ValueError("openGauss logical filter branches must be non-empty objects")
            _validate_filters(condition, field_names)
    elif not isinstance(filters.get("field"), str) or filters["field"] not in field_names:
        raise ValueError(f"Unknown openGauss filter field: {filters.get('field')!r}")
    elif filters.get("op") in {"must", "must_not"} and not isinstance(
        filters.get("conds", []), list
    ):
        raise ValueError("openGauss filter conds must be a list")


def _candidate_limit(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"candidate_limit must be an integer in [1, {MAX_CANDIDATE_LIMIT}]")
    if value > MAX_CANDIDATE_LIMIT:
        raise ResourceExhaustedError(
            f"openGauss candidate_limit requested={value} exceeds max={MAX_CANDIDATE_LIMIT}",
            details={"requested": value, "max": MAX_CANDIDATE_LIMIT},
        )
    return value


def _sparse_alpha(value: Any) -> float:
    alpha = _finite_number(value, "sparse_weight")
    if not 0 <= alpha <= 1:
        raise ValueError("sparse_weight must be in [0, 1]")
    return alpha


def _parse_time_decay(advance: Any):
    if advance is None:
        return None
    if not isinstance(advance, dict) or set(advance) != {"time_decay"}:
        raise UnimplementedError("openGauss advanced ranking supports time_decay only")
    request = advance["time_decay"]
    if not isinstance(request, dict):
        raise ValueError("time_decay must be an object")
    compiled_keys = {"field", "origin_ms", "offset_ms", "scale_ms", "decay"}
    if set(request) == compiled_keys:
        spec = SimpleNamespace(field=request["field"])
        for key in compiled_keys - {"field"}:
            setattr(spec, key, _finite_number(request[key], f"time_decay.{key}"))
        from openviking.utils.time_decay import MAX_DURATION_MS

        # Compiled payloads also cross the public advance boundary; they may
        # encode the fixed curve, but cannot introduce a caller-defined one.
        if spec.scale_ms != 7 * 86400000 or spec.decay != 0.5:
            raise ValueError("time_decay requires the fixed scale=7d and decay=0.5 curve")
        if not 0 <= spec.offset_ms <= MAX_DURATION_MS or spec.offset_ms % 60000:
            raise ValueError("time_decay offset must be whole minutes within the duration limit")
    else:
        if "protection" not in request or set(request) - {"protection", "origin", "field"}:
            raise ValueError("time_decay requires protection and optional origin/field only")
        # Imported lazily: the parent integration supplies the shared helper.
        from openviking.utils.time_decay import build_time_decay_fusion_spec

        origin = request.get("origin")
        if isinstance(origin, str):
            from openviking.utils.time_utils import parse_iso_datetime

            origin = parse_iso_datetime(origin)
        if origin is not None and not isinstance(origin, datetime):
            raise ValueError("time_decay.origin must be an ISO 8601 string or datetime")
        if origin is not None and origin.tzinfo is None:
            origin = origin.replace(tzinfo=timezone.utc)
        spec = build_time_decay_fusion_spec(
            protection=request["protection"],
            origin=origin,
            field=request.get("field", "updated_at"),
        )
    _quote_identifier(spec.field, kind="openGauss time-decay field")
    return spec


def _time_decay_factor(value: Any, spec) -> float | None:
    """Match the native contract: missing/invalid times leave the score unchanged."""
    if value is None or isinstance(value, bool):
        return None
    from openviking.utils.time_decay import _datetime_to_epoch_ms

    try:
        timestamp = _datetime_to_epoch_ms(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp):
        return None
    return time_decay_factor(timestamp, spec)


def time_decay_factor(value: Any, spec) -> float:
    """Evaluate the fixed curve using the shared time parser, without a shared evaluator."""
    from openviking.utils.time_decay import _datetime_to_epoch_ms

    try:
        timestamp = _datetime_to_epoch_ms(value)
    except (ValueError, TypeError, OverflowError):
        return 1.0
    elapsed = max(0.0, abs(spec.origin_ms - timestamp) - spec.offset_ms)
    return math.exp(math.log(spec.decay) * elapsed / spec.scale_ms)


class OpenGaussRetrievalMixin:
    """Mixin for OpenGaussCollection; no constructor or implicit query-time DDL."""

    def _sparse_posting_table_name(self) -> str:
        return _bounded_identifier(f"_ov_sparse_{self._name}")

    def _retrieval_options(self) -> dict:
        options = self._meta.get("_sparse_index", {})
        if not isinstance(options, dict):
            raise ValueError("openGauss _sparse_index metadata must be an object")
        return options

    def _require_sparse_ready(self) -> None:
        options = self._retrieval_options()
        if options.get("sparse_enabled") is not True:
            raise FailedPreconditionError("openGauss sparse retrieval is disabled")
        if options.get("sparse_ready") is not True or not self.validate_sparse_index():
            raise FailedPreconditionError("openGauss sparse postings are not physically ready")

    def _sparse_enabled_on_cursor(self, cursor) -> bool:
        # A handle opened before backfill must not keep skipping maintenance.
        cursor.execute(
            f'SELECT meta_json FROM "{_META_TABLE}" WHERE table_name = %s', (self._name,)
        )
        row = cursor.fetchone()
        if not row:
            raise RuntimeError("openGauss collection metadata is missing")
        meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        options = meta.get("_sparse_index", {})
        if not options.get("sparse_enabled", False):
            return False
        if options.get("sparse_ready") is not True:
            raise RuntimeError("openGauss enabled sparse postings are not ready")
        return True

    def _persist_sparse_state_on_cursor(self, cursor, updates: dict) -> dict:
        cursor.execute(
            f'SELECT meta_json FROM "{_META_TABLE}" WHERE table_name = %s FOR UPDATE',
            (self._name,),
        )
        row = cursor.fetchone()
        if not row:
            raise RuntimeError("openGauss collection metadata is missing")
        meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        retrieval = dict(meta.get("_sparse_index", {}))
        retrieval.update(updates)
        meta = {**meta, "_sparse_index": retrieval}
        cursor.execute(
            f'UPDATE "{_META_TABLE}" SET meta_json = %s WHERE table_name = %s',
            (json.dumps(meta, ensure_ascii=False), self._name),
        )
        return meta

    def _validate_sparse_storage_on_cursor(self, cursor, ids: list[str] | None = None) -> None:
        table = _quote_identifier(self._name)
        restriction = ""
        params: list[Any] = []
        if ids is not None:
            restriction = f' AND c."id" IN ({", ".join(["%s"] * len(ids))})'
            params = list(ids)
        cursor.execute(
            f"SELECT 1 FROM {table} c WHERE c.sparse_vector IS NOT NULL "
            f"AND jsonb_typeof(c.sparse_vector) <> 'object'{restriction} LIMIT 1",
            params,
        )
        if cursor.fetchone():
            raise ValueError("openGauss stored sparse_vector must be a JSON object or SQL NULL")
        cursor.execute(
            f"SELECT 1 FROM {table} c, jsonb_each(c.sparse_vector) e "
            "WHERE (octet_length(e.key) = 0 OR octet_length(e.key) > %s "
            "OR CASE WHEN jsonb_typeof(e.value) = 'number' THEN "
            "NOT ((e.value::text)::double precision >= 0 "
            "AND (e.value::text)::double precision < 'Infinity'::double precision) "
            f"ELSE TRUE END){restriction} LIMIT 1",
            [MAX_SPARSE_TERM_BYTES, *params],
        )
        if cursor.fetchone():
            raise ValueError(
                "openGauss sparse terms/weights are invalid; negative weights unsupported"
            )
        cursor.execute(
            f"SELECT 1 FROM {table} c, jsonb_each(c.sparse_vector) e "
            f"WHERE (e.value::text)::double precision <> 0{restriction} "
            'GROUP BY c."id" HAVING COUNT(*) > %s LIMIT 1',
            [*params, MAX_SPARSE_NONZERO],
        )
        if cursor.fetchone():
            raise ValueError(
                f"openGauss stored sparse_vector exceeds {MAX_SPARSE_NONZERO} nonzeros"
            )

    def _insert_sparse_postings_on_cursor(self, cursor, ids: list[str] | None = None) -> None:
        table = _quote_identifier(self._name)
        postings = _quote_identifier(self._sparse_posting_table_name())
        restriction = ""
        params = []
        if ids is not None:
            restriction = f' AND c."id" IN ({", ".join(["%s"] * len(ids))})'
            params = list(ids)
        cursor.execute(
            f"INSERT INTO {postings} (id, term, weight) "
            f'SELECT c."id", e.key, (e.value::text)::double precision FROM {table} c, '
            "jsonb_each(c.sparse_vector) e "
            f"WHERE (e.value::text)::double precision > 0{restriction}",
            params,
        )

    def create_sparse_index(
        self, *, candidate_limit: int | None = None, sparse_weight: float | None = None
    ) -> None:
        """Explicitly create/rebuild postings, validate JSONB and atomically mark ready.

        The base-table write lock prevents a backfill/mutation lost update. The
        posting primary key covers id-based maintenance; the secondary B-tree
        starts with term for query-term posting access. SPQ is not certified.
        """
        if getattr(self, "_distributed", False):
            raise NotImplementedError("SPQ sparse indexes have not been certified")
        options = self._retrieval_options()
        limit = _candidate_limit(
            options.get("candidate_limit", DEFAULT_CANDIDATE_LIMIT)
            if candidate_limit is None
            else candidate_limit
        )
        alpha = _sparse_alpha(
            options.get("sparse_weight", 0.5) if sparse_weight is None else sparse_weight
        )
        if "sparse_vector" not in self._field_names:
            raise ValueError("openGauss collection must define a JSONB sparse_vector field")
        table = _quote_identifier(self._name)
        posting_name = self._sparse_posting_table_name()
        postings = _quote_identifier(posting_name)
        term_index = _quote_identifier(_bounded_identifier(f"{posting_name}_term_idx"))
        with self._lock:
            cursor = self._cursor()
            try:
                cursor.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
                meta = self._load_metadata_on_cursor(cursor)
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (posting_name,),
                )
                if cursor.fetchone() is not None and not meta.get("_sparse_index"):
                    raise RuntimeError("Refusing to adopt an unowned sparse posting table")
                cursor.execute(
                    f"CREATE TABLE IF NOT EXISTS {postings} ("
                    "id VARCHAR(256) NOT NULL, term VARCHAR(256) NOT NULL, "
                    "weight DOUBLE PRECISION NOT NULL, PRIMARY KEY (id, term), "
                    f"CHECK (octet_length(term) BETWEEN 1 AND {MAX_SPARSE_TERM_BYTES}), "
                    "CHECK (weight > 0 AND weight < 'Infinity'::double precision))"
                )
                cursor.execute(f"CREATE INDEX IF NOT EXISTS {term_index} ON {postings} (term, id)")
                self._validate_sparse_storage_on_cursor(cursor)
                cursor.execute(f"DELETE FROM {postings}")
                self._insert_sparse_postings_on_cursor(cursor)
                new_meta = self._persist_sparse_state_on_cursor(
                    cursor,
                    {
                        "sparse_enabled": True,
                        "sparse_ready": True,
                        "candidate_limit": limit,
                        "sparse_weight": alpha,
                    },
                )
                self._conn.commit()
                self._meta = new_meta
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def _sync_sparse_postings_on_cursor(self, cursor, primary_keys) -> None:
        """After upsert/update, sync actual JSONB rows (partial updates stay intact)."""
        ids = list(dict.fromkeys(str(key) for key in primary_keys))
        if not ids:
            return
        placeholders = ", ".join(["%s"] * len(ids))
        # The id-only upsert conflict path may not have updated/locked a row.
        # Serialize its derived writes with concurrent base-row updates too.
        cursor.execute(
            f'SELECT "id" FROM {_quote_identifier(self._name)} '
            f'WHERE "id" IN ({placeholders}) FOR UPDATE',
            ids,
        )
        if not self._sparse_enabled_on_cursor(cursor):
            return
        self._validate_sparse_storage_on_cursor(cursor, ids)
        postings = _quote_identifier(self._sparse_posting_table_name())
        cursor.execute(f"DELETE FROM {postings} WHERE id IN ({placeholders})", ids)
        self._insert_sparse_postings_on_cursor(cursor, ids)

    def _delete_sparse_postings_on_cursor(self, cursor, primary_keys=None) -> None:
        """After delete, clean ids; None means all rows, not an empty id batch."""
        ids = None if primary_keys is None else list(dict.fromkeys(str(k) for k in primary_keys))
        if ids == [] or not self._sparse_enabled_on_cursor(cursor):
            return
        postings = _quote_identifier(self._sparse_posting_table_name())
        where = ""
        if ids is not None:
            # A delete of a previously absent id must not erase a concurrent
            # insert that became visible after the base DELETE statement.
            where = (
                f" WHERE id IN ({', '.join(['%s'] * len(ids))})"
                f" AND NOT EXISTS (SELECT 1 FROM {_quote_identifier(self._name)} c "
                f'WHERE c."id" = {postings}.id)'
            )
        cursor.execute(f"DELETE FROM {postings}{where}", ids or [])

    def _drop_sparse_postings_on_cursor(self, cursor) -> None:
        """Explicit collection-drop hook, in the collection's drop transaction."""
        cursor.execute(
            f'SELECT meta_json FROM "{_META_TABLE}" WHERE table_name = %s',
            (self._name,),
        )
        row = cursor.fetchone()
        meta = json.loads(row[0]) if row and isinstance(row[0], str) else (row[0] if row else {})
        if meta.get("_sparse_index"):
            cursor.execute(
                f"DROP TABLE IF EXISTS {_quote_identifier(self._sparse_posting_table_name())}"
            )

    def _validate_ranked_filters(self, filters) -> None:
        _validate_filters(filters, self._field_names)

    def search_ranked(
        self,
        index_name: str,
        dense_vector=None,
        limit: int = 10,
        offset: int = 0,
        filters=None,
        sparse_vector=None,
        output_fields=None,
        advance=None,
        return_detail_info: bool = False,
    ) -> SearchResult:
        """Recall <= effective budget per modality, complete scores, rerank, then page.

        One SQL statement provides one MVCC snapshot even at READ COMMITTED.
        This is bounded approximate fusion, not a guarantee of global fused top-k.
        Sparse recall includes positive-overlap documents only; it never pads
        with non-overlap zero scores. Hybrid adds independent dense candidates,
        whose exact sparse score can be zero (even for negative dense scores).
        Missing dense vectors are excluded from hybrid candidates, rather than
        assigning them an invented dense score. The union has <= 2 * budget ids.
        Concurrent calls require the collection's thread-local pooled connection,
        not a shared raw driver connection; DDL still takes the collection lock.
        """
        _validate_search_parameters(limit, offset, output_fields)
        if getattr(self, "_distributed", False):
            raise UnimplementedError("SPQ sparse/advanced ranking has not been certified")
        spec = _parse_time_decay(advance)
        options = self._retrieval_options()
        configured_budget = getattr(self, "_retrieval_config", {}).get(
            "candidate_limit", DEFAULT_CANDIDATE_LIMIT
        )
        budget = _candidate_limit(options.get("candidate_limit", configured_budget))
        alpha = _sparse_alpha(options.get("sparse_weight", 0.5))
        sparse = _validate_sparse_vector(sparse_vector)
        dense = None
        if dense_vector is not None and dense_vector != []:
            dense = _validate_vector(dense_vector, self._dim, field_name="query vector")
        has_dense = bool(dense)
        has_sparse = bool(sparse)
        if spec is not None and spec.field not in self._date_time_fields:
            raise ValueError("openGauss time_decay field must be a declared date_time field")
        output_sql = self._select_output_columns(output_fields)
        self._validate_ranked_filters(filters)
        where, filter_params = _build_where_clause(
            self._normalize_filter_date_times(filters), self._array_fields
        )
        if not limit or not (has_dense or has_sparse):
            return SearchResult(data=[])
        budget = _candidate_limit(max(budget, limit + offset))
        if has_dense and index_name in self._pending_indexes and not self._table_has_rows():
            return SearchResult(data=[])
        if has_sparse:
            self._require_sparse_ready()
        metric, _ = self._resolve_distance_and_op(index_name)
        if metric not in _DISTANCE_OP:
            raise ValueError(f"Unsupported openGauss distance metric: {metric!r}")
        op = _DISTANCE_OP[metric]
        if has_dense and index_name not in self._indexes:
            raise FailedPreconditionError(
                "openGauss dense ANN index is not ready; build it explicitly first"
            )
        table = _quote_identifier(self._name)
        postings = _quote_identifier(self._sparse_posting_table_name())
        condition = f"({where})" if where else "TRUE"
        vector_text = "[" + ",".join(str(v) for v in dense) + "]" if has_dense else None
        ctes, candidate_selects, params = [], [], []
        if has_sparse:
            values_sql = ", ".join(["(%s::text, %s::double precision)"] * len(sparse))
            ctes.append(f"query_sparse(term, weight) AS (VALUES {values_sql})")
            for term, weight in sparse.items():
                params.extend([term, weight])
        if has_dense:
            ctes.append(
                f'dense_candidates AS (SELECT "id" AS _ov_candidate_id FROM {table} '
                f'WHERE {condition} AND "vector" IS NOT NULL '
                f'ORDER BY "vector" {op} %s::vector LIMIT %s)'
            )
            params.extend([*filter_params, vector_text, budget])
            candidate_selects.append("SELECT _ov_candidate_id FROM dense_candidates")
        if has_sparse:
            vector_guard = ' AND "vector" IS NOT NULL' if has_dense else ""
            ctes.append(
                f"sparse_candidates AS (SELECT p.id AS _ov_candidate_id FROM {postings} p "
                "JOIN query_sparse q ON p.term = q.term "
                f'WHERE EXISTS (SELECT 1 FROM {table} WHERE "id" = p.id '
                f"AND {condition}{vector_guard}) GROUP BY p.id "
                "HAVING SUM(p.weight * q.weight) > 0 "
                "ORDER BY SUM(p.weight * q.weight) DESC, p.id LIMIT %s)"
            )
            params.extend([*filter_params, budget])
            candidate_selects.append("SELECT _ov_candidate_id FROM sparse_candidates")
        ctes.append("candidates AS (" + " UNION ".join(candidate_selects) + ")")
        distance_sql = (
            f'{table}."vector" {op} %s::vector' if has_dense else "NULL::double precision"
        )
        if has_dense:
            params.append(vector_text)
        sparse_sql = (
            f"COALESCE((SELECT SUM(p.weight * q.weight) FROM {postings} p "
            f'JOIN query_sparse q ON p.term = q.term WHERE p.id = {table}."id"), 0)'
            if has_sparse
            else "0::double precision"
        )
        time_sql = f"{table}.{_quote_identifier(spec.field)}" if spec is not None else "NULL"
        projection = (
            f"{table}.*"
            if output_sql == "*"
            else ", ".join(
                column if column.startswith("NULL AS ") else f"{table}.{column}"
                for column in output_sql.split(", ")
            )
        )
        # A semi-join keeps the outer projection unambiguous even if a user
        # column is named _ov_candidate_id; candidates are never fetched first.
        sql = (
            "WITH " + ", ".join(ctes) + f" SELECT {projection}, "
            f"{distance_sql} AS _ov_dense_distance, {sparse_sql} AS _ov_sparse_score, "
            f"{time_sql} AS _ov_decay_time FROM {table} "
            f'WHERE {table}."id" IN (SELECT candidates._ov_candidate_id FROM candidates)'
        )
        cursor = self._cursor()
        try:
            if has_dense:
                self._apply_search_params_on_cursor(cursor, index_name)
            cursor.execute(sql, params)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cursor.close()
        items = []
        for row in rows:
            # Ranking columns are positional so same-named schema fields survive.
            record = self._row_to_dict(row[:-3], columns[:-3])
            dense_distance, sparse_score, timestamp = row[-3:]
            sparse_score = float(sparse_score)
            if not math.isfinite(sparse_score):
                raise ValueError("openGauss sparse dot product must be finite")
            dense_score = _distance_to_similarity(metric, dense_distance) if has_dense else 0.0
            if has_dense and (dense_distance is None or not math.isfinite(float(dense_distance))):
                raise ValueError("openGauss dense candidate distance must be finite")
            origin_score = (
                (1 - alpha) * dense_score + alpha * sparse_score
                if has_dense and has_sparse
                else dense_score
                if has_dense
                else sparse_score
            )
            factor = _time_decay_factor(timestamp, spec) if spec is not None else None
            score = origin_score * factor if factor is not None else origin_score
            item = SearchItemResult(id=record.pop("id"), fields=record, score=score)
            if return_detail_info and spec is not None:
                item.origin_score = origin_score
                item.addition_score = factor
            items.append(item)
        items.sort(key=lambda item: (-item.score, str(item.id)))
        return SearchResult(data=items[offset : offset + limit])
