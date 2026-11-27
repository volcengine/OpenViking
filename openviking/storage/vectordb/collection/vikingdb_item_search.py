# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Translate the VikingDB data-plane Item Search contract."""

from __future__ import annotations

import math
from typing import Any

from openviking.storage.errors import VikingDBException
from openviking.storage.vectordb.collection.vikingdb_catalog import catalog_mode
from openviking.storage.vectordb.collection.vikingdb_query_scope import account_partition_values


def build_item_group_request(
    meta: dict[str, Any] | None,
    *,
    project: str,
    collection_name: str,
    index_name: str,
    dense_vector: list[float] | None,
    sparse_vector: dict[str, float] | None,
    filters: dict[str, Any] | None,
    max_groups: int,
    aggregation: str,
    filter_only: bool,
    keywords_query: str | None = None,
    keywords_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not filter_only and keywords_query is None and not dense_vector:
        raise ValueError("VikingDB Item search requires a dense query vector")
    if aggregation not in ("max", "sum"):
        raise ValueError("Item aggregation must be max or sum")
    if type(max_groups) is not int or max_groups <= 0:
        raise ValueError("Item group limit must be a positive integer")

    if meta is not None:
        mode = catalog_mode(meta)
        if mode is None:
            raise NotImplementedError(
                "VikingDB Item search requires a pre-created account-partitioned collection "
                "with inherited item_uuids and non-inherited item_attrs"
            )
        if mode != "union" and not filter_only:
            raise NotImplementedError(
                "The VikingDB group_by protocol currently requires union inheritance"
            )
    partitions = account_partition_values(filters)
    if len(partitions) != 1:
        raise ValueError("VikingDB Item search requires one unconditional account_id equality")
    request = {
        "project": project,
        "collection_name": collection_name,
        "index_name": index_name,
        "partition": partitions.pop(),
        "filter": filters,
    }
    if filter_only:
        return request
    request["group_by"] = {
        "item_ids_field": "item_uuids",
        "item_attrs_field": "item_attrs",
        "op": aggregation,
        "group_limit": max_groups,
        # OV uses only group metadata. One is the smallest supported member limit.
        "record_per_group_limit": 1,
        "found_records_output_fields": [],
    }
    if keywords_query is not None:
        request["query"] = keywords_query
        if keywords_args:
            request.update(
                {key: keywords_args[key] for key in ("bm25_k1", "bm25_b") if key in keywords_args}
            )
            input_limit = keywords_args.get("post_process_input_limit")
            if input_limit is not None:
                request["advance"] = {"post_process_input_limit": max(input_limit, max_groups)}
    else:
        request["dense_vector"] = dense_vector
        if sparse_vector:
            request["sparse_vector"] = sparse_vector
    return request


def parse_item_group_result(
    result: Any,
    *,
    score_threshold: float | None,
    action: str = "/api/vikingdb/data/search/vector",
) -> dict[str, Any]:
    def invalid() -> None:
        raise VikingDBException(
            "Invalid VikingDB Item group response",
            error_type="invalid_response",
            retryable=False,
            action=action,
        )

    if not isinstance(result, dict) or not isinstance(result.get("groups"), list):
        invalid()
    groups = []
    seen = set()
    for row in result["groups"]:
        if not isinstance(row, dict):
            invalid()
        item_id = row.get("item_id")
        uri = row.get("item_uri")
        score = row.get("item_score")
        attrs = row.get("item_attrs")
        if (
            not isinstance(item_id, str)
            or not item_id
            or item_id in seen
            or not isinstance(uri, str)
            or not uri.startswith(("/", "viking://"))
            or not uri.strip("/")
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or "item_attrs" not in row
            or (
                attrs is not None
                and (not isinstance(attrs, list) or any(not isinstance(a, str) for a in attrs))
            )
        ):
            invalid()
        seen.add(item_id)
        group = {
            "entity_id": item_id,
            "entity_score": float(score),
            "entity_uri": uri,
            "entity_attrs": attrs,
        }
        if score_threshold is None or score >= score_threshold:
            groups.append(group)
    return {"groups": groups}
