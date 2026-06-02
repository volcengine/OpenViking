# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local backend collection adapter."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection
from openviking.storage.vectordb.utils import validation

from .base import CollectionAdapter


class LocalCollectionAdapter(CollectionAdapter):
    """Adapter for local embedded vectordb backend."""

    DEFAULT_LOCAL_PROJECT_NAME = "vectordb"

    def __init__(self, collection_name: str, project_path: str, index_name: str):
        super().__init__(collection_name=collection_name, index_name=index_name)
        self.mode = "local"
        self._project_path = project_path

    @classmethod
    def from_config(cls, config: Any):
        project_path = (
            str(Path(config.path) / cls.DEFAULT_LOCAL_PROJECT_NAME) if config.path else ""
        )
        return cls(
            collection_name=config.name or "context",
            project_path=project_path,
            index_name=config.index_name or "default",
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

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        collection_path = self._collection_path()
        if collection_path:
            os.makedirs(collection_path, exist_ok=True)
        return get_or_create_local_collection(meta_data=meta, path=collection_path)

    def scan_all(self) -> list[Dict[str, Any]]:
        coll = self.get_collection()
        local_collection = getattr(coll, "_Collection__collection", None)
        store_mgr = getattr(local_collection, "store_mgr", None)
        meta = getattr(local_collection, "meta", None)
        if store_mgr is None or meta is None:
            return super().scan_all()

        records: list[Dict[str, Any]] = []
        vector_key = meta.vector_key
        sparse_vector_key = meta.sparse_vector_key
        for candidate in store_mgr.get_all_cands_data():
            record = json.loads(candidate.fields)
            if vector_key:
                record[vector_key] = list(candidate.vector)
            if sparse_vector_key and candidate.sparse_raw_terms and candidate.sparse_values:
                record[sparse_vector_key] = dict(
                    zip(candidate.sparse_raw_terms, candidate.sparse_values, strict=False)
                )
            record = validation.fix_fields_data(record, meta.fields_dict)
            if meta.primary_key:
                record["id"] = record.get(meta.primary_key)
            records.append(self._normalize_record_for_read(record))
        return records
