# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from typing import Any

from .vikingdb_catalog import CATALOG_LEVEL


def is_search_or_agg(path: str) -> bool:
    return path.startswith("/api/vikingdb/data/search/") or path == "/api/vikingdb/data/agg"


def account_partition_values(condition: Any) -> set[str]:
    """Only unconditional equality predicates can establish the account scope."""
    if not isinstance(condition, dict):
        return set()
    values = condition.get("conds")
    if condition.get("op") == "and" and isinstance(values, (list, tuple)):
        return set().union(*(account_partition_values(child) for child in values))
    if (
        condition.get("op") == "must"
        and condition.get("field") == "account_id"
        and isinstance(values, list)
        and len(values) == 1
        and isinstance(values[0], str)
        and values[0]
    ):
        return {values[0]}
    return set()


def with_account_partition(data: dict[str, Any], *, require_scope: bool = False) -> dict[str, Any]:
    scopes = account_partition_values(data.get("filter"))
    if len(scopes) == 1:
        scope = scopes.pop()
        if data.get("partition", scope) == scope:
            return {**data, "partition": scope}
        raise ValueError("VikingDB partition does not match the account_id filter")
    if require_scope:
        raise ValueError("VikingDB partitioned queries require one account_id scope")
    return data


def without_catalog_rows(data: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep Catalog declarations out of ordinary retrieval and counts."""
    fields = metadata.get("Fields")
    scalar_fields = metadata.get("ScalarIndex")
    if fields is not None and not any(field.get("FieldName") == "level" for field in fields):
        return data
    if scalar_fields is not None and "level" not in scalar_fields:
        return data
    exclude_catalog = {"op": "must_not", "field": "level", "conds": [CATALOG_LEVEL]}
    condition = data.get("filter")
    if condition:
        exclude_catalog = {"op": "and", "conds": [condition, exclude_catalog]}
    return {**data, "filter": exclude_catalog}
