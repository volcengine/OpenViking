# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Private VikingDB deployment backend driver."""

from __future__ import annotations

from openviking.storage.vector_store.driver import VectorStoreDriver
from openviking.storage.vector_store.drivers.common import normalize_collection_names
from openviking.storage.vector_store.registry import register_driver
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.vikingdb_clients import (
    VIKINGDB_APIS,
    VikingDBClient,
)
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection


@register_driver("vikingdb")
class VikingDBPrivateDriver(VectorStoreDriver):
    """Driver for private VikingDB deployment."""

    def __init__(
        self,
        *,
        host: str,
        headers: dict | None,
        project_name: str,
        collection_name: str,
    ):
        self.mode = "vikingdb"
        self._host = host
        self._headers = headers
        self._project_name = project_name
        self._collection_name = collection_name
        self._collection = None

    @classmethod
    def from_config(cls, config):
        if not config.vikingdb or not config.vikingdb.host:
            raise ValueError("VikingDB backend requires a valid host")

        return cls(
            host=config.vikingdb.host,
            headers=config.vikingdb.headers,
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
        )

    def _match(self, name: str) -> bool:
        return name == self._collection_name

    def _client(self) -> VikingDBClient:
        return VikingDBClient(self._host, self._headers)

    def _fetch_collection_meta(self):
        path, method = VIKINGDB_APIS["GetVikingdbCollection"]
        req = {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }
        response = self._client().do_req(method, path=path, req_body=req)
        if response.status_code != 200:
            return None
        result = response.json()
        meta = result.get("Result", {})
        return meta or None

    def has_collection(self, name: str) -> bool:
        if not self._match(name):
            return False
        return self._fetch_collection_meta() is not None

    def get_collection(self, name: str):
        if not self._match(name):
            return None
        if self._collection is not None:
            return self._collection
        meta = self._fetch_collection_meta()
        if meta is None:
            return None
        self._collection = Collection(
            VikingDBCollection(
                host=self._host,
                headers=self._headers,
                meta_data=meta,
            )
        )
        return self._collection

    def create_collection(self, name: str, meta):
        raise NotImplementedError("private vikingdb collection should be pre-created")

    def drop_collection(self, name: str) -> None:
        if not self._match(name):
            return
        coll = self.get_collection(name)
        if coll is None:
            return
        coll.drop()
        self._collection = None

    def list_collections(self) -> list[str]:
        path, method = VIKINGDB_APIS["ListVikingdbCollection"]
        req = {"ProjectName": self._project_name}
        response = self._client().do_req(method, path=path, req_body=req)
        if response.status_code != 200:
            return []
        result = response.json()
        raw = result.get("Result", {}).get("Collections", [])
        names = normalize_collection_names(raw)
        return [n for n in names if n == self._collection_name]

    def close(self) -> None:
        if self._collection is not None:
            self._collection.close()
            self._collection = None
