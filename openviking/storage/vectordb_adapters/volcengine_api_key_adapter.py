# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Volcengine API key backend collection adapter."""

from __future__ import annotations

from typing import Any, Dict

from openviking.storage.expr import PathScope
from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
    VolcengineApiKeyCollection,
)

from .base import CollectionAdapter


class VolcengineApiKeyCollectionAdapter(CollectionAdapter):
    """Adapter for Volcengine-hosted VikingDB data-plane access via API key."""

    def __init__(
        self,
        *,
        api_key: str,
        host: str,
        project_name: str,
        collection_name: str,
        index_name: str,
    ):
        super().__init__(collection_name=collection_name, index_name=index_name)
        self._collection: Collection | None = None
        self.mode = "volcengine_api_key"
        self._api_key = api_key
        self._host = host
        self._project_name = project_name

    @classmethod
    def from_config(cls, config: Any):
        cfg = getattr(config, "volcengine_api_key", None)
        if not cfg or not cfg.api_key or not cfg.host:
            raise ValueError(
                "Volcengine API key backend requires api_key and host configuration"
            )

        return cls(
            api_key=cfg.api_key,
            host=cfg.host,
            project_name=config.project_name or "default",
            collection_name=config.name or "context",
            index_name=config.index_name or "default",
        )

    def _meta(self) -> Dict[str, Any]:
        return {
            "ProjectName": self._project_name,
            "CollectionName": self._collection_name,
            "IndexName": self._index_name,
        }

    def _new_collection_handle(self) -> Collection:
        return Collection(
            VolcengineApiKeyCollection(
                api_key=self._api_key,
                host=self._host,
                meta_data=self._meta(),
            )
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        self._collection = self._new_collection_handle()

    def _create_backend_collection(self, meta: Dict[str, Any]) -> Collection:
        raise NotImplementedError(
            "volcengine_api_key backend does not support create_collection; "
            "pre-create collection/index/schema out of band"
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

    def _compile_filter(self, expr):
        if isinstance(expr, PathScope):
            path = (
                self._encode_uri_field_value(expr.path)
                if expr.field in self._URI_FIELD_NAMES
                else expr.path
            )
            return {"op": "prefix", "field": expr.field, "prefix": path}
        return super()._compile_filter(expr)

    def _normalize_record_for_read(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return super()._normalize_record_for_read(record)
