# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Remote HTTP vector backend driver."""

from __future__ import annotations

from openviking.storage.vector_store.driver import VectorStoreDriver
from openviking.storage.vector_store.drivers.common import normalize_collection_names, parse_url
from openviking.storage.vector_store.registry import register_driver
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.http_collection import (
    HttpCollection,
    get_or_create_http_collection,
    list_vikingdb_collections,
)


@register_driver("http")
class HttpVectorDriver(VectorStoreDriver):
    """Driver for remote HTTP vectordb project."""

    def __init__(self, host: str, port: int, project_name: str, collection_name: str):
        self.mode = "http"
        self._host = host
        self._port = port
        self._project_name = project_name
        self._collection_name = collection_name
        self._collection = None

    @classmethod
    def from_config(cls, config):
        if not config.url:
            raise ValueError("HTTP backend requires a valid URL")

        host, port = parse_url(config.url)
        collection_name = config.name or "context"
        project_name = config.project_name or "default"
        return cls(
            host=host,
            port=port,
            project_name=project_name,
            collection_name=collection_name,
        )

    def _match(self, name: str) -> bool:
        return name == self._collection_name

    def _meta(self) -> dict:
        return {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }

    def _remote_has_collection(self) -> bool:
        raw = list_vikingdb_collections(
            host=self._host,
            port=self._port,
            project_name=self._project_name,
        )
        names = normalize_collection_names(raw)
        return self._collection_name in names

    def _ensure_collection_handle(self) -> None:
        if self._collection is not None:
            return
        if not self._remote_has_collection():
            return
        self._collection = Collection(
            HttpCollection(
                ip=self._host,
                port=self._port,
                meta_data=self._meta(),
            )
        )

    def has_collection(self, name: str) -> bool:
        if not self._match(name):
            return False
        exists = self._remote_has_collection()
        if exists:
            self._ensure_collection_handle()
        return exists

    def get_collection(self, name: str):
        if not self._match(name):
            return None
        self._ensure_collection_handle()
        return self._collection

    def create_collection(self, name: str, meta):
        if not self._match(name):
            raise ValueError(
                f"http backend is bound to collection '{self._collection_name}', got '{name}'"
            )
        payload = dict(meta)
        payload.update(self._meta())
        self._collection = get_or_create_http_collection(
            host=self._host,
            port=self._port,
            meta_data=payload,
        )
        return self._collection

    def drop_collection(self, name: str) -> None:
        if not self._match(name):
            return
        coll = self.get_collection(name)
        if coll is None:
            return
        coll.drop()
        self._collection = None

    def list_collections(self) -> list[str]:
        return [self._collection_name] if self.has_collection(self._collection_name) else []

    def close(self) -> None:
        if self._collection is not None:
            self._collection.close()
            self._collection = None
