# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import copy
import json
from typing import Any, Dict, List, Optional

import requests

from openviking.storage.vectordb.collection.collection import Collection, ICollection
from openviking.storage.vectordb.collection.result import (
    AggregateResult,
    DataItem,
    FetchDataInCollectionResult,
    SearchItemResult,
    SearchResult,
)

# Default request timeout (seconds)
DEFAULT_TIMEOUT = 30

headers = {"Content-Type": "application/json"}


def get_or_create_http_collection(
    host: str = "127.0.0.1", port: int = 5000, meta_data: Optional[Dict[str, Any]] = None
):
    """Create or retrieve a Collection via HTTP.

    Args:
        host: Host address of the HTTP service.
        port: Port number of the HTTP service.
        meta_data: Collection metadata.

    Returns:
        Collection: The collection instance.

    Raises:
        Exception: If the collection creation/retrieval fails.
    """
    if meta_data is None:
        meta_data = {}
    url = "http://{}:{}/CreateVikingdbCollection".format(host, port)
    if "Fields" in meta_data:
        meta_data["Fields"] = json.dumps(meta_data["Fields"])
    response = requests.post(url, headers=headers, json=meta_data, timeout=DEFAULT_TIMEOUT)
    # logger.info(f"CreateVikingdbCollection response: {response.text}")
    if response.status_code == 200:
        http_collection = HttpCollection(host, port, meta_data)
        return Collection(http_collection)
    else:
        raise Exception(f"Failed to get or create collection: {response.text}")


def list_vikingdb_collections(
    host: str = "127.0.0.1", port: int = 5000, project_name: str = "default"
):
    """List all VikingDB collections.

    Args:
        host: Host address of the HTTP service.
        port: Port number of the HTTP service.
        project_name: The name of the project.

    Returns:
        List[Dict[str, Any]]: A list of collection information.
    """
    url = "http://{}:{}/ListVikingdbCollection".format(host, port)
    response = requests.get(
        url,
        headers=headers,
        params={
            "ProjectName": project_name,
        },
        timeout=DEFAULT_TIMEOUT,
    )
    # logger.info(f"ListVikingdbCollection response: {response.text}")
    if response.status_code != 200:
        return []
    result = json.loads(response.text)
    return result.get("data", [])


class HttpCollection(ICollection):
    """HTTP implementation of the ICollection interface."""

    def __init__(
        self, ip: str = "127.0.0.1", port: int = 5000, meta_data: Optional[Dict[str, Any]] = None
    ):
        self.ip = ip
        self.port = port
        self.meta_data = meta_data if meta_data is not None else {}
        self.url_prefix = "http://{}:{}/".format(ip, port)
        self.project_name = self.meta_data.get("ProjectName", "default")
        self.collection_name = self.meta_data.get("CollectionName", "")

    def update(self, fields: Optional[Dict[str, Any]] = None, description: Optional[str] = None):
        data = {
            "ProjectName": self.project_name,
            "CollectionName": self.collection_name,
        }
        if fields:
            data["Fields"] = json.dumps(fields)
        if description is not None:
            data["Description"] = description
        url = self.url_prefix + "UpdateVikingdbCollection"
        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"UpdateVikingdbCollection response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

    def get_meta_data(self):
        url = self.url_prefix + "GetVikingdbCollection"
        response = requests.get(
            url,
            headers=headers,
            params={
                "ProjectName": self.project_name,
                "CollectionName": self.collection_name,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"GetCollectionMeta response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

    def close(self):
        pass

    def drop(self):
        url = self.url_prefix + "DeleteVikingdbCollection"
        response = requests.post(
            url,
            headers=headers,
            json={
                "ProjectName": self.project_name,
                "CollectionName": self.collection_name,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"DeleteVikingdbCollection response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

    def create_index(self, index_name: str, meta_data: Dict[str, Any]):
        url = self.url_prefix + "CreateVikingdbIndex"
        data = copy.deepcopy(meta_data)
        data["IndexName"] = index_name
        data["ProjectName"] = self.project_name
        data["CollectionName"] = self.collection_name

        if "VectorIndex" in meta_data:
            data["VectorIndex"] = json.dumps(meta_data["VectorIndex"])
        if "ScalarIndex" in meta_data:
            data["ScalarIndex"] = json.dumps(meta_data["ScalarIndex"])
        response = requests.post(url, headers=headers, json=data, timeout=DEFAULT_TIMEOUT)
        # logger.info(f"CreateVikingdbCollection response: {response.text}")
        if response.status_code != 200:
            raise Exception(f"Failed to create index: {response.text}")

        pass

    def has_index(self, index_name: str):
        indexes = self.list_indexes()
        return index_name in indexes if isinstance(indexes, list) else False

    def get_index(self, index_name: str):
        return self.get_index_meta_data(index_name)

    def list_indexes(
        self,
    ):
        url = self.url_prefix + "ListVikingdbIndex"
        response = requests.get(
            url,
            headers=headers,
            params={
                "ProjectName": self.project_name,
                "CollectionName": self.collection_name,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"ListVikingdbIndex response: {response.text}")
        if response.status_code != 200:
            return []
        result = json.loads(response.text)
        return result.get("data", [])

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
            data["ScalarIndex"] = json.dumps(scalar_index)
        if description is not None:
            data["Description"] = description
        url = self.url_prefix + "UpdateVikingdbIndex"
        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"UpdateVikingdbIndex response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

    def get_index_meta_data(self, index_name: str):
        url = self.url_prefix + "GetVikingdbIndex"
        response = requests.get(
            url,
            headers=headers,
            params={
                "ProjectName": self.project_name,
                "CollectionName": self.collection_name,
                "IndexName": index_name,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"GetVikingdbIndex response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

    def drop_index(self, index_name: str):
        url = self.url_prefix + "DeleteVikingdbIndex"
        response = requests.post(
            url,
            headers=headers,
            json={
                "ProjectName": self.project_name,
                "CollectionName": self.collection_name,
                "IndexName": index_name,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"DeleteVikingdbIndex response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

    def upsert_data(self, data_list: List[Dict[str, Any]], ttl: int = 0):
        url = self.url_prefix + "api/vikingdb/data/upsert"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "fields": json.dumps(data_list),
                "ttl": ttl,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"UpsertData response: {response.text}")
        if response.status_code != 200:
            return []
        result = json.loads(response.text)
        return result.get("data", [])

    def fetch_data(self, primary_keys: List[Any]) -> FetchDataInCollectionResult:
        url = self.url_prefix + "api/vikingdb/data/fetch_in_collection"
        response = requests.get(
            url,
            headers=headers,
            params={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "ids": json.dumps(primary_keys),
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"FetchData response: {response.text}")
        if response.status_code != 200:
            return FetchDataInCollectionResult()
        result = json.loads(response.text)
        data = result.get("data", {})

        # Parse the data into FetchDataInCollectionResult
        fetch_result = FetchDataInCollectionResult()

        if isinstance(data, dict):
            if "fetch" in data:
                fetch = data.get("fetch", [])
                fetch_result.items = [
                    DataItem(
                        id=item.get("id"),
                        fields=item.get("fields"),
                    )
                    for item in fetch
                ]
            if "ids_not_exist" in data:
                fetch_result.ids_not_exist = data.get("ids_not_exist", [])

        return fetch_result

    def delete_data(self, primary_keys: List[Any]):
        url = self.url_prefix + "api/vikingdb/data/delete"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "ids": json.dumps(primary_keys),
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"DeleteData response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

    def delete_all_data(self):
        url = self.url_prefix + "api/vikingdb/data/delete"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "del_all": True,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"DeleteAllData response: {response.text}")
        if response.status_code != 200:
            return {}
        result = json.loads(response.text)
        return result.get("data", {})

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
        url = self.url_prefix + "api/vikingdb/data/search/vector"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "index_name": index_name,
                "dense_vector": json.dumps(dense_vector) if dense_vector else None,
                "sparse_vector": json.dumps(sparse_vector) if sparse_vector else None,
                "filter": json.dumps(filters) if filters else None,
                "output_fields": json.dumps(output_fields) if output_fields else None,
                "limit": limit,
                "offset": offset,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"SearchByVector response: {response.text}")
        if response.status_code != 200:
            return SearchResult()

        data = json.loads(response.text).get("data", {})
        result = SearchResult()
        if isinstance(data, dict) and "data" in data:
            result.data = [
                SearchItemResult(
                    id=item.get("id"),
                    fields=item.get("fields"),
                    score=item.get("score"),
                )
                for item in data.get("data", [])
            ]
        return result

    def search_by_id(
        self,
        index_name: str,
        id: Any,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        url = self.url_prefix + "api/vikingdb/data/search/id"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "index_name": index_name,
                "id": id,
                "filter": json.dumps(filters) if filters else None,
                "output_fields": json.dumps(output_fields) if output_fields else None,
                "limit": limit,
                "offset": offset,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"SearchById response: {response.text}")
        if response.status_code != 200:
            return SearchResult()

        data = json.loads(response.text).get("data", {})
        result = SearchResult()
        if isinstance(data, dict) and "data" in data:
            result.data = [
                SearchItemResult(
                    id=item.get("id"),
                    fields=item.get("fields"),
                    score=item.get("score"),
                )
                for item in data.get("data", [])
            ]
        return result

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
        url = self.url_prefix + "api/vikingdb/data/search/multi_modal"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "index_name": index_name,
                "text": text,
                "image": image,
                "video": video,
                "filter": json.dumps(filters) if filters else None,
                "output_fields": json.dumps(output_fields) if output_fields else None,
                "limit": limit,
                "offset": offset,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"SearchByMultiModal response: {response.text}")
        if response.status_code != 200:
            return SearchResult()

        data = json.loads(response.text).get("data", {})
        result = SearchResult()
        if isinstance(data, dict) and "data" in data:
            result.data = [
                SearchItemResult(
                    id=item.get("id"),
                    fields=item.get("fields"),
                    score=item.get("score"),
                )
                for item in data.get("data", [])
            ]
        return result

    def search_by_random(
        self,
        index_name: str,
        limit: int = 10,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        output_fields: Optional[List[str]] = None,
    ) -> SearchResult:
        url = self.url_prefix + "api/vikingdb/data/search/random"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "index_name": index_name,
                "filter": json.dumps(filters) if filters else None,
                "output_fields": json.dumps(output_fields) if output_fields else None,
                "limit": limit,
                "offset": offset,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"SearchByRandom response: {response.text}")
        if response.status_code != 200:
            return SearchResult()

        data = json.loads(response.text).get("data", {})
        result = SearchResult()
        if isinstance(data, dict) and "data" in data:
            result.data = [
                SearchItemResult(
                    id=item.get("id"),
                    fields=item.get("fields"),
                    score=item.get("score"),
                )
                for item in data.get("data", [])
            ]
        return result

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
        url = self.url_prefix + "api/vikingdb/data/search/keywords"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "index_name": index_name,
                "keywords": json.dumps(keywords) if keywords else None,
                "query": query,
                "filter": json.dumps(filters) if filters else None,
                "output_fields": json.dumps(output_fields) if output_fields else None,
                "limit": limit,
                "offset": offset,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"SearchByKeywords response: {response.text}")
        if response.status_code != 200:
            return SearchResult()

        data = json.loads(response.text).get("data", {})
        result = SearchResult()
        if isinstance(data, dict) and "data" in data:
            result.data = [
                SearchItemResult(
                    id=item.get("id"),
                    fields=item.get("fields"),
                    score=item.get("score"),
                )
                for item in data.get("data", [])
            ]
        return result

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
        url = self.url_prefix + "api/vikingdb/data/search/scalar"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "index_name": index_name,
                "field": field,
                "order": order,
                "filter": json.dumps(filters) if filters else None,
                "output_fields": json.dumps(output_fields) if output_fields else None,
                "limit": limit,
                "offset": offset,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        # logger.info(f"SearchByScalar response: {response.text}")
        if response.status_code != 200:
            return SearchResult()

        data = json.loads(response.text).get("data", {})
        result = SearchResult()
        if isinstance(data, dict) and "data" in data:
            result.data = [
                SearchItemResult(
                    id=item.get("id"),
                    fields=item.get("fields"),
                    score=item.get("score"),
                )
                for item in data.get("data", [])
            ]
        return result

    def aggregate_data(
        self,
        index_name: str,
        op: str = "count",
        field: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        cond: Optional[Dict[str, Any]] = None,
    ) -> AggregateResult:
        url = self.url_prefix + "api/vikingdb/data/aggregate"
        response = requests.post(
            url,
            headers=headers,
            json={
                "project": self.project_name,
                "collection_name": self.collection_name,
                "index_name": index_name,
                "agg": {
                    "op": op,
                    "field": field,
                },
                "filter": filters,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            return AggregateResult(agg={}, op=op, field=field)
        result = json.loads(response.text)
        data = result.get("data", {})
        return self._parse_aggregate_result(data, op, field)

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
