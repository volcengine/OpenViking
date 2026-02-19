# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
from typing import Any, Dict, List, Optional

from openviking.storage.vectordb.collection.collection import ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)
from openviking.storage.vectordb.collection.vikingdb_clients import (
    VIKINGDB_APIS,
    VikingDBClient,
)
from openviking_cli.utils.logger import default_logger as logger


class VikingDBCollection(ICollection):
    """
    VikingDB collection implementation for private deployment.
    """

    def __init__(
        self,
        host: str,
        headers: Optional[Dict[str, str]] = None,
        meta_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()
        self.client = VikingDBClient(host, headers)
        self.meta_data = meta_data if meta_data is not None else {}
        self.project_name = self.meta_data.get("ProjectName", "default")
        self.collection_name = self.meta_data.get("CollectionName", "")

    def _console_post(self, data: Dict[str, Any], action: str):
        path, method = VIKINGDB_APIS[action]
        response = self.client.do_req(method, path=path, req_body=data)
        if response.status_code != 200:
            logger.error(f"Request to {action} failed: {response.text}")
            return {}
        try:
            result = response.json()
            if "Result" in result:
                return result["Result"]
            return result.get("data", {})
        except json.JSONDecodeError:
            return {}

    def _console_get(self, params: Optional[Dict[str, Any]], action: str):
        if params is None:
            params = {}
        path, method = VIKINGDB_APIS[action]
        # Console GET actions are actually POSTs in VikingDB API
        response = self.client.do_req(method, path=path, req_body=params)

        if response.status_code != 200:
            logger.error(f"Request to {action} failed: {response.text}")
            return {}
        try:
            result = response.json()
            return result.get("Result", {})
        except json.JSONDecodeError:
            return {}

    def _data_post(self, path: str, data: Dict[str, Any]):
        response = self.client.do_req("POST", path, req_body=data)
        if response.status_code != 200:
            logger.error(f"Request to {path} failed: {response.text}")
            return {}
        try:
            result = response.json()
            return result.get("result", {})
        except json.JSONDecodeError:
            return {}

    def _data_get(self, path: str, params: Dict[str, Any]):
        response = self.client.do_req("GET", path, req_params=params)
        if response.status_code != 200:
            logger.error(f"Request to {path} failed: {response.text}")
            return {}
        try:
            result = response.json()
            return result.get("result", {})
        except json.JSONDecodeError:
            return {}

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        data = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
        }
        if fields:
            data["Fields"] = fields
        if description is not None:
            data["Description"] = description

        return self._console_post(data, action="UpdateVikingdbCollection")

    def get_meta_data(self):
        params = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
        }
        return self._console_get(params, action="GetVikingdbCollection")

    def close(self):
        pass

    def drop(self):
        raise NotImplementedError("collection should be managed manually")

    def create_index(self, index_name: str, meta_data: Dict[str, Any]):
        raise NotImplementedError("index should be pre-created")

    def has_index(self, index_name: str):
        indexes = self.list_indexes()
        return index_name in indexes if isinstance(indexes, list) else False

    def get_index(self, index_name: str):
        return self.get_index_meta_data(index_name)

    def list_indexes(self):
        params = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
        }
        return self._console_get(params, action="ListVikingdbIndex")

    def update_index(
        self,
        index_name: str,
        scalar_index: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ):
        raise NotImplementedError("index should be managed manually")

    def get_index_meta_data(self, index_name: str):
        params = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
            "IndexName": index_name,
        }
        return self._console_get(params, action="GetVikingdbIndex")

    def drop_index(self, index_name: str):
        raise NotImplementedError("index should be managed manually")

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        path = "/api/vikingdb/data/upsert"
        data = {
            "project": self.project_name,
            "collection_name": self.collection_name,
            "data": data_list,
            "ttl": ttl,
        }
        return self._data_post(path, data)

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        path = "/api/vikingdb/data/fetch_in_collection"
        data = {
            "project": self.project_name,
            "collection_name": self.collection_name,
            "ids": primary_keys,
        }
        resp_data = self._data_post(path, data)
        return self._parse_fetch_result(resp_data)

    def delete_data(self, primary_keys: List[Any]):
        path = "/api/vikingdb/data/delete"
        data = {
            "project": self.project_name,
            "collection_name": self.collection_name,
            "ids": primary_keys,
        }
        return self._data_post(path, data)

    def delete_all_data(self):
        path = "/api/vikingdb/data/delete"
        data = {
            "project": self.project_name,
            "collection_name": self.collection_name,
            "del_all": True,
        }
        return self._data_post(path, data)

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
        path = "/api/vikingdb/data/search/vector"
        data = {
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
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
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
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
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
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
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
            "filter": filters,
            "output_fields": output_fields,
            "limit": limit,
            "offset": offset,
        }
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
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
            "keywords": keywords,
            "query": query,
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
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
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
        path = "/api/vikingdb/data/aggregate"
        data = {
            "project": self.project_name,
            "collection_name": self.collection_name,
            "index_name": index_name,
            "agg": {
                "op": op,
                "field": field,
            },
            "filter": filters,
        }
        resp_data = self._data_post(path, data)
        return self._parse_aggregate_result(resp_data, op, field)

    def _parse_aggregate_result(
        self, data: Dict[str, Any], op: str, field: Optional[str]
    ) -> AggregateResult:
        result = AggregateResult(op=op, field=field)
        if isinstance(data, dict):
            if "agg" in data:
                result.agg = data["agg"]
            else:
                result.agg = data
        return result
