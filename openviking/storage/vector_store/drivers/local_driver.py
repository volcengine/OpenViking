# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Local persistent vector backend driver."""

from __future__ import annotations

import os
from pathlib import Path

from openviking.storage.vector_store.driver import VectorStoreDriver
from openviking.storage.vector_store.registry import register_driver
from openviking.storage.vectordb.collection.local_collection import (
    get_or_create_local_collection,
)


@register_driver("local")
class LocalVectorDriver(VectorStoreDriver):
    """Driver for local embedded vectordb backend."""

    DEFAULT_LOCAL_PROJECT_NAME = "vectordb"

    def __init__(self, collection_name: str, collection_path: str):
        self.mode = "local"
        self._collection_name = collection_name
        self._collection_path = collection_path
        self._collection = None

    @classmethod
    def from_config(cls, config):
        collection_name = config.name or "context"
        if config.path:
            project_path = Path(config.path) / cls.DEFAULT_LOCAL_PROJECT_NAME
            collection_path = str(project_path / collection_name)
        else:
            collection_path = ""
        return cls(collection_name=collection_name, collection_path=collection_path)

    def _match(self, name: str) -> bool:
        return name == self._collection_name

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        if not self._collection_path:
            return
        meta_path = os.path.join(self._collection_path, "collection_meta.json")
        if os.path.exists(meta_path):
            self._collection = get_or_create_local_collection(path=self._collection_path)

    def has_collection(self, name: str) -> bool:
        if not self._match(name):
            return False
        self._load_existing_collection_if_needed()
        return self._collection is not None

    def get_collection(self, name: str):
        if not self._match(name):
            return None
        self._load_existing_collection_if_needed()
        return self._collection

    def create_collection(self, name: str, meta):
        if not self._match(name):
            raise ValueError(
                f"local backend is bound to collection '{self._collection_name}', got '{name}'"
            )
        if self._collection is not None:
            return self._collection
        if self._collection_path:
            os.makedirs(self._collection_path, exist_ok=True)
        self._collection = get_or_create_local_collection(
            meta_data=meta,
            path=self._collection_path,
        )
        return self._collection

    def drop_collection(self, name: str) -> None:
        if not self.has_collection(name):
            return
        assert self._collection is not None
        self._collection.drop()
        self._collection = None

    def list_collections(self) -> list[str]:
        return [self._collection_name] if self.has_collection(self._collection_name) else []

    def close(self) -> None:
        if self._collection is not None:
            self._collection.close()
            self._collection = None
