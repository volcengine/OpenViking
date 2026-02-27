# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""HTTP backend collection adapter."""

from __future__ import annotations

from typing import Any, Dict

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.http_collection import (
    HttpCollection,
    get_or_create_http_collection,
    list_vikingdb_collections,
)

from .base import CollectionAdapter, _normalize_collection_names, _parse_url


class HttpCollectionAdapter(CollectionAdapter):
    """Adapter for remote HTTP vectordb project."""

    def __init__(self, host: str, port: int, project_name: str, collection_name: str):
        super().__init__(collection_name=collection_name)
        self.mode = "http"
        self._host = host
        self._port = port
        self._project_name = project_name

    @classmethod
    def from_config(cls, config: Any):
        if not config.url:
            raise ValueError("HTTP backend requires a valid URL")
        host, port = _parse_url(config.url)
        return cls(
            host=host,
            port=port,
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
        )

    def _meta(self) -> Dict[str, Any]:
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
        return self._collection_name in _normalize_collection_names(raw)

    def _load_existing_collection_if_needed(self) -> None:
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

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        payload = dict(meta)
        payload.update(self._meta())
        return get_or_create_http_collection(
            host=self._host,
            port=self._port,
            meta_data=payload,
        )
