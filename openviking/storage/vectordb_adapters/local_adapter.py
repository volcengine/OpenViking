# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Local backend collection adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

from .base import CollectionAdapter


class LocalCollectionAdapter(CollectionAdapter):
    """Adapter for local embedded vectordb backend."""

    DEFAULT_LOCAL_PROJECT_NAME = "vectordb"

    def __init__(self, collection_name: str, project_path: str):
        super().__init__(collection_name=collection_name)
        self.mode = "local"
        self._project_path = project_path

    @classmethod
    def from_config(cls, config: Any):
        project_path = (
            str(Path(config.path) / cls.DEFAULT_LOCAL_PROJECT_NAME) if config.path else ""
        )
        return cls(collection_name=config.name or "context", project_path=project_path)

    def _collection_path(self) -> str:
        if not self._project_path:
            return ""
        return str(Path(self._project_path) / self._collection_name)

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        collection_path = self._collection_path()
        if not collection_path:
            return
        meta_path = os.path.join(collection_path, "collection_meta.json")
        if os.path.exists(meta_path):
            self._collection = get_or_create_local_collection(path=collection_path)

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        collection_path = self._collection_path()
        if collection_path:
            os.makedirs(collection_path, exist_ok=True)
        return get_or_create_local_collection(meta_data=meta, path=collection_path)
