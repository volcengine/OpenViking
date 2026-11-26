# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared Item operations for VikingDB private and API-key data-plane collections."""

from typing import Any, Dict, List, Optional

from .vikingdb_catalog import CATALOG_LEVEL, VikingDBCatalog, catalog_mode, is_account_sharded
from .vikingdb_query_scope import is_search_or_agg, with_account_partition


class VikingDBItemCollectionMixin:
    def _item_metadata(self) -> Dict[str, Any] | None:
        return self.meta_data

    def _initialize_item_collection(self, index_name: str) -> None:
        self.catalog_index_name = index_name
        self._catalog = VikingDBCatalog(self)
        meta = self._item_metadata()
        self._has_catalog_metadata = meta is None or (
            catalog_mode(meta) is not None
            and any(f.get("FieldName") == "level" for f in meta.get("Fields", []))
        )

    def _prepare_data_request(
        self, path: str, data: Dict[str, Any], *, include_catalog: bool = False
    ) -> Dict[str, Any]:
        is_query = is_search_or_agg(path)
        meta = self._item_metadata()
        account_sharded = meta is not None and is_account_sharded(meta)
        if is_query:
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
        if (
            is_query
            and not include_catalog
            and getattr(self, "_has_catalog_metadata", False)
            and (meta is not None or data.get("partition"))
        ):
            hidden = {"op": "must_not", "field": "level", "conds": [CATALOG_LEVEL]}
            condition = data.get("filter")
            data = {
                **data,
                "filter": {"op": "and", "conds": [condition, hidden]} if condition else hidden,
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
    ) -> Dict[str, Any]:
        from .vikingdb_item_search import (
            build_item_group_request,
            parse_item_group_result,
        )

        request = build_item_group_request(
            self._item_metadata(),
            project=self.project_name,
            collection_name=self.collection_name,
            index_name=index_name,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            filters=filters,
            max_groups=max_groups,
            aggregation=aggregation,
            filter_only=filter_only,
            keywords_query=keywords_query,
        )
        if filter_only:
            return self._catalog.filter_item_groups(
                request, max_groups=max_groups, score_threshold=score_threshold
            )
        path = (
            "/api/vikingdb/data/search/keywords"
            if keywords_query is not None
            else "/api/vikingdb/data/search/vector"
        )
        result = self._data_post(path, request)
        return parse_item_group_result(result, score_threshold=score_threshold, action=path)
