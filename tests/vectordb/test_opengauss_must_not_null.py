# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""``must_not`` filters must keep rows whose payload field was never set.

``NOT IN`` / ``NOT (x = ANY(col))`` evaluate to NULL when the column itself is
NULL, so the plain negation the openGauss adapter used to emit dropped every
row without a value for the filtered field. A row that lacks the field does
not carry the excluded value either, so the filter has to match it.
"""

from openviking.storage.vectordb_adapters.opengauss.sql import _build_where_clause


def test_scalar_must_not_keeps_rows_without_the_field():
    sql, params = _build_where_clause(
        {"op": "must_not", "field": "category", "conds": ["draft", "spam"]}
    )
    assert '"category" IS NULL OR "category" NOT IN (%s, %s)' in sql
    assert params == ["draft", "spam"]


def test_array_must_not_keeps_rows_without_the_field():
    sql, params = _build_where_clause(
        {"op": "must_not", "field": "search_tags", "conds": ["hidden=true"]},
        {"search_tags"},
    )
    assert '"search_tags" IS NULL OR (NOT (%s = ANY("search_tags")))' in sql
    assert params == ["hidden=true"]


def test_must_not_null_guard_survives_nesting():
    sql, params = _build_where_clause(
        {
            "op": "and",
            "conds": [
                {"op": "must", "field": "kind", "conds": ["note"]},
                {"op": "must_not", "field": "category", "conds": ["draft"]},
            ],
        }
    )
    assert '("category" IS NULL OR "category" NOT IN (%s))' in sql
    assert params == ["note", "draft"]
