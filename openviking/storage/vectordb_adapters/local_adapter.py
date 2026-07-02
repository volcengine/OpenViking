# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local backend collection adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
from openviking_cli.utils import get_logger

from .base import CollectionAdapter

logger = get_logger(__name__)


class LocalCollectionAdapter(CollectionAdapter):
    """Adapter for local embedded vectordb backend."""

    DEFAULT_LOCAL_PROJECT_NAME = "vectordb"

    def __init__(
        self,
        collection_name: str,
        project_path: str,
        index_name: str,
        distance: str = "cosine",
        sparse_weight: float = 0.0,
    ):
        super().__init__(collection_name=collection_name, index_name=index_name)
        self.mode = "local"
        self._project_path = project_path
        self._distance = distance
        self._sparse_weight = sparse_weight

    @classmethod
    def from_config(cls, config: Any):
        project_path = (
            str(Path(config.path) / cls.DEFAULT_LOCAL_PROJECT_NAME) if config.path else ""
        )
        return cls(
            collection_name=config.name or "context",
            project_path=project_path,
            index_name=config.index_name or "default",
            distance=getattr(config, "distance_metric", None) or "cosine",
            sparse_weight=getattr(config, "sparse_weight", 0.0) or 0.0,
        )

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
            self._recover_missing_default_index()

    def _recover_missing_default_index(self) -> None:
        """Rebuild the default index from the store when it is missing on load.

        A collection can end up persisted with store content but no usable index
        (e.g. the ``index/`` directory was emptied, or an index subdir survived
        but its ``index_meta.json`` was lost/corrupted). ``_recover()`` only
        restores indexes that still have valid on-disk metadata, so such dirty
        states leave the collection with no searchable index and ``search``
        silently returns nothing. Rebuilding from the store (a full scan of the
        candidate table) makes the data searchable again.
        """
        coll = self._collection
        if coll is None or coll.has_index(self._index_name):
            return
        index_meta = self.build_default_index_meta(
            index_name=self._index_name,
            distance=self._distance,
            use_sparse=self._sparse_weight > 0.0,
            sparse_weight=self._sparse_weight,
            scalar_index_fields=[],
        )
        coll.create_index(self._index_name, index_meta)
        logger.warning(
            "Rebuilt missing index '%s' from store for local collection '%s'",
            self._index_name,
            self._collection_name,
        )

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        collection_path = self._collection_path()
        if collection_path:
            os.makedirs(collection_path, exist_ok=True)
        return get_or_create_local_collection(meta_data=meta, path=collection_path)

    def update_data(self, data_list: List[Dict[str, Any]]):
        collection = self.get_collection()
        result = collection.update_data(data_list)
        return list(result.ids or [])
