# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Local backend collection adapter."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.local_collection import get_or_create_local_collection

from .base import CollectionAdapter


class LocalCollectionAdapter(CollectionAdapter):
    """Adapter for local embedded vectordb backend."""

    DEFAULT_LOCAL_PROJECT_NAME = "vectordb"
    # 这里按 collection 路径做进程内共享，避免多个适配器重复打开同一个嵌入式库导致锁冲突。
    _shared_lock = threading.RLock()
    _shared_collections: dict[str, Collection] = {}
    _shared_ref_counts: dict[str, int] = {}

    def __init__(
        self,
        collection_name: str,
        project_path: str,
        enable_shared_collection_handle: bool = False,
    ):
        super().__init__(collection_name=collection_name)
        self.mode = "local"
        self._project_path = project_path
        # 该开关默认关闭，保持原有“每个适配器各自打开本地库”的行为，仅在显式配置时启用共享句柄。
        self._enable_shared_collection_handle = enable_shared_collection_handle
        # 记录当前适配器绑定的共享 key，便于 close 时安全释放引用。
        self._shared_collection_key: Optional[str] = None

    @classmethod
    def from_config(cls, config: Any):
        project_path = (
            str(Path(config.path) / cls.DEFAULT_LOCAL_PROJECT_NAME) if config.path else ""
        )
        return cls(
            collection_name=config.name or "context",
            project_path=project_path,
            enable_shared_collection_handle=getattr(
                config, "enable_shared_collection_handle", False
            ),
        )

    def _collection_path(self) -> str:
        if not self._project_path:
            return ""
        return str(Path(self._project_path) / self._collection_name)

    def _attach_shared_collection(self, shared_key: str, collection: Collection) -> Collection:
        # 这里统一维护引用计数，避免某个适配器提前 close 时把仍在使用的共享句柄关掉。
        ref_count = self._shared_ref_counts.get(shared_key, 0) + 1
        self._shared_collections[shared_key] = collection
        self._shared_ref_counts[shared_key] = ref_count
        self._shared_collection_key = shared_key
        self._collection = collection
        return collection

    def _get_or_open_shared_collection(
        self,
        shared_key: str,
        *,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Collection:
        with self._shared_lock:
            collection = self._shared_collections.get(shared_key)
            if collection is not None:
                return self._attach_shared_collection(shared_key, collection)

            if meta is None:
                collection = get_or_create_local_collection(path=shared_key)
            else:
                collection = get_or_create_local_collection(meta_data=meta, path=shared_key)
            return self._attach_shared_collection(shared_key, collection)

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        collection_path = self._collection_path()
        if not collection_path:
            return
        meta_path = os.path.join(collection_path, "collection_meta.json")
        if os.path.exists(meta_path):
            if self._enable_shared_collection_handle:
                # 开启共享句柄时，统一走进程内共享逻辑，避免重复打开同一个嵌入式库。
                self._get_or_open_shared_collection(collection_path)
            else:
                self._collection = get_or_create_local_collection(path=collection_path)

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        collection_path = self._collection_path()
        if collection_path:
            os.makedirs(collection_path, exist_ok=True)
            if self._enable_shared_collection_handle:
                return self._get_or_open_shared_collection(collection_path, meta=meta)
            return get_or_create_local_collection(meta_data=meta, path=collection_path)
        return get_or_create_local_collection(meta_data=meta, path=collection_path)

    def close(self) -> None:
        if not self._enable_shared_collection_handle:
            super().close()
            return

        shared_key = self._shared_collection_key
        if not shared_key:
            super().close()
            return

        with self._shared_lock:
            ref_count = self._shared_ref_counts.get(shared_key, 0)
            if ref_count > 1:
                self._shared_ref_counts[shared_key] = ref_count - 1
            else:
                collection = self._shared_collections.pop(shared_key, None)
                self._shared_ref_counts.pop(shared_key, None)
                if collection is not None:
                    collection.close()

        self._collection = None
        self._shared_collection_key = None
