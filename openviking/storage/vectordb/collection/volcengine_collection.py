# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import copy
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
from openviking.storage.vectordb.collection.volcengine_clients import (
    VIKING_DB_VERSION,
    ClientForConsoleApi,
    ClientForDataApi,
)
from openviking_cli.utils.logger import default_logger as logger


def get_or_create_volcengine_collection(config: Dict[str, Any], meta_data: Dict[str, Any]):
    """
    Get or create a Volcengine Collection.

    Args:
        config: Configuration dictionary containing AK, SK, Region.
        meta_data: Collection metadata.
    """
    # Extract configuration
    ak = config.get("AK")
    sk = config.get("SK")
    region = config.get("Region")
    host = config.get("Host", "")

    collection_name = meta_data.get("CollectionName")
    if not collection_name:
        raise ValueError("CollectionName is required in config")

    # Initialize Console client for creating Collection
    client = ClientForConsoleApi(ak, sk, region, host)

    # Try to create Collection
    try:
        params = {"Action": "CreateVikingdbCollection", "Version": VIKING_DB_VERSION}
        response = client.do_req("POST", req_params=params, req_body=meta_data)
        logger.info(f"Create collection response: {response.text}")
        if response.status_code != 200:
            result = response.json()
            if "AlreadyExists" in result.get("ResponseMetadata", {}).get("Error", {}).get(
                "Code", ""
            ):
                pass
            else:
                raise Exception(
                    f"Failed to create collection: {response.status_code} {response.text}"
                )
    except Exception as e:
        logger.error(f"Failed to create collection: {e}")
        raise e

    logger.info(f"Collection {collection_name} created successfully")
    return VolcengineCollection(ak, sk, region, host, meta_data)


class VolcengineCollection(ICollection):
    def __init__(
        self,
        ak: str,
        sk: str,
        region: str,
        host: str,
        meta_data: Optional[Dict[str, Any]] = None,
    ):
        self.console_client = ClientForConsoleApi(ak, sk, region, host)
        self.data_client = ClientForDataApi(ak, sk, region, host)
        self.meta_data = meta_data if meta_data is not None else {}
        self.project_name = self.meta_data.get("ProjectName", "default")
        self.collection_name = self.meta_data.get("CollectionName", "")

    def _console_post(self, data: Dict[str, Any], action: str):
        params = {"Action": action, "Version": VIKING_DB_VERSION}
        response = self.console_client.do_req("POST", req_params=params, req_body=data)
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
        req_params = {"Action": action, "Version": VIKING_DB_VERSION}
        req_body = params

        response = self.console_client.do_req("POST", req_params=req_params, req_body=req_body)

        if response.status_code != 200:
            logger.error(f"Request to {action} failed: {response.text}")
            return {}
        try:
            result = response.json()
            return result.get("Result", {})
        except json.JSONDecodeError:
            return {}

    def _data_post(self, path: str, data: Dict[str, Any]):
        response = self.data_client.do_req("POST", path, req_body=data)
        if response.status_code != 200:
            logger.error(f"Request to {path} failed: {response.text}")
            return {}
        try:
            result = response.json()
            return result.get("result", {})
        except json.JSONDecodeError:
            return {}

    def _data_get(self, path: str, params: Dict[str, Any]):
        response = self.data_client.do_req("GET", path, req_params=params)
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
        data = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
        }
        return self._console_post(data, action="DeleteVikingdbCollection")

    def create_index(self, index_name: str, meta_data: Dict[str, Any]):
        data = copy.deepcopy(meta_data)
        data["IndexName"] = index_name
        data["ProjectName"] = self.project_name
        data["CollectionName"] = self.collection_name

        params = {"Action": "CreateVikingdbIndex", "Version": VIKING_DB_VERSION}
        response = self.console_client.do_req("POST", req_params=params, req_body=data)
        if response.status_code != 200:
            result = response.json()
            if "AlreadyExists" in result.get("ResponseMetadata", {}).get("Error", {}).get(
                "Code", ""
            ):
                pass
            else:
                raise Exception(
                    f"Failed to create collection: {response.status_code} {response.text}"
                )

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
        data = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
            "IndexName": index_name,
        }
        if scalar_index:
            data["ScalarIndex"] = scalar_index
        if description is not None:
            data["Description"] = description

        return self._console_post(data, action="UpdateVikingdbIndex")

    def get_index_meta_data(self, index_name: str):
        params = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
            "IndexName": index_name,
        }
        return self._console_get(params, action="GetVikingdbIndex")

    def drop_index(self, index_name: str):
        data = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
            "IndexName": index_name,
        }
        return self._console_post(data, action="DeleteVikingdbIndex")

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
        # print(resp_data)
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
