# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openviking.storage.errors import ConnectionError
from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.collection.volcengine_clients import (
    ClientForDataApi,
    ClientForDataApiWithApiKey,
)
from openviking_cli.utils.logger import default_logger as logger


class VolcengineApiKeyCollection(ICollection):
    """VikingDB data-plane collection implementation using Bearer API key."""

    def __init__(
        self,
        api_key: str,
        host: Optional[str] = None,
        region: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None,
    ):
        resolved_host = (host or "").rstrip("/") or (
            ClientForDataApi._global_host.get(region or "") if region else None
        )
        if not resolved_host:
            raise ValueError(
                "host or region is required for Volcengine API key data-plane access"
            )
        self.data_client = ClientForDataApiWithApiKey(api_key, resolved_host)
        self.meta_data = meta_data if meta_data is not None else {}
        self.project_name = self.meta_data.get("ProjectName", "default")
        self.collection_name = self.meta_data.get("CollectionName", "")
        self.index_name = self.meta_data.get("IndexName", "default")

    @staticmethod
    def _build_response_error(response: Any, action: str) -> ConnectionError:
        try:
            result = response.json()
        except json.JSONDecodeError:
            result = {}

        message = ""
        if isinstance(result, dict):
            message = (
                result.get("message")
                or result.get("msg")
                or result.get("error")
                or response.text
            )
        else:
            message = response.text

        return ConnectionError(
            f"Request to {action} failed: {response.status_code} {message}"
        )

    @staticmethod
    def _sanitize_uri_value(v: Any) -> Any:
        """Remove viking:// prefix and normalize to /... format; return None for empty values."""
        if not isinstance(v, str):
            return v
        s = v.strip()
        if s in {"/", "viking://"}:
            return "/"
        if s.startswith("viking://"):
            s = s[len("viking://") :]
        s = s.strip("/")
        if not s:
            return None
        return f"/{s}"

    @classmethod
    def _sanitize_payload(cls, obj: Any) -> Any:
        """Recursively sanitize URI-related values in data/filter payload."""
        if isinstance(obj, dict):
            return cls._sanitize_dict_payload(obj)
        if isinstance(obj, list):
            return cls._sanitize_list_payload(obj)
        return obj

    @classmethod
    def _sanitize_dict_payload(cls, obj: Dict[str, Any]) -> Any:
        field_name = obj.get("field")
        if (
            field_name in ("uri", "parent_uri")
            and "conds" in obj
            and isinstance(obj["conds"], list)
        ):
            new_conds = cls._sanitize_filter_conds(obj["conds"])
            if not new_conds:
                return None
            obj = dict(obj)
            obj["conds"] = new_conds

        if obj.get("op") == "prefix" and "prefix" in obj:
            obj = dict(obj)
            if not cls._sanitize_prefix(obj):
                return None

        return cls._sanitize_dict_keys(obj)

    @classmethod
    def _sanitize_filter_conds(cls, conds: List[Any]) -> List[Any]:
        new_conds = []
        for x in conds:
            if isinstance(x, str):
                sv = cls._sanitize_uri_value(x)
                if sv:
                    new_conds.append(sv)
            else:
                y = cls._sanitize_payload(x)
                if y is not None:
                    new_conds.append(y)
        return new_conds

    @classmethod
    def _sanitize_prefix(cls, obj: Dict[str, Any]) -> bool:
        pv = cls._sanitize_uri_value(obj.get("prefix"))
        if pv is None:
            return False
        obj["prefix"] = pv
        return True

    @classmethod
    def _sanitize_dict_keys(cls, obj: Dict[str, Any]) -> Dict[str, Any]:
        new_obj: Dict[str, Any] = {}
        for k, v in obj.items():
            if k in ("uri", "parent_uri"):
                sv = cls._sanitize_uri_value(v)
                if sv is not None:
                    new_obj[k] = sv
            else:
                y = cls._sanitize_payload(v)
                if y is not None:
                    new_obj[k] = y
        return new_obj

    @classmethod
    def _sanitize_list_payload(cls, obj: List[Any]) -> List[Any]:
        sanitized_list = []
        for x in obj:
            y = cls._sanitize_payload(x)
            if y is not None:
                sanitized_list.append(y)
        return sanitized_list

    def _data_post(self, path: str, data: Dict[str, Any]):
        safe_data = self._sanitize_payload(data)
        response = self.data_client.do_req("POST", path, req_body=safe_data)
        if response.status_code != 200:
            raise self._build_response_error(response, path)
        try:
            result = response.json()
            return result.get("result", {})
        except json.JSONDecodeError:
            logger.warning("Invalid JSON response from %s", path)
            return {}

    def _data_get(self, path: str, params: Dict[str, Any]):
        response = self.data_client.do_req("GET", path, req_params=params)
        if response.status_code != 200:
            raise self._build_response_error(response, path)
        try:
            result = response.json()
            return result.get("result", {})
        except json.JSONDecodeError:
            logger.warning("Invalid JSON response from %s", path)
            return {}

    def _parse_fetch_result(self, data: Dict[str, Any]) -> FetchDataInCollectionResult:
        result = FetchDataInCollectionResult()
        if isinstance(data, dict):
            if "fetch" in data:
                fetch = data.get("fetch", [])
                result.items = [
                    DataItem(
                        id=item.get("id"),
                        fields=item.get("fields"),
                    )
                    for item in fetch
                ]
            if "ids_not_exist" in data:
                result.ids_not_exist = data.get("ids_not_exist", [])
        return result

    def _parse_search_result(self, data: Dict[str, Any]) -> SearchResult:
        result = SearchResult()
        if isinstance(data, dict) and "data" in data:
            data_list = data.get("data", [])
            result.data = [
                SearchItemResult(
                    id=item.get("id"),
                    fields=item.get("fields"),
                    score=item.get("score"),
                )
                for item in data_list
            ]
        return result

    def _parse_aggregate_result(
        self,
        data: Dict[str, Any],
        op: str = "count",
        field: Optional[str] = None,
    ) -> AggregateResult:
        result = AggregateResult(op=op, field=field)
        if isinstance(data, dict):
            if "agg" in data:
                result.agg = data["agg"]
            else:
                result.agg = data
        return result

    def _base_data_payload(self, index_name: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "project": self.project_name,
            "collection_name": self.collection_name,
        }
        if index_name:
            payload["index_name"] = index_name
        return payload

    def update(
        self,
        fields: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        raise NotImplementedError(
            "volcengine api_key mode is data-plane only; update is not supported"
        )

    def get_meta_data(self):
        return {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
            "IndexName": self.index_name,
            "Description": "data-plane only backend",
        }

    def close(self):
        pass

    def drop(self):
        raise NotImplementedError(
            "volcengine api_key mode is data-plane only; drop is not supported"
        )

    def create_index(self, index_name: str, meta_data: Dict[str, Any]):
        raise NotImplementedError(
            "volcengine api_key mode is data-plane only; create_index is not supported"
        )

    def has_index(self, index_name: str):
        return index_name == self.index_name

    def get_index(self, index_name: str):
        return None

    def list_indexes(self):
        return [self.index_name]

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        raise NotImplementedError(
            "volcengine api_key mode is data-plane only; update_index is not supported"
        )

    def get_index_meta_data(self, index_name: str):
        if index_name != self.index_name:
            return None
        return {
            "IndexName": self.index_name,
            "Description": "data-plane only backend",
        }

    def drop_index(self, index_name: str):
        raise NotImplementedError(
            "volcengine api_key mode is data-plane only; drop_index is not supported"
        )

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        path = "/api/vikingdb/data/upsert"
        data = {
            **self._base_data_payload(),
            "data": data_list,
            "ttl": ttl,
        }
        return self._data_post(path, data)

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        path = "/api/vikingdb/data/fetch_in_collection"
        data = {
            **self._base_data_payload(),
            "ids": primary_keys,
        }
        resp_data = self._data_post(path, data)
        return self._parse_fetch_result(resp_data)

    def delete_data(self, primary_keys: List[Any]):
        path = "/api/vikingdb/data/delete"
        data = {
            **self._base_data_payload(),
            "ids": primary_keys,
        }
        return self._data_post(path, data)

    def delete_all_data(self):
        path = "/api/vikingdb/data/delete"
        data = {
            **self._base_data_payload(),
            "del_all": True,
        }
        return self._data_post(path, data)

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
        if dense_vector is None and sparse_vector is None:
            raise ValueError("At least one of dense_vector or sparse_vector is required")

        path = "/api/vikingdb/data/search/vector"
        data = {
            **self._base_data_payload(index_name=index_name),
            "dense_vector": dense_vector,
            "filter": filters,
            "output_fields": output_fields,
            "limit": limit,
            "offset": offset,
        }
        if sparse_vector:
            data["sparse_vector"] = sparse_vector
        resp_data = self._data_post(path, data)
        return self._parse_search_result(resp_data)

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
        path = "/api/vikingdb/data/search/keywords"
        data = {
            **self._base_data_payload(index_name=index_name),
            "keywords": keywords,
            "query": query,
            "filter": filters,
            "output_fields": output_fields,
            "limit": limit,
            "offset": offset,
        }
        resp_data = self._data_post(path, data)
        return self._parse_search_result(resp_data)

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        path = "/api/vikingdb/data/search/id"
        data = {
            **self._base_data_payload(index_name=index_name),
            "id": id,
            "filter": filters,
            "output_fields": output_fields,
            "limit": limit,
            "offset": offset,
        }
        resp_data = self._data_post(path, data)
        return self._parse_search_result(resp_data)

    def search_by_multimodal(
        self,
        index_name: str,
        text: Optional[str] = None,
        image: Optional[Any] = None,
        video: Optional[Any] = None,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        path = "/api/vikingdb/data/search/multi_modal"
        data = {
            **self._base_data_payload(index_name=index_name),
            "text": text,
            "image": image,
            "video": video,
            "filter": filters,
            "output_fields": output_fields,
            "limit": limit,
            "offset": offset,
        }
        resp_data = self._data_post(path, data)
        return self._parse_search_result(resp_data)

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        path = "/api/vikingdb/data/search/random"
        data = {
            **self._base_data_payload(index_name=index_name),
            "filter": filters,
            "output_fields": output_fields,
            "limit": limit,
            "offset": offset,
        }
        resp_data = self._data_post(path, data)
        return self._parse_search_result(resp_data)

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
        path = "/api/vikingdb/data/search/scalar"
        data = {
            **self._base_data_payload(index_name=index_name),
            "field": field,
            "order": order,
            "filter": filters,
            "output_fields": output_fields,
            "limit": limit,
            "offset": offset,
        }
        resp_data = self._data_post(path, data)
        return self._parse_search_result(resp_data)

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        path = "/api/vikingdb/data/agg"
        data = {
            **self._base_data_payload(index_name=index_name),
            "op": op,
            "field": field,
            "filter": filters,
        }
        if cond is not None:
            data["cond"] = cond
        resp_data = self._data_post(path, data)
        return self._parse_aggregate_result(resp_data, op, field)
