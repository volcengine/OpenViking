# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared Item operations for VikingDB private and API-key data-plane collections."""

import math
from typing import Any, Dict, List, Optional

from openviking.core.search_args import keyword_recall_options
from openviking.storage.errors import VikingDBException

from .vikingdb_catalog import VikingDBCatalog, catalog_mode, is_account_sharded
from .vikingdb_query_scope import account_partition_values, is_search_or_agg, with_account_partition


class VikingDBItemCollectionMixin:
    def _item_metadata(self) -> Dict[str, Any] | None:
        return self.meta_data

    def _initialize_item_collection(self) -> None:
        self._catalog = VikingDBCatalog(self)

    def _prepare_data_request(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        is_query = is_search_or_agg(path)
        meta = self._item_metadata()
        account_sharded = meta is not None and is_account_sharded(meta)
        if is_query:
            shard_field = self.meta_data.get("ShardBy", self.meta_data.get("PartitionBy"))
            if account_sharded or (meta is None and shard_field in (None, "account_id")):
                data = with_account_partition(data, require_scope=account_sharded)
        elif meta is None or account_sharded:
            if (
                meta is not None
                and ("ids" in data or path.endswith("/update"))
                and not data.get("partition_by")
            ):
                raise ValueError("VikingDB partitioned point operations require partition_by")
            if path.endswith("/update") and data.get("partition_by"):
                scope = data["partition_by"]
                rows = data.get("data", [])
                if any(row.get("account_id", scope) != scope for row in rows):
                    raise ValueError("VikingDB updates cannot change the account_id partition")
                # ShardBy selects the record's partition and cannot be updated,
                # even when a full OV record repeats its current account_id.
                data = {
                    **data,
                    "data": [{k: v for k, v in row.items() if k != "account_id"} for row in rows],
                }
        return data

    def catalog_execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._catalog.execute(request)

    def search_item_groups(
        self,
        index_name: str,
        *,
        dense_vector: Optional[List[float]] = None,
        sparse_vector: Optional[Dict[str, float]] = None,
        filters: Optional[Dict[str, Any]] = None,
        max_groups: int = 10,
        aggregation: str = "max",
        score_threshold: Optional[float] = None,
        filter_only: bool = False,
        keywords_query: Optional[str] = None,
        keywords_args: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not filter_only and keywords_query is None and not dense_vector:
            raise ValueError("VikingDB Item search requires a dense query vector")
        if aggregation not in ("max", "sum"):
            raise ValueError("Item aggregation must be max or sum")
        if type(max_groups) is not int or max_groups <= 0:
            raise ValueError("Item group limit must be a positive integer")

        meta = self._item_metadata()
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
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
            "partition": partitions.pop(),
            "filter": filters,
        }
        if filter_only:
            return self._catalog.filter_item_groups(
                request, max_groups=max_groups, score_threshold=score_threshold
            )

        request["group_by"] = {
            "item_ids_field": "item_uuids",
            "item_attrs_field": "item_attrs",
            "op": aggregation,
            "group_limit": max_groups,
            # Only group metadata is used. VikingDB requires at least one member.
            "record_per_group_limit": 1,
            "found_records_output_fields": [],
        }
        if keywords_query is not None:
            path = "/api/vikingdb/data/search/keywords"
            request["query"] = keywords_query
            request.update(keyword_recall_options(keywords_args, max_groups))
        else:
            path = "/api/vikingdb/data/search/vector"
            request["dense_vector"] = dense_vector
            if sparse_vector:
                request["sparse_vector"] = sparse_vector
        return _parse_item_groups(self._data_post(path, request), score_threshold, path)


def _parse_item_groups(result: Any, score_threshold: float | None, action: str) -> dict[str, Any]:
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
