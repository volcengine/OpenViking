# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Qdrant backend adapter for OpenViking."""

from __future__ import annotations

import math
from typing import Any

from openviking.storage.vectordb.collection.collection import Collection
from openviking.storage.vectordb.collection.qdrant_collection import QdrantCollection
from openviking.storage.vectordb.collection.qdrant_rest import QdrantRestClient
from openviking.storage.vectordb.qdrant_utils import compile_qdrant_filter

from .base import CollectionAdapter


class QdrantCollectionAdapter(CollectionAdapter):
    """Adapter that maps OpenViking collection operations to Qdrant REST."""

    _DATA_BATCH_SIZE = 100
    USE_CONTENT_FIELD = False

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None,
        timeout_seconds: float,
        project_name: str,
        collection_name: str,
        index_name: str,
        distance_metric: str,
        dimension: int,
        sparse_weight: float,
        dense_vector_name: str,
        sparse_vector_name: str,
        metadata_collection_name: str | None,
    ) -> None:
        super().__init__(collection_name=collection_name, index_name=index_name)
        self.mode = "qdrant"
        self._client = QdrantRestClient(
            url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
        self._project_name = project_name or "default"
        self._physical_collection_name = f"{self._project_name}__{collection_name}"
        self._metadata_collection_name_override = metadata_collection_name
        self._metadata_collection_name = metadata_collection_name or (
            f"{self._physical_collection_name}__openviking_meta"
        )
        self._distance_metric = distance_metric
        self._dimension = int(dimension)
        self._sparse_weight = float(sparse_weight)
        if not math.isfinite(self._sparse_weight) or not 0.0 <= self._sparse_weight <= 1.0:
            raise ValueError("Qdrant sparse_weight must be a finite number between 0 and 1")
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name

    @classmethod
    def from_config(cls, config: Any) -> "QdrantCollectionAdapter":
        qdrant_cfg = getattr(config, "qdrant", None)
        custom = dict(getattr(config, "custom_params", {}) or {})
        url = (
            getattr(qdrant_cfg, "url", None)
            or getattr(config, "url", None)
            or custom.get("url")
        )
        if not url:
            raise ValueError("Qdrant backend requires qdrant.url or vectordb.url")
        return cls(
            url=str(url).strip().rstrip("/"),
            api_key=getattr(qdrant_cfg, "api_key", None) or custom.get("api_key"),
            timeout_seconds=float(
                getattr(qdrant_cfg, "timeout_seconds", None)
                or custom.get("timeout_seconds")
                or 10.0
            ),
            project_name=str(config.project_name or "default"),
            collection_name=str(config.name or "context"),
            index_name=str(config.index_name or "default"),
            distance_metric=str(config.distance_metric or "cosine"),
            dimension=int(config.dimension or 0),
            sparse_weight=float(config.sparse_weight or 0.0),
            dense_vector_name=str(
                getattr(qdrant_cfg, "dense_vector_name", None)
                or custom.get("dense_vector_name")
                or "vector"
            ),
            sparse_vector_name=str(
                getattr(qdrant_cfg, "sparse_vector_name", None)
                or custom.get("sparse_vector_name")
                or "sparse_vector"
            ),
            metadata_collection_name=getattr(qdrant_cfg, "metadata_collection_name", None)
            or custom.get("metadata_collection_name"),
        )

    def _new_collection(self) -> QdrantCollection:
        self._physical_collection_name = f"{self._project_name}__{self._collection_name}"
        self._metadata_collection_name = self._metadata_collection_name_override or (
            f"{self._physical_collection_name}__openviking_meta"
        )
        return QdrantCollection(
            client=self._client,
            collection_name=self._physical_collection_name,
            metadata_collection_name=self._metadata_collection_name,
            dense_vector_name=self._dense_vector_name,
            sparse_vector_name=self._sparse_vector_name,
            vector_dim=self._dimension,
            distance=self._distance_metric,
            sparse_enabled=self._sparse_weight > 0.0,
            sparse_weight=self._sparse_weight,
        )

    def _load_existing_collection_if_needed(self) -> None:
        if self._collection is not None:
            return
        candidate = self._new_collection()
        if not candidate.collection_exists():
            return
        if not candidate.has_openviking_metadata():
            candidate.close()
            raise RuntimeError(
                "Qdrant collection exists but OpenViking metadata is missing: "
                f"{self._physical_collection_name}. "
                "Use a different project/name, restore metadata, or drop the stale collection."
            )
        candidate.get_meta_data()
        self._collection = Collection(candidate)

    def _create_backend_collection(self, meta: dict[str, Any]) -> Collection:
        candidate = self._new_collection()
        candidate.create_remote_collection(meta)
        return Collection(candidate)

    def _sanitize_scalar_index_fields(
        self,
        scalar_index_fields: list[str],
        fields_meta: list[dict[str, Any]],
    ) -> list[str]:
        del fields_meta
        return list(dict.fromkeys([*scalar_index_fields, "uri_depth", "scope_roots"]))

    def _build_default_index_meta(
        self,
        *,
        index_name: str,
        distance: str,
        use_sparse: bool,
        sparse_weight: float,
        scalar_index_fields: list[str],
    ) -> dict[str, Any]:
        return {
            "IndexName": index_name,
            "VectorIndex": {
                "IndexType": "hnsw_hybrid" if use_sparse else "hnsw",
                "Distance": distance,
            },
            "ScalarIndex": scalar_index_fields,
            "SparseWeight": sparse_weight,
        }

    def _compile_filter(self, expr: Any) -> dict[str, Any]:
        return compile_qdrant_filter(expr)

    def update_data(self, data_list: list[dict[str, Any]]) -> list[str]:
        result = self.get_collection().update_data(data_list)
        return [str(item) for item in result]


__all__ = ["QdrantCollectionAdapter"]
