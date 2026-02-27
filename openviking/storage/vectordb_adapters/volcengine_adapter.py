# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Volcengine backend collection adapter."""

from __future__ import annotations

from typing import Any, Dict

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.volcengine_collection import (
    VolcengineCollection,
    get_or_create_volcengine_collection,
)

from .base import CollectionAdapter


class VolcengineCollectionAdapter(CollectionAdapter):
    """Adapter for Volcengine-hosted VikingDB."""

    def __init__(
        self,
        *,
        ak: str,
        sk: str,
        region: str,
        project_name: str,
        collection_name: str,
    ):
        super().__init__(collection_name=collection_name)
        self.mode = "volcengine"
        self._ak = ak
        self._sk = sk
        self._region = region
        self._project_name = project_name

    @classmethod
    def from_config(cls, config: Any):
        if not (
            config.volcengine
            and config.volcengine.ak
            and config.volcengine.sk
            and config.volcengine.region
        ):
            raise ValueError("Volcengine backend requires AK, SK, and Region configuration")
        return cls(
            ak=config.volcengine.ak,
            sk=config.volcengine.sk,
            region=config.volcengine.region,
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
        )

    def _meta(self) -> Dict[str, Any]:
        return {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
        }

    def _config(self) -> Dict[str, Any]:
        return {
            "AK": self._ak,
            "SK": self._sk,
            "Region": self._region,
        }

    def _new_collection_handle(self) -> VolcengineCollection:
        return VolcengineCollection(
            ak=self._ak,
            sk=self._sk,
            region=self._region,
            meta_data=self._meta(),
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        candidate = self._new_collection_handle()
        meta = candidate.get_meta_data() or {}
        if meta and meta.get("CollectionName"):
            self._collection = candidate

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        payload = dict(meta)
        payload.update(self._meta())
        return get_or_create_volcengine_collection(
            config=self._config(),
            meta_data=payload,
        )

    def _sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        date_time_fields = {
            field.get("FieldName") for field in fields_meta if field.get("FieldType") == "date_time"
        }
        return [field for field in scalar_index_fields if field not in date_time_fields]

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> Dict[str, Any]:
        index_type = "hnsw_hybrid" if use_sparse else "hnsw"
        index_meta: Dict[str, Any] = {
            "IndexName": index_name,
            "VectorIndex": {
                "IndexType": index_type,
                "Distance": distance,
                "Quant": "int8",
            },
            "ScalarIndex": scalar_index_fields,
        }
        if use_sparse:
            index_meta["VectorIndex"]["EnableSparse"] = True
            index_meta["VectorIndex"]["SearchWithSparseLogitAlpha"] = sparse_weight
        return index_meta

    def _normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("uri", "parent_uri"):
            value = record.get(key)
            if isinstance(value, str) and not value.startswith("viking://"):
                stripped = value.strip("/")
                if stripped:
                    record[key] = f"viking://{stripped}"
        return record
