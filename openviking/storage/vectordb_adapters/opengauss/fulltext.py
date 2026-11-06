# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Schema-declared native BM25, with read-only physical readiness checks."""

from __future__ import annotations

import math
from typing import Any

from openviking.storage.vectordb.collection.result import SearchItemResult, SearchResult
from openviking_cli.exceptions import (
    FailedPreconditionError,
    ResourceExhaustedError,
    UnimplementedError,
)

from .capabilities import probe_fulltext_capabilities
from .retrieval import _validate_filters, _validate_output_fields, _validate_search_parameters
from .sql import _bounded_identifier, _build_where_clause, _quote_identifier, _validate_identifier

# Bounds candidate materialization, not the tie group: an unresolved boundary
# at this limit fails closed rather than returning an incomplete, unstable page.
_BM25_MAX_CANDIDATES = 100_000

_BM25_INDEX_CATALOG_SQL = """SELECT i.indisvalid, i.indisready, a.amname,
                        pg_catalog.pg_get_indexdef(i.indexrelid, 1, true),
                        i.indpred IS NULL, i.indnatts
                 FROM pg_catalog.pg_index i
                 JOIN pg_catalog.pg_class t ON t.oid = i.indrelid
                 JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
                 JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid
                 JOIN pg_catalog.pg_am a ON a.oid = x.relam
                 WHERE n.nspname = current_schema() AND t.relname = %s AND x.relname = %s"""


def _fulltext_index_spec(name: str) -> dict[str, str]:
    return {"name": _bounded_identifier(f"{name}_ov_bm25"), "engine": "bm25"}


def _declares_fulltext(meta: dict[str, Any]) -> bool:
    definitions = meta.get("FullText") or []
    if not isinstance(definitions, list) or any(
        not isinstance(item, dict) or item.get("Field") != "content" for item in definitions
    ):
        raise ValueError("openGauss FullText must declare the content field only")
    return bool(definitions)


def _matches_bm25_index(rows, spec: dict[str, Any], *, require_valid: bool = True) -> bool:
    if spec.get("engine") != "bm25" or len(rows) != 1:
        return False
    valid, ready, method, expression, unconditional, count = rows[0]
    return bool(
        (not require_valid or (valid and ready))
        and unconditional
        and count == 1
        and method == "bm25"
        and expression.strip() in {"content", '"content"'}
    )


class OpenGaussFulltextMixin:
    """BM25 is built on an empty table at creation, never repaired by a query."""

    def probe_fulltext_capabilities(self):
        with self._lock:
            return probe_fulltext_capabilities(self._conn)

    def _fulltext_spec(self) -> dict[str, Any]:
        return dict(self._meta.get("_fulltext_index") or _fulltext_index_spec(self._name))

    def _fulltext_index_rows(self, spec: dict[str, Any], cursor=None):
        params = (self._name, spec["name"])
        if cursor is None:
            return self._execute(_BM25_INDEX_CATALOG_SQL, params, fetch=True)
        cursor.execute(_BM25_INDEX_CATALOG_SQL, params)
        return cursor.fetchall()

    def _fulltext_index_matches(
        self, spec: dict[str, Any], cursor=None, *, require_valid: bool = True
    ) -> bool:
        return _matches_bm25_index(
            self._fulltext_index_rows(spec, cursor), spec, require_valid=require_valid
        )

    def validate_fulltext_index(self, engine: str | None = None) -> bool:
        if engine is not None and engine != "bm25":
            raise ValueError("engine must be bm25")
        with self._lock:
            if getattr(self, "_distributed", False) or not _declares_fulltext(self._meta):
                return False
            if self._get_column_types().get("content") != "text":
                return False
            return self._fulltext_index_matches(self._fulltext_spec())

    def _fulltext_output_columns(self, output_fields):
        _validate_output_fields(output_fields)
        actual = set(self._get_all_columns())
        public = actual - {"_ov_content_empty"}
        if output_fields:
            unknown = set(output_fields) - public
            if unknown:
                raise ValueError(f"Unknown openGauss output fields: {sorted(unknown)}")
            fields = list(dict.fromkeys(["id", *output_fields]))
        else:
            fields = list(dict.fromkeys(["id", *sorted(public)]))
        if "content" in fields and "_ov_content_empty" in actual:
            fields.append("_ov_content_empty")
        return ", ".join(_quote_identifier(field) for field in fields)

    def _bm25_page(self, cursor, sql, params, *, limit: int, offset: int):
        if limit == 0:
            return []
        end = offset + limit
        if end > _BM25_MAX_CANDIDATES:
            raise ResourceExhaustedError(
                f"BM25 pagination requested={end} exceeds the "
                f"{_BM25_MAX_CANDIDATES} candidate safety limit (max={_BM25_MAX_CANDIDATES})",
                details={"requested": end, "max": _BM25_MAX_CANDIDATES},
            )
        # Native <&> cannot be evaluated by a regular Sort/Seq Scan. Keep its
        # single ordering key inside LIMIT, and sort the complete boundary tie
        # group outside. All growing prefixes must observe the same snapshot.
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        prefix = min(end + 1, _BM25_MAX_CANDIDATES)
        while True:
            # Native corpus statistics need not be MVCC-stable. Re-score the
            # whole prefix each round; never merge scores from earlier rounds.
            cursor.execute(sql, [*params, prefix])
            rows = cursor.fetchall()
            scores = [float(row[-1]) for row in rows]
            if any(not math.isfinite(score) for score in scores):
                raise ValueError("Database returned a non-finite full-text score")
            exhausted = len(rows) < prefix
            boundary_complete = len(rows) >= end and scores[-1] < scores[end - 1]
            if exhausted or boundary_complete:
                return rows[offset:end]
            if prefix == _BM25_MAX_CANDIDATES:
                raise ResourceExhaustedError(
                    "BM25 boundary tie group is not complete within the "
                    f"{_BM25_MAX_CANDIDATES} candidate safety limit "
                    f"(requested>={prefix + 1}, max={_BM25_MAX_CANDIDATES})",
                    details={"requested": prefix + 1, "max": _BM25_MAX_CANDIDATES},
                )
            prefix = min(prefix * 2, _BM25_MAX_CANDIDATES)

    def search_by_keywords(
        self,
        index_name: str,
        keywords: list[str] | None = None,
        query: str | None = None,
        limit: int = 10,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        output_fields: list[str] | None = None,
        *,
        engine: str = "bm25",
    ) -> SearchResult:
        """Search the schema-declared, physically verified collection-wide BM25 index."""
        _validate_identifier(index_name, kind="logical index name")
        _validate_search_parameters(limit, offset, output_fields)
        _validate_filters(filters, self._field_names)
        if engine != "bm25":
            raise ValueError("engine must be bm25")
        if getattr(self, "_distributed", False):
            raise UnimplementedError("SPQ native BM25 has not been certified")
        if query is not None and not isinstance(query, str):
            raise TypeError("query must be text")
        if keywords is not None and (
            not isinstance(keywords, list) or not all(isinstance(term, str) for term in keywords)
        ):
            raise TypeError("keywords must be a list of strings")
        text = query if query is not None else " ".join(keywords or [])
        with self._lock:
            projection = self._fulltext_output_columns(output_fields)
            where, where_params = _build_where_clause(
                self._normalize_filter_date_times(filters), self._array_fields
            )
            if not text.strip() or limit == 0:
                return SearchResult(data=[])
            if not self.validate_fulltext_index(engine):
                raise FailedPreconditionError(f"A verified, ready {engine} full-text index is required")
            spec = self._fulltext_spec()
            table = _quote_identifier(self._name)
            index = _validate_identifier(spec["name"], kind="full-text index name")
            hint = f"/*+ indexscan ({self._name} {index}) */"
            predicate = f"WHERE ({where})" if where else ""
            # Neither the score predicate nor the secondary sort may enter
            # the native ordered scan. _bm25_page proves prefix completeness
            # before applying the actual page offset and limit.
            sql = (
                f"SELECT * FROM (SELECT {hint} {projection}, "
                f'"content" <&> %s AS _ov_fulltext_score FROM {table} {predicate} '
                'ORDER BY "content" <&> %s DESC LIMIT %s) AS _ov_hits '
                'WHERE _ov_fulltext_score > 0 ORDER BY _ov_fulltext_score DESC, "id"'
            )
            params = [text, *where_params, text]
            cursor = self._cursor()
            try:
                rows = self._bm25_page(cursor, sql, params, limit=limit, offset=offset)
                columns = [description[0] for description in cursor.description]
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()
        items = []
        for row in rows:
            record = self._row_to_dict(row, columns)
            score = float(record.pop("_ov_fulltext_score"))
            if not math.isfinite(score):
                raise ValueError("Database returned a non-finite full-text score")
            items.append(SearchItemResult(id=record.pop("id"), fields=record, score=score))
        return SearchResult(data=items)
